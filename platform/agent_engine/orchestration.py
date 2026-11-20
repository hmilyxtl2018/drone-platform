"""Fixed Agent handoffs with immutable artifacts and one shared budget.

The coordinator schedules configured agents; it does not execute model-provided
agent names or grant additional provider/tool permissions. Queue submission is
outside the transaction and can safely be retried using the persisted child ID.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import re

from psycopg2.extras import Json

from .repository import PostgresRepository, as_json, new_id
from .execution_baseline import build_profile_baseline, register_baseline


HANDOFF_GRAPH = {
    "uav-capability-analyst": frozenset({"uav-design-engineer", "uav-evidence-reviewer"}),
    "uav-design-engineer": frozenset({"uav-evidence-reviewer"}),
    "uav-evidence-reviewer": frozenset(),
}
HANDOFF_TASK_FIELDS = frozenset({
    'entityId', 'uavId', 'question', 'requirements', 'analysisPurpose', 'analysisContext',
    'selectedObject', 'selectionReason', 'objectClass', 'category', 'budgetLimits',
    'executionMode', 'automationMode', 'collaborate', 'includeDesign', 'workspaceId', 'workspaceRevision',
    'workspaceRevisionHash', 'supplementalMaterial',
})
DELIVERABLE_STATUSES = frozenset({"completed", "completed-with-gaps", "requires-human-review"})
ACTION_STATUSES = frozenset({"completed", "completed-with-gaps", "requires-human-review"})
HANDOFF_PROJECTION_SCHEMA = "handoff-projection.v1"


def validate_handoff(source_agent, target_agent, *, cursor=None, parent_version=None):
    if cursor is not None:
        try:
            cursor.execute("SELECT to_regclass(current_schema() || '.agent_composition_edge')")
            row=cursor.fetchone()
            installed=next(iter(row.values())) if isinstance(row, dict) else row[0]
            if installed:
                cursor.execute("""SELECT edge_id,parent_agent_key,child_agent_key,parent_profile_version,
                                        child_profile_version,handoff_schema_json,allowed_artifact_types_json,
                                        budget_mode,max_depth,approval_required,enabled
                                   FROM agent_composition_edge
                                  WHERE parent_agent_key=%s AND child_agent_key=%s
                                    AND enabled=true
                                    AND (%s IS NULL OR parent_profile_version=%s)
                                  LIMIT 1""", (source_agent, target_agent, parent_version, parent_version))
                edge=cursor.fetchone()
                if edge:
                    return dict(edge)
                # A present composition table is authoritative: do not fall back
                # to a stale in-code graph when an edge was explicitly denied.
                raise PermissionError("handoff is outside the configured Agent dependency graph")
        except PermissionError: raise
    if target_agent not in HANDOFF_GRAPH.get(source_agent, ()):
        raise PermissionError("handoff is outside the configured Agent dependency graph")
    return None


def _hash(content):
    # Match existing agent_run_artifact hashing exactly, including its spacing.
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def _optional_ceiling(*values):
    """NULL means no extra cap; an explicit zero remains a deny ceiling."""
    specified = [int(value) for value in values if value is not None]
    return min(specified) if specified else None


def constrained_child_profile(upstream, downstream, limits, parent_step_key=None):
    """Preserve the child's role while restricting execution to the parent grant."""
    model = deepcopy(upstream.model_policy)
    config = model.setdefault('config_json', {})
    own_config = downstream.model_policy.get('config_json', {})
    for key in ('researchSteps', 'researchOutputContract', 'dataContractVersion', 'researchMethodsVersion',
                'decisionSemanticsVersion'):
        if key in own_config:
            config[key] = deepcopy(own_config[key])
        else:
            config.pop(key, None)
    # Preserve the reviewed execution layer.  Without this explicit carryover
    # an Action child would inherit the application Profile's config and be
    # persisted as an independent application Run.
    for key in ('agentLayer', 'actionExecutor', 'runtimeKind'):
        if key in own_config:
            config[key] = deepcopy(own_config[key])
    # A resumed legacy parent must keep its original reasoning ownership.
    config.pop('actionReasoningVersion', None)
    if (upstream.model_policy.get('config_json', {}).get('actionReasoningVersion') == 'child-v1'
            and own_config.get('actionReasoningVersion') == 'child-v1'):
        config['actionReasoningVersion'] = 'child-v1'
    config['executionBaseline'] = deepcopy(own_config.get('executionBaseline', {}))
    # Capability graphs are owned by the application Profile.  An Action child
    # receives the reviewed edge and its own tools, but must not inherit the
    # parent's graph path (which would fail ownership validation or expose
    # application capabilities inside the child snapshot).
    config.pop('capabilityGraphPath', None)
    config['capabilityActions'] = deepcopy(own_config.get('capabilityActions', []))
    if own_config.get('agentLayer') == 'action':
        # Deterministic Action Agents execute their fixed Plan.  They do not
        # enter the model-driven research loop merely because the parent is a
        # research-capable application.
        config.pop('engineMode', None)
        config['researchModeAvailable'] = False
        from .cognitive_identity import AGENT_KEYS as COGNITIVE_KEYS
        if downstream.agent_key in COGNITIVE_KEYS:
            config['engineMode'] = 'cognitive-suite-v1'
            config['cognitiveSuiteVersion'] = '1.0.0'
        else:
            config.pop('cognitiveSuiteVersion', None)
    else:
        config['researchModeAvailable'] = bool(own_config.get('researchModeAvailable',
                                                               config.get('researchModeAvailable', False)))
        config['engineMode'] = 'research-v1'
    if 'prompt_version' in downstream.model_policy:
        model['prompt_version'] = downstream.model_policy['prompt_version']
    if 'max_output_tokens' in model and 'max_output_tokens' in downstream.model_policy:
        model['max_output_tokens'] = min(model['max_output_tokens'], downstream.model_policy['max_output_tokens'])
    from .cognitive_identity import APPLICATION_KEY
    if upstream.agent_key == APPLICATION_KEY and parent_step_key:
        parent_node = next((node for node in upstream.execution_baseline.get('graph', {}).get('nodes', [])
                            if node['id'] == parent_step_key), {})
        model['max_output_tokens'] = min(model.get('max_output_tokens', 2048),
                                         int(parent_node.get('budget', {}).get('output_tokens', 0)))
    resources = deepcopy(upstream.resource_policy)
    resources['budgetLimits'] = deepcopy(limits)
    from .data_contracts import VERSION
    if own_config.get('dataContractVersion') == VERSION:
        own_limits = downstream.resource_policy.get('budgetLimits') or own_config.get('budgetLimits', {})
        # An omitted child cap preserves the authorized parent ceiling. It is
        # distinct from an explicit zero, which forbids that resource.
        effective_limits = {key: min(value, own_limits.get(key, value)) for key, value in limits.items()}
        config['budgetLimits'] = deepcopy(effective_limits)
        resources['budgetLimits'] = effective_limits
    for key in ('max_parallel_tools', 'max_retries_per_tool'):
        if key in downstream.resource_policy and key in resources:
            resources[key] = min(resources[key], downstream.resource_policy[key])
    risk_key = 'permitted_risk_levels_json'
    if risk_key in resources and risk_key in downstream.resource_policy:
        resources[risk_key] = sorted(set(as_json(resources[risk_key])) & set(as_json(downstream.resource_policy[risk_key])))
    tools = {}
    for key, parent in upstream.tools.items():
        child = downstream.tools.get(key)
        if child is None:
            continue
        # A new endpoint/version/argument contract needs a new root Run. A
        # downstream edit must not silently obtain a different implementation.
        same = all(getattr(parent, field) == getattr(child, field) for field in (
            'version', 'protocol', 'endpoint', 'input_schema', 'output_schema',
            'config', 'access_mode', 'risk_level'))
        tools[key] = replace(parent, enabled=parent.enabled and child.enabled and same,
                             approval_required=parent.approval_required or child.approval_required,
                             timeout_seconds=min(parent.timeout_seconds, child.timeout_seconds),
                             retry_limit=min(parent.retry_limit, child.retry_limit))
    scope = deepcopy(downstream.scope_policy)
    for key in ('allowedEntities', 'allowedMetrics'):
        if key in upstream.scope_policy:
            allowed = set(upstream.scope_policy[key])
            if key in scope:
                allowed.intersection_update(scope[key])
            scope[key] = sorted(allowed)
    # The downstream Profile remains responsible for its own prompt and output
    # contract, while the parent's Harness grant is an upper bound. This keeps
    # a child from gaining a tool, data source, network domain, or larger node
    # budget merely by changing its own Profile.
    harness = deepcopy(getattr(downstream, 'harness_contract', {}) or {})
    parent_harness = deepcopy(getattr(upstream, 'harness_contract', {}) or {})
    if harness and parent_harness:
        parent_tools = {(g.get('tool_key'), g.get('tool_version')): g
                        for g in parent_harness.get('toolGrants', [])}
        harness['toolGrants'] = [deepcopy(g) for g in harness.get('toolGrants', [])
                                 if (g.get('tool_key'), g.get('tool_version')) in parent_tools]
        parent_data = {(g.get('resource_type'), g.get('resource_pattern'), g.get('access_mode'))
                       for g in parent_harness.get('dataGrants', [])}
        harness['dataGrants'] = [deepcopy(g) for g in harness.get('dataGrants', [])
                                 if (g.get('resource_type'), g.get('resource_pattern'), g.get('access_mode')) in parent_data]
        parent_network = {g.get('domain_pattern'): g for g in parent_harness.get('networkGrants', [])}
        networks = []
        for grant in harness.get('networkGrants', []):
            upper = parent_network.get(grant.get('domain_pattern'))
            if not upper:
                continue
            item = deepcopy(grant)
            item['methods'] = sorted(set(grant.get('methods', [])) & set(upper.get('methods', [])))
            item['approvalRequired'] = bool(grant.get('approvalRequired') or upper.get('approvalRequired'))
            item['max_response_bytes'] = _optional_ceiling(grant.get('max_response_bytes'), upper.get('max_response_bytes'))
            if item['methods']:
                networks.append(item)
        harness['networkGrants'] = networks
        parent_budgets = {(b.get('step_key')): b for b in parent_harness.get('stepBudgets', [])}
        parent_node = next((n for n in (getattr(upstream, 'execution_baseline', {}) or {}).get('graph', {}).get('nodes', [])
                            if n.get('id') == parent_step_key), {})
        bounded = []
        for budget in harness.get('stepBudgets', []):
            upper = (parent_node.get('budget') or parent_budgets.get(parent_step_key)) if parent_step_key else parent_budgets.get(budget.get('step_key'))
            if not upper:
                if parent_step_key:
                    raise ValueError('parent Action node has no frozen budget ceiling')
                continue
            item = deepcopy(budget)
            for key in ('max_rounds', 'max_iterations', 'max_retries', 'model_calls', 'tool_calls', 'http_calls',
                        'search_calls', 'scrape_calls', 'input_tokens', 'output_tokens',
                        'max_parallelism', 'max_concurrency', 'max_runtime_seconds',
                        'max_evidence_bytes', 'estimated_cost_micros'):
                if key in upper and key in item:
                    item[key] = _optional_ceiling(item[key], upper[key])
            bounded.append(item)
        harness['stepBudgets'] = bounded
        harness['effectiveParentGrantHash'] = _hash({
            'tools': harness['toolGrants'], 'data': harness['dataGrants'],
            'network': harness['networkGrants'], 'budgets': harness['stepBudgets']})
    return replace(downstream, model_policy=model, tools=tools,
                   source_policy=deepcopy(upstream.source_policy),
                   resource_policy=resources, scope_policy=scope,
                   harness_contract=harness,
                   # The child executes its own fixed Action Plan.  Keeping
                   # the parent's baseline here would re-run application
                   # nodes inside the child and would invalidate the edge.
                   execution_baseline=deepcopy(downstream.execution_baseline))


class HandoffRepository:
    def __init__(self, repository):
        self.repo = repository

    @staticmethod
    def _result(row, created=False):
        return {"handoffId": row["handoff_id"], "parentRunId": row["run_id"],
                "childRunId": row["child_run_id"], "rootRunId": row["root_run_id"],
                "targetAgentKey": row["target_agent_key"], "artifacts": row["artifacts_json"],
                "projectionSchemaVersion": row.get("projection_schema_version"),
                "projection": row.get("projection_json") or {},
                "projectionHash": row.get("projection_hash"),
                "created": created}

    def create_child(self, parent_run_id: str, agent_key: str, artifact_ids: list[str],
                     idempotency_key: str) -> dict:
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", idempotency_key):
            raise ValueError("handoff requires a stable operation key")
        if not isinstance(artifact_ids, list) or not 1 <= len(artifact_ids) <= 32 or \
                any(not isinstance(item, str) or not item for item in artifact_ids):
            raise ValueError("handoff requires between one and 32 artifact IDs")
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("handoff artifact IDs must be unique")
        artifact_ids = sorted(artifact_ids)
        with self.repo.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock_shared(hashtextextended('agent-admin-profile-config',0))")
                # Parent serialization makes both operation-key and identical
                # artifact-set retries deterministic, including two API workers.
                cursor.execute("SELECT * FROM agent_run WHERE run_id=%s FOR UPDATE", (parent_run_id,))
                parent = cursor.fetchone()
                if not parent:
                    raise ValueError("parent run not found")
                edge = validate_handoff(parent["agent_key"], agent_key, cursor=cursor,
                                        parent_version=parent.get("agent_version"))
                cursor.execute("SELECT * FROM agent_run_handoff WHERE run_id=%s AND idempotency_key=%s",
                               (parent_run_id, idempotency_key))
                previous = cursor.fetchone()
                if previous:
                    saved_ids = sorted(item["artifactId"] for item in previous["artifacts_json"])
                    if previous["target_agent_key"] != agent_key or saved_ids != artifact_ids:
                        raise ValueError("handoff operation key already identifies different work")
                    return self._result(previous)
                if parent["status"] not in DELIVERABLE_STATUSES:
                    raise ValueError("parent has no deliverable terminal output")
                cursor.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s", (parent_run_id,))
                membership = cursor.fetchone()
                if not membership:
                    raise ValueError("parent shared budget must be initialized")
                root_id = membership["root_run_id"]
                cursor.execute("SELECT limits_json FROM agent_run_budget WHERE root_run_id=%s", (root_id,))
                limits = dict(cursor.fetchone()["limits_json"])
                cursor.execute("SELECT artifact_id,artifact_type,content_json,content_hash FROM agent_run_artifact "
                               "WHERE run_id=%s AND artifact_id=ANY(%s) ORDER BY artifact_id",
                               (parent_run_id, artifact_ids))
                artifacts = [dict(row) for row in cursor.fetchall()]
                if len(artifacts) != len(artifact_ids):
                    raise ValueError("handoff artifact is missing or belongs to a different Run")
                if not any(item["artifact_type"] == "agent-output" for item in artifacts):
                    raise ValueError("handoff requires the upstream Agent output artifact")
                refs, inputs = [], []
                for artifact in artifacts:
                    content = as_json(artifact["content_json"])
                    if _hash(content) != artifact["content_hash"]:
                        raise ValueError("upstream artifact hash mismatch")
                    ref = {"artifactId": artifact["artifact_id"], "contentHash": artifact["content_hash"],
                           "artifactType": artifact["artifact_type"]}
                    refs.append(ref)
                    inputs.append({**ref, "runId": parent_run_id, "content": deepcopy(content)})
                artifacts_hash = _hash(refs)
                cursor.execute("SELECT * FROM agent_run_handoff WHERE run_id=%s AND target_agent_key=%s "
                               "AND artifacts_hash=%s", (parent_run_id, agent_key, artifacts_hash))
                same = cursor.fetchone()
                if same:
                    return self._result(same)

                upstream = self.repo.load_profile_snapshot(parent_run_id)
                downstream = self.repo.load_profile(agent_key)
                child_profile = constrained_child_profile(upstream, downstream, limits)
                # The effective child Profile is a constrained projection of
                # the upstream grant.  Its executable graph can therefore
                # differ from the target Agent's published baseline (for
                # example when a parent has a narrower tool grant).  Register
                # that exact graph in the same transaction before creating the
                # Run; create_run deliberately rejects unregistered baselines.
                child_graph = build_profile_baseline(child_profile)
                child_baseline = register_baseline(cursor, agent_key, child_graph,
                                                   status='testing', created_by='handoff')
                child_profile = replace(child_profile, execution_baseline=child_baseline)
                parent_input = as_json(parent["input_json"])
                # Handoff is a role projection.  It carries task configuration
                # and approved artifact references, never the parent's private
                # state, transient planner material or arbitrary input keys.
                child_input = {key: deepcopy(parent_input[key]) for key in HANDOFF_TASK_FIELDS
                               if key in parent_input}
                handoff_id = new_id("handoff")
                projection = {
                    "schemaVersion": HANDOFF_PROJECTION_SCHEMA,
                    "taskFields": sorted(child_input),
                    "task": deepcopy(child_input),
                    "artifactRefs": deepcopy(refs),
                    "sourceAgentKey": parent["agent_key"],
                    "targetAgentKey": agent_key,
                }
                projection_hash = _hash(projection)
                child_input.update({"executionMode": "research", "parentRunId": parent_run_id,
                                    "rootRunId": root_id, "handoffId": handoff_id,
                                    "upstreamArtifacts": inputs,
                                    "upstreamAgentKey": parent["agent_key"],
                                    "upstreamStatus": parent["status"]})
                # No independent budget is created: even grandchildren join the
                # original budget root, atomically with their frozen snapshot.
                from .run_origin import origin_context
                with origin_context(source_kind='handoff', parent_run_id=parent_run_id):
                    child_id = PostgresRepository.create_run(self.repo, child_profile, child_input, _cursor=cursor)
                cursor.execute("INSERT INTO agent_run_budget_member(run_id,root_run_id,parent_run_id) VALUES(%s,%s,%s)",
                               (child_id, root_id, parent_run_id))
                budget_grant = {
                    "mode": (edge or {}).get("budget_mode", "shared"),
                    "rootRunId": root_id,
                    "parentRunId": parent_run_id,
                    "limits": deepcopy(limits),
                    "maxDepth": (edge or {}).get("max_depth", 1),
                    "approvalRequired": bool((edge or {}).get("approval_required", False)),
                }
                grant_hash = _hash(budget_grant)
                edge_id = (edge or {}).get("edge_id")
                # 053 adds immutable grant evidence. Keep the legacy insert path
                # for old schemas used by unit fixtures and historical Runs.
                cursor.execute("""SELECT 1 FROM information_schema.columns
                                  WHERE table_schema=current_schema()
                                    AND table_name='agent_run_handoff'
                                    AND column_name='composition_edge_id'""")
                audit_columns = bool(cursor.fetchone())
                cursor.execute("""SELECT 1 FROM information_schema.columns
                                  WHERE table_schema=current_schema()
                                    AND table_name='agent_run_handoff'
                                    AND column_name IN ('projection_schema_version','projection_json','projection_hash')""")
                projection_columns = len(cursor.fetchall()) == 3
                if audit_columns and projection_columns:
                    cursor.execute("INSERT INTO agent_run_handoff(handoff_id,run_id,child_run_id,root_run_id,"
                                   "target_agent_key,idempotency_key,artifacts_json,artifacts_hash,composition_edge_id,"
                                   "budget_grant_json,grant_hash,projection_schema_version,projection_json,projection_hash) "
                                   "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                                   (handoff_id, parent_run_id, child_id, root_id, agent_key,
                                    idempotency_key, Json(refs), artifacts_hash, edge_id,
                                    Json(budget_grant), grant_hash, HANDOFF_PROJECTION_SCHEMA,
                                    Json(projection), projection_hash))
                elif audit_columns:
                    cursor.execute("INSERT INTO agent_run_handoff(handoff_id,run_id,child_run_id,root_run_id,"
                                   "target_agent_key,idempotency_key,artifacts_json,artifacts_hash,composition_edge_id,"
                                   "budget_grant_json,grant_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                                   (handoff_id, parent_run_id, child_id, root_id, agent_key,
                                    idempotency_key, Json(refs), artifacts_hash, edge_id,
                                    Json(budget_grant), grant_hash))
                else:
                    cursor.execute("INSERT INTO agent_run_handoff(handoff_id,run_id,child_run_id,root_run_id,"
                                   "target_agent_key,idempotency_key,artifacts_json,artifacts_hash) "
                                   "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                                   (handoff_id, parent_run_id, child_id, root_id, agent_key,
                                    idempotency_key, Json(refs), artifacts_hash))
                row = dict(cursor.fetchone())
                # This parent event also fences the full transaction against a
                # stale queue worker; child writes alone have no queue entry yet.
                self.repo.event(cursor, parent_run_id, "agent.handoff", None,
                                {"handoffId": handoff_id, "childRunId": child_id,
                                 "targetAgentKey": agent_key, "artifactsHash": artifacts_hash})
                self.repo.event(cursor, child_id, "budget.shared", None,
                                {"rootRunId": root_id, "parentRunId": parent_run_id})
                return self._result(row, created=True)

    def list_children(self, parent_run_id):
        with self.repo.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_run_handoff WHERE run_id=%s ORDER BY created_at,handoff_id",
                               (parent_run_id,))
                return [self._result(dict(row)) for row in cursor.fetchall()]


class ActionInvocationRepository:
    """Persist one application-node -> Action-Agent invocation.

    Handoffs are intentionally terminal-output delivery records and therefore
    require a completed parent.  An Action invocation is different: it is a
    bounded child run created while its application parent is executing.  The
    record below keeps the binding, input hash, output hash and shared budget
    grant together so a capability declaration cannot be mistaken for an
    executed Action.
    """

    def __init__(self, repository):
        self.repo = repository

    @staticmethod
    def _hash(value):
        return _hash(value)

    @staticmethod
    def _row(row):
        return {"invocationId": row["invocation_id"], "parentRunId": row["run_id"],
                "parentStepRunId": row["parent_step_run_id"], "parentStepKey": row["parent_step_key"],
                "childRunId": row["child_run_id"], "compositionEdgeId": row["composition_edge_id"],
                "idempotencyKey": row["idempotency_key"], "inputArtifactId": row["input_artifact_id"],
                "inputArtifactHash": row["input_artifact_hash"], "outputArtifactId": row.get("output_artifact_id"),
                "outputArtifactHash": row.get("output_artifact_hash"),
                "budgetGrant": row.get("budget_grant_json") or {}, "grantHash": row.get("grant_hash"),
                "status": row.get("status", "planned"), "created": False}

    def create(self, parent_run_id: str, parent_step_run_id: str, parent_step_key: str,
               child_agent_key: str, composition_edge_id: str, input_artifact_id: str,
               input_artifact_hash: str, input_payload: dict, idempotency_key: str) -> dict:
        if not all(isinstance(value, str) and value for value in (
                parent_run_id, parent_step_run_id, parent_step_key, child_agent_key,
                composition_edge_id, input_artifact_id, input_artifact_hash, idempotency_key)):
            raise ValueError("Action invocation identifiers are required")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", idempotency_key):
            raise ValueError("Action invocation requires a stable operation key")
        if not isinstance(input_payload, dict):
            raise ValueError("Action invocation input must be an object")
        with self.repo.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_run WHERE run_id=%s FOR UPDATE", (parent_run_id,))
                parent = cursor.fetchone()
                if not parent:
                    raise ValueError("parent run not found")
                cursor.execute("SELECT * FROM agent_action_invocation WHERE run_id=%s AND idempotency_key=%s",
                               (parent_run_id, idempotency_key))
                previous = cursor.fetchone()
                if previous:
                    if previous["child_run_id"] and previous["composition_edge_id"] != composition_edge_id:
                        raise ValueError("Action invocation key already identifies different work")
                    return self._row(dict(previous))
                upstream = self.repo.load_profile_snapshot(parent_run_id)
                frozen_edges = (upstream.harness_contract or {}).get('compositionEdges')
                if frozen_edges is not None:
                    edge = next((item for item in frozen_edges if item.get('edge_id') == composition_edge_id
                        and item.get('parent_agent_key') == parent['agent_key']
                        and item.get('child_agent_key') == child_agent_key and item.get('enabled')), None)
                else:
                    cursor.execute("""SELECT edge_id,budget_mode,max_depth,approval_required,enabled
                                   FROM agent_composition_edge
                                  WHERE edge_id=%s AND parent_agent_key=%s AND child_agent_key=%s
                                    AND parent_profile_version=%s AND enabled=true""",
                                   (composition_edge_id, parent["agent_key"], child_agent_key,
                                    parent["agent_version"]))
                    edge = cursor.fetchone()
                if not edge:
                    raise PermissionError("Action invocation is outside the configured composition graph")
                cursor.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s", (parent_run_id,))
                member = cursor.fetchone()
                if not member:
                    raise ValueError("parent shared budget must be initialized")
                root_id = member["root_run_id"]
                cursor.execute("SELECT limits_json FROM agent_run_budget WHERE root_run_id=%s", (root_id,))
                budget_row = cursor.fetchone()
                if not budget_row:
                    raise ValueError("shared budget root is missing")
                limits = deepcopy(budget_row["limits_json"])

                upstream = self.repo.load_profile_snapshot(parent_run_id)
                downstream = self.repo.load_profile(child_agent_key)
                child_profile = constrained_child_profile(upstream, downstream, limits, parent_step_key)
                child_graph = build_profile_baseline(child_profile)
                child_baseline = register_baseline(cursor, child_agent_key, child_graph,
                                                   status='testing', created_by='action-invocation')
                child_profile = replace(child_profile, execution_baseline=child_baseline)
                child_input = deepcopy(input_payload)
                child_input.update({"executionMode": "fixed", "parentRunId": parent_run_id,
                                    "rootRunId": root_id, "parentStepRunId": parent_step_run_id,
                                    "parentStepKey": parent_step_key, "compositionEdgeId": composition_edge_id,
                                    "inputArtifactId": input_artifact_id,
                                    "inputArtifactHash": input_artifact_hash})
                child_input['upstreamArtifacts'] = [{
                    'artifactId': input_artifact_id, 'contentHash': input_artifact_hash,
                    'artifactType': 'action-input', 'runId': parent_run_id}]
                # Phase3Repository recognizes parentRunId for Action profiles and
                # joins the child to the existing root budget instead of creating
                # a second allowance.
                from .run_origin import origin_context
                with origin_context(source_kind='delegation', parent_run_id=parent_run_id):
                    child_id = self.repo.create_run(child_profile, child_input, _cursor=cursor)
                grant = {"mode": edge["budget_mode"], "rootRunId": root_id,
                         "parentRunId": parent_run_id, "compositionEdgeId": composition_edge_id,
                         "limits": limits, "maxDepth": edge["max_depth"],
                         "approvalRequired": bool(edge["approval_required"])}
                grant_hash = self._hash(grant)
                invocation_id = new_id("action-invocation")
                cursor.execute("""INSERT INTO agent_action_invocation(
                    invocation_id,run_id,parent_step_run_id,parent_step_key,child_run_id,
                    composition_edge_id,idempotency_key,input_artifact_id,input_artifact_hash,
                    budget_grant_json,grant_hash,status)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running') RETURNING *""",
                    (invocation_id, parent_run_id, parent_step_run_id, parent_step_key, child_id,
                     composition_edge_id, idempotency_key, input_artifact_id, input_artifact_hash,
                     Json(grant), grant_hash))
                row = dict(cursor.fetchone())
                self.repo.event(cursor, parent_run_id, "action.invocation.created", parent_step_key,
                                {"invocationId": invocation_id, "childRunId": child_id,
                                 "compositionEdgeId": composition_edge_id,
                                 "inputArtifactHash": input_artifact_hash, "grantHash": grant_hash})
                self.repo.event(cursor, child_id, "budget.shared", parent_step_key,
                                {"rootRunId": root_id, "parentRunId": parent_run_id,
                                 "compositionEdgeId": composition_edge_id})
                result = self._row(row)
                result["created"] = True
                return result

    def complete(self, invocation_id: str, parent_run_id: str, child_run_id: str,
                 status: str) -> dict:
        if status not in ACTION_STATUSES | {"blocked", "failed", "cancelled"}:
            raise ValueError("invalid Action invocation status")
        with self.repo.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_action_invocation WHERE invocation_id=%s FOR UPDATE",
                               (invocation_id,))
                row = cursor.fetchone()
                if not row or row["run_id"] != parent_run_id or row["child_run_id"] != child_run_id:
                    raise ValueError("Action invocation does not belong to the parent")
                artifacts = self.repo.list_artifacts(child_run_id)
                output = next((item for item in artifacts if item["artifact_type"] == "agent-output"), None)
                output_id = output["artifact_id"] if output else None
                output_hash = output["content_hash"] if output else None
                cursor.execute("""UPDATE agent_action_invocation
                                   SET status=%s,output_artifact_id=%s,output_artifact_hash=%s,updated_at=now()
                                 WHERE invocation_id=%s RETURNING *""",
                               (status, output_id, output_hash, invocation_id))
                saved = dict(cursor.fetchone())
                self.repo.event(cursor, parent_run_id, "action.invocation.completed", row["parent_step_key"],
                                {"invocationId": invocation_id, "childRunId": child_run_id,
                                 "status": status, "outputArtifactHash": output_hash})
                return self._row(saved)


class AgentOrchestrator:
    """Deterministic continuation hook; enqueue can be retried after restart."""
    def __init__(self, handoffs: HandoffRepository, enqueue):
        self.handoffs, self.enqueue = handoffs, enqueue

    def after_delivery(self, parent_run_id: str, artifact_ids: list[str], *, include_design: bool = False):
        parent = self.handoffs.repo.get_run(parent_run_id)
        if not parent or parent["status"] not in DELIVERABLE_STATUSES:
            raise ValueError("parent has no deliverable terminal output")
        if parent["agent_key"] == "uav-evidence-reviewer":
            return []
        target = "uav-design-engineer" if parent["agent_key"] == "uav-capability-analyst" and include_design \
                 else "uav-evidence-reviewer"
        result = self.handoffs.create_child(parent_run_id, target, artifact_ids,
                                            "continue:"+target+":"+_hash(sorted(artifact_ids)))
        self.enqueue(result["childRunId"])
        return [result]
