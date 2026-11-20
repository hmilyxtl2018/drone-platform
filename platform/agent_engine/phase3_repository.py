"""PostgreSQL provider, shared-budget, model-audit and plan persistence.

This repository never changes provider/source configuration. Limits are frozen
once initialized. All reservations and settlements serialize on the root row,
including requests from child runs. Missing usage remains charged conservatively.
"""
from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import replace
from typing import Any

from psycopg2.extras import Json

from .repository import PostgresRepository, new_id

RESOURCES = frozenset({"model_calls", "input_tokens", "output_tokens", "tool_calls",
                       "http_calls", "search_calls", "scrape_calls",
                       "reasoning_rounds", "gate_checks", "relation_reviews"})
TOKEN_RESOURCES = frozenset({"input_tokens", "output_tokens"})
MAX_COUNTER = 2**63 - 1


class BudgetExceededError(RuntimeError):
    """A request was rejected before external execution."""


def _counters(value: dict[str, int], *, allow_empty: bool = True) -> dict[str, int]:
    if not isinstance(value, dict) or (not value and not allow_empty):
        raise ValueError("budget counters must be a nonempty mapping" if not allow_empty
                         else "budget counters must be a mapping")
    for key, count in value.items():
        if key not in RESOURCES:
            raise ValueError(f"unknown budget resource: {key}")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= MAX_COUNTER:
            raise ValueError(f"budget counter must be a nonnegative integer: {key}")
    return dict(value)


class RunBudgetAdmissionError(PermissionError):
    """Safe, structured budget diagnostics for callers before a Run exists."""

    def __init__(self, code, message, issues):
        super().__init__(message)
        self.admission_details = {"reasonCode": code, "message": message,
                                  "issues": issues, "retryable": False}


class RunBudgetMinimumError(ValueError):
    """The selected budget cannot fund a single complete baseline traversal."""

    def __init__(self, message, issues):
        super().__init__(message + ': ' + ', '.join(item['resource'] for item in issues))
        self.admission_details = {"reasonCode": 'budget-below-execution-minimum',
                                  "message": message, "issues": issues, "retryable": False}


def profile_budget_limits(profile):
    """Use the same frozen ceilings at discovery, admission and persistence."""
    config = profile.model_policy.get('config_json', {}) or {}
    reasoning = getattr(profile, 'reasoning_policy', {}) or {}
    return _counters({**config.get('budgetLimits', {}),
                      **reasoning.get('budgetLimits', {})}, allow_empty=False)


def resolve_profile_budget(profile, requested):
    configured = profile_budget_limits(profile)
    requested = _counters(requested)
    issues = [{"field": "budget." + key, "resource": key,
               "requested": amount, "maximum": configured.get(key, 0)}
              for key, amount in requested.items() if amount > configured.get(key, 0)]
    if issues:
        raise RunBudgetAdmissionError('budget-limit-exceeded',
            '请求预算超过当前 Agent Profile 上限；请按 maximum 调整，或使用空 budget 继承配置。', issues)
    return {**configured, **requested}


class Phase3Repository(PostgresRepository):
    def load_profile(self, agent_key, version=None):
        # Admin mutations take the matching exclusive transaction lock. Keep a
        # session shared lock over all profile/provider/source reads, which use
        # separate short-lived repository transactions.
        with self.connection_factory() as lock_con:
            with lock_con.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock_shared(hashtextextended('agent-admin-profile-config',0))")
            try:
                return self._load_profile_consistent(agent_key, version)
            finally:
                with lock_con.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock_shared(hashtextextended('agent-admin-profile-config',0))")

    def _load_profile_consistent(self, agent_key, version=None):
        profile = super().load_profile(agent_key, version)
        if hasattr(self, 'load_reasoning_policy'):
            reasoning = self.load_reasoning_policy(agent_key)
            if reasoning and reasoning.get('status') != 'unavailable':
                profile = replace(profile, reasoning_policy=reasoning)
        config = profile.model_policy.get("config_json", {})
        if not config.get("researchModeAvailable"):
            return profile
        provider_key = profile.model_policy.get("model_provider")
        provider = self.get_provider(provider_key)
        if not provider or not provider["enabled"] or provider["provider_type"] != "model":
            raise ValueError("model provider is missing or disabled")
        model = deepcopy(profile.model_policy)
        model["providerSnapshot"] = {key: deepcopy(provider[key]) for key in (
            "provider_key", "base_url", "credential_ref", "enabled", "version", "config_json")}
        source = deepcopy(profile.source_policy)
        if profile.agent_key not in FUTURE_AGENT_KEYS:
            source_key = config.get("ontologySourceKey", "uav-ontology")
            registered = self.get_data_source(source_key)
            if not registered or not registered["enabled"] or not registered["read_only"]:
                raise ValueError("ontology source must be enabled and read only")
            if registered["connector"] != "sqlite":
                raise ValueError("ontology connector is unsupported")
            source["ontologySource"] = {key: deepcopy(registered[key]) for key in (
                "source_key", "connector", "root_uri", "enabled", "read_only", "policy_version", "config_json")}
        if any(key.startswith("web.") and tool.enabled for key, tool in profile.tools.items()):
            web = self.get_provider(config.get("webProviderKey", "firecrawl"))
            if not web or not web["enabled"] or web["provider_type"] != "web":
                raise ValueError("web provider is missing or disabled")
            ref = web["credential_ref"] or ""
            if not ref.startswith("env:"):
                raise ValueError("web credential must be an environment reference")
            source["firecrawlProvider"] = {"providerKey": web["provider_key"], "baseUrl": web["base_url"],
                                           "credentialRef": ref[4:], "version": web["version"],
                                           "config": deepcopy(web.get('config_json') or {})}
        resources = deepcopy(profile.resource_policy)
        resources["budgetLimits"] = deepcopy(config.get("budgetLimits", {}))
        resources["budgetLimits"].update((profile.reasoning_policy or {}).get('budgetLimits', {}))
        capability = {}
        if config.get("capabilityGraphPath") or profile.agent_key in FUTURE_AGENT_KEYS:
            from .capabilities import graph_snapshot
            with self.connection_factory() as con:
                with con.cursor() as cursor:
                    capability = graph_snapshot(cursor, config, profile.agent_key)
        return replace(profile, model_policy=model, source_policy=source, resource_policy=resources,
                       capability_graph=capability)

    def create_run(self, profile, input_data, _cursor=None):
        from .data_contracts import validate_admission
        validate_admission(profile, input_data)
        config = profile.model_policy.get("config_json", {})
        from .cognitive_identity import ACTION_KEYS as COGNITIVE_ACTIONS, APPLICATION_KEY
        if profile.agent_key in COGNITIVE_ACTIONS and not input_data.get('parentRunId'):
            raise PermissionError('cognitive professional roles require a delegated application Run')
        if profile.agent_key == APPLICATION_KEY and input_data.get('executionMode') != 'profile-run':
            raise PermissionError('cognitive research requires a frozen application admission')
        if profile.agent_key in FUTURE_AGENT_KEYS:
            # Apply the same persisted default to direct repository admissions
            # (including workspace runs) as to the public Engine entry point.
            input_data = {'automationMode': 'unattended-draft', **input_data}
            from .runtime_contracts import validate_future_research_admission
            validate_future_research_admission(profile, input_data)
        if config.get('agentLayer') == 'action':
            if input_data.get('executionMode') == 'research':
                raise ValueError('deterministic Actions use their fixed Plan')
            limits = _counters(config.get('budgetLimits', {}), allow_empty=False)
            for key, amount in _counters(input_data.get('budgetLimits', {})).items():
                if amount > limits.get(key, 0):
                    raise PermissionError('Action budget cannot exceed configured limits')
                limits[key] = amount
            resources = {**deepcopy(profile.resource_policy), 'budgetLimits': limits}
            parent_run_id = input_data.get('parentRunId')
            if parent_run_id is not None and (not isinstance(parent_run_id, str) or not parent_run_id):
                raise ValueError('parentRunId must be a non-empty string')
            def persist_action(cursor):
                rid = super(Phase3Repository, self).create_run(replace(profile, resource_policy=resources), input_data, _cursor=cursor)
                if parent_run_id:
                    cursor.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s", (parent_run_id,))
                    parent = cursor.fetchone()
                    if not parent:
                        raise ValueError('parent budget is not initialized')
                    root_id = parent['root_run_id']
                    cursor.execute("SELECT limits_json FROM agent_run_budget WHERE root_run_id=%s", (root_id,))
                    root_limits = dict(cursor.fetchone()['limits_json'])
                    cursor.execute('INSERT INTO agent_run_budget_member(run_id,root_run_id,parent_run_id) VALUES(%s,%s,%s)', (rid, root_id, parent_run_id))
                    self.event(cursor, rid, 'budget.shared', None, {'rootRunId': root_id, 'parentRunId': parent_run_id, 'agentLayer': 'action'})
                else:
                    cursor.execute('INSERT INTO agent_run_budget(root_run_id,limits_json) VALUES(%s,%s)', (rid, Json(limits)))
                    cursor.execute('INSERT INTO agent_run_budget_member(run_id,root_run_id,parent_run_id) VALUES(%s,%s,NULL)', (rid, rid))
                    self.event(cursor, rid, 'budget.initialized', None, {'rootRunId': rid, 'parentRunId': None, 'agentLayer': 'action'})
                return rid
            if _cursor is not None:
                return persist_action(_cursor)
            with self.connection_factory() as con:
                with con.cursor() as cursor:
                    return persist_action(cursor)
        if input_data.get("executionMode") != "research":
            # Fixed application Profiles with Action bindings still need a
            # durable root budget so every delegated child charges the same
            # ceiling while the research provider remains intentionally off.
            baseline = profile.execution_baseline or {}
            has_child_nodes = any(node.get('childAgentKey') for node in (baseline.get('graph', {}) or {}).get('nodes', []))
            from .cognitive_identity import AGENT_KEYS as COGNITIVE_KEYS
            if profile.agent_key in FUTURE_AGENT_KEYS or profile.agent_key in COGNITIVE_KEYS:
                limits = resolve_profile_budget(profile, input_data.get('budgetLimits', {}))
                resources = {**deepcopy(profile.resource_policy), 'budgetLimits': limits}
                def persist_fixed(cursor):
                    if profile.agent_key in COGNITIVE_KEYS:
                        from .cognitive_admission import validate_root
                        validate_root(cursor, profile, input_data)
                    rid = super(Phase3Repository, self).create_run(
                        replace(profile, resource_policy=resources), input_data, _cursor=cursor)
                    if profile.agent_key in COGNITIVE_KEYS:
                        validate_root(cursor, profile, input_data, consume_run_id=rid)
                    cursor.execute('INSERT INTO agent_run_budget(root_run_id,limits_json) VALUES(%s,%s)', (rid, Json(limits)))
                    cursor.execute('INSERT INTO agent_run_budget_member(run_id,root_run_id,parent_run_id) VALUES(%s,%s,NULL)', (rid, rid))
                    self.event(cursor, rid, 'budget.initialized', None, {'rootRunId': rid, 'parentRunId': None, 'agentLayer': profile.agent_layer})
                    return rid
                if _cursor is not None:
                    return persist_fixed(_cursor)
                with self.connection_factory() as con:
                    with con.cursor() as cursor:
                        return persist_fixed(cursor)
            return super().create_run(profile, input_data, _cursor=_cursor)
        if not config.get("researchModeAvailable") or "providerSnapshot" not in profile.model_policy:
            raise ValueError("research mode is not configured for this agent")
        # The caller's selected entity narrows configured scope; it is never a
        # model-provided whitelist. Typed entity validation happens before writes.
        entity_id = input_data.get("entityId") or input_data.get("uavId")
        if not isinstance(entity_id, str) or not entity_id or len(entity_id) > 200:
            raise ValueError("entityId is required for research runs")
        requirements = input_data.get('requirements', {})
        if not isinstance(requirements, dict) or len(requirements) > 100:
            raise ValueError('requirements must be a bounded object')
        if any(not isinstance(key,str) or not 1 <= len(key) <= 120 for key in requirements):
            raise ValueError('invalid requirement name')
        for flag in ('collaborate','includeDesign'):
            if flag in input_data and type(input_data[flag]) is not bool:
                raise ValueError(flag + ' must be boolean')
        question=input_data.get('question','')
        if not isinstance(question,str) or len(question)>10000:
            raise ValueError('question must be bounded text')
        material = input_data.get('supplementalMaterial', '')
        if not isinstance(material, str) or len(material) > 30000:
            raise ValueError('supplemental material must be bounded text')
        if 'entitySelection' in input_data or 'analysisPurpose' in input_data:
            from agent_tools.ontology_catalog import bind_selection
            input_data = bind_selection(profile, input_data)
        scope = deepcopy(profile.scope_policy)
        permitted = scope.get("allowedEntities")
        if permitted is not None and entity_id not in permitted:
            raise PermissionError("entity is outside the configured scope")
        scope["allowedEntities"] = [entity_id]
        scope.setdefault("allowedMetrics", config.get("allowedMetrics", [
            "enduranceHours", "payloadKg", "maxSpeedMps", "mtowKg", "cruiseSpeedMps", "rangeKm", "ceilingM"]))
        limits = _counters(profile.resource_policy.get("budgetLimits", {}), allow_empty=False)
        requested_limits = _counters(input_data.get("budgetLimits", {}))
        for key, amount in requested_limits.items():
            if amount > limits.get(key, 0):
                raise PermissionError("run budget cannot exceed configured limits")
            limits[key] = amount
        if limits.get("scrape_calls", 0) > profile.source_policy.get("maxPages", 8):
            raise ValueError("scrape budget exceeds frozen source policy")
        model = deepcopy(profile.model_policy)
        model["config_json"]["engineMode"] = "research-v1"
        resources = deepcopy(profile.resource_policy)
        resources["budgetLimits"] = limits
        def persist(cursor):
            run_id = super(Phase3Repository, self).create_run(
                replace(profile, scope_policy=scope, model_policy=model, resource_policy=resources),
                input_data, _cursor=cursor)
            cursor.execute("INSERT INTO agent_run_budget(root_run_id,limits_json) VALUES(%s,%s)", (run_id, Json(limits)))
            cursor.execute("INSERT INTO agent_run_budget_member(run_id,root_run_id,parent_run_id) VALUES(%s,%s,NULL)",
                           (run_id, run_id))
            self.event(cursor, run_id, "budget.initialized", None, {"rootRunId": run_id, "parentRunId": None})
            return run_id
        if _cursor is not None:
            return persist(_cursor)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                return persist(cursor)

    def get_provider(self, provider_key: str) -> dict[str, Any] | None:
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM provider_registry WHERE provider_key=%s", (provider_key,))
                return self._one(cursor)

    def get_data_source(self, source_key: str) -> dict[str, Any] | None:
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM data_source_registry WHERE source_key=%s", (source_key,))
                return self._one(cursor)

    def initialize_budget(self, run_id: str, limits: dict[str, int],
                          parent_run_id: str | None = None) -> dict[str, Any]:
        """Freeze a root limit or join an existing parent's budget.

        Child limits must be empty or exactly match the root; assigning an
        independent allowance to a child is intentionally unsupported.
        """
        limits = _counters(limits)
        if parent_run_id == run_id:
            raise ValueError("a run cannot be its own parent")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT run_id FROM agent_run WHERE run_id=%s FOR UPDATE", (run_id,))
                if not cursor.fetchone():
                    raise ValueError("run not found")
                cursor.execute("SELECT * FROM agent_run_budget_member WHERE run_id=%s", (run_id,))
                member = self._one(cursor)
                if member:
                    if member["parent_run_id"] != parent_run_id:
                        raise ValueError("budget parent cannot change")
                    cursor.execute("SELECT limits_json FROM agent_run_budget WHERE root_run_id=%s",
                                   (member["root_run_id"],))
                    existing = dict(cursor.fetchone()["limits_json"])
                    if (parent_run_id is None or limits) and limits != existing:
                        raise ValueError("budget limits cannot change")
                    return self._budget_view(cursor, run_id)
                if parent_run_id is not None:
                    cursor.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s",
                                   (parent_run_id,))
                    parent = cursor.fetchone()
                    if not parent:
                        raise ValueError("parent budget is not initialized")
                    root_id = parent["root_run_id"]
                    cursor.execute("SELECT limits_json FROM agent_run_budget WHERE root_run_id=%s",
                                   (root_id,))
                    if limits and limits != dict(cursor.fetchone()["limits_json"]):
                        raise ValueError("child budget cannot override root limits")
                else:
                    root_id = run_id
                    cursor.execute("INSERT INTO agent_run_budget(root_run_id,limits_json) VALUES(%s,%s)",
                                   (run_id, Json(limits)))
                cursor.execute("INSERT INTO agent_run_budget_member(run_id,root_run_id,parent_run_id) "
                               "VALUES(%s,%s,%s)", (run_id, root_id, parent_run_id))
                self.event(cursor, run_id, "budget.initialized", None,
                           {"rootRunId": root_id, "parentRunId": parent_run_id})
                return self._budget_view(cursor, run_id)

    @staticmethod
    def _budget_view(cursor, run_id: str) -> dict[str, Any]:
        cursor.execute("SELECT m.run_id,m.root_run_id,m.parent_run_id,b.limits_json,b.used_json "
                       "FROM agent_run_budget_member m JOIN agent_run_budget b USING(root_run_id) "
                       "WHERE m.run_id=%s", (run_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("budget is not initialized")
        limits, used = dict(row["limits_json"]), dict(row["used_json"])
        return {"run_id": row["run_id"], "root_run_id": row["root_run_id"],
                "parent_run_id": row["parent_run_id"], "limits": limits, "used": used,
                "remaining": {key: max(0, limits.get(key, 0)-used.get(key, 0)) for key in RESOURCES},
                "overrun": {key: used[key]-limits.get(key, 0) for key in used
                            if used[key] > limits.get(key, 0)}}

    def get_budget(self, run_id: str) -> dict[str, Any]:
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                return self._budget_view(cursor, run_id)

    def record_budget_ledger(self, run_id: str, *, kind: str, status: str,
                             estimate: dict[str, int] | None = None,
                             actual: dict[str, int] | None = None,
                             operation_key: str | None = None,
                             step_key: str | None = None,
                             retry_count: int = 0,
                             concurrency_peak: int | None = None,
                             cancellation_reason: str | None = None,
                             incomplete: bool = False):
        """Append one cost/termination record when the P1-D ledger exists.

        Older verification schemas deliberately remain compatible: the method
        is a no-op when migration 064 has not been applied.
        """
        if kind not in {'tool', 'model', 'http', 'search', 'scrape'}:
            raise ValueError('invalid budget ledger kind')
        if status not in {'succeeded', 'failed', 'timeout', 'cancelled', 'unknown', 'incomplete'}:
            raise ValueError('invalid budget ledger status')
        if type(retry_count) is not int or retry_count < 0:
            raise ValueError('retry_count must be nonnegative')
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT to_regclass(current_schema() || '.agent_run_budget_ledger') AS table_name")
                row = cursor.fetchone()
                if not row or not row['table_name']:
                    return None
                cursor.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s", (run_id,))
                membership = cursor.fetchone()
                if not membership:
                    raise ValueError('budget ledger requires initialized Run membership')
                ledger_id = new_id('ledger')
                cursor.execute("""INSERT INTO agent_run_budget_ledger(
                    ledger_id,root_run_id,run_id,step_key,operation_key,kind,status,
                    estimate_json,actual_json,retry_count,concurrency_peak,cancellation_reason,incomplete)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id,operation_key,kind) WHERE operation_key IS NOT NULL DO NOTHING
                    RETURNING ledger_id""", (ledger_id, membership['root_run_id'], run_id, step_key,
                    operation_key, kind, status, Json(estimate or {}), Json(actual or {}), retry_count,
                    concurrency_peak, cancellation_reason, bool(incomplete)))
                inserted = cursor.fetchone()
                if inserted:
                    self.event(cursor, run_id, 'budget.ledger', step_key,
                               {'ledgerId': inserted['ledger_id'], 'rootRunId': membership['root_run_id'],
                                'kind': kind, 'status': status, 'operationKey': operation_key,
                                'estimate': estimate or {}, 'actual': actual or {},
                                'retryCount': retry_count, 'incomplete': bool(incomplete)})
                return inserted['ledger_id'] if inserted else None

    @staticmethod
    def _lock_budget(cursor, run_id: str) -> dict[str, Any]:
        from .mission_budget import lock_settlement
        lock_settlement(cursor, run_id)
        cursor.execute("SELECT b.* FROM agent_run_budget b JOIN agent_run_budget_member m "
                       "USING(root_run_id) WHERE m.run_id=%s FOR NO KEY UPDATE OF b", (run_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("budget is not initialized")
        return dict(row)

    def reserve_budget(self, run_id: str, charges: dict[str, int],
                       operation_key: str | None = None, step_key: str | None = None, *, cursor=None) -> str:
        return self._reserve_budget(run_id, charges, operation_key, step_key, cursor=cursor)

    def _reserve_budget(self, run_id: str, charges: dict[str, int],
                        operation_key: str | None = None, step_key: str | None = None, *, cursor=None,
                        reasoning_stage_entry: bool = False) -> str:
        charges = _counters(charges, allow_empty=False)
        if not any(charges.values()):
            raise ValueError("reservation must charge a positive amount")
        if operation_key is not None and (not isinstance(operation_key, str) or not operation_key.strip()):
            raise ValueError("operation key must be a nonempty string")
        if cursor is None:
            with self.connection_factory() as con:
                with con.cursor() as transaction_cursor:
                    return self._reserve_budget(run_id, charges, operation_key, step_key, cursor=transaction_cursor,
                                                reasoning_stage_entry=reasoning_stage_entry)
        budget = self._lock_budget(cursor, run_id)
        root_id = budget["root_run_id"]
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_reasoning_budget_binding') AS name")
        integrity = bool(cursor.fetchone()['name'])
        if step_key is None and hasattr(self, 'reserve_reasoning_stage'):
            from .reasoning_repository import reasoning_installed
            if reasoning_installed(cursor) and not reasoning_stage_entry:
                cursor.execute('''WITH RECURSIVE ancestors(run_id,parent_run_id,path,depth) AS (
                    SELECT m.run_id,m.parent_run_id,ARRAY[m.run_id],1
                    FROM agent_run_budget_member m WHERE m.run_id=%s
                    UNION ALL
                    SELECT m.run_id,m.parent_run_id,a.path||m.run_id,a.depth+1
                    FROM ancestors a JOIN agent_run_budget_member m ON m.run_id=a.parent_run_id
                    WHERE NOT m.run_id=ANY(a.path) AND a.depth<128
                ) SELECT EXISTS(SELECT 1 FROM ancestors a
                    JOIN agent_run_reasoning_snapshot s ON s.run_id=a.run_id) AS has_reasoning,
                    EXISTS(SELECT 1 FROM ancestors a JOIN agent_run_budget_member m ON m.run_id=a.run_id
                        WHERE a.depth=128 AND m.parent_run_id IS NOT NULL) AS too_deep''', (run_id,))
                ancestry = cursor.fetchone()
                if ancestry['too_deep']:
                    raise ValueError('budget parent chain is cyclic or too deep')
                if ancestry['has_reasoning']:
                    raise PermissionError('reasoning budget reservation requires a frozen node attribution')
            if reasoning_stage_entry and charges != {'reasoning_rounds': 1}:
                raise ValueError('reasoning stage entry can only charge one reasoning round')
        if operation_key is not None:
            cursor.execute("SELECT * FROM agent_budget_reservation WHERE root_run_id=%s "
                           "AND operation_key=%s", (root_id, operation_key))
            previous = cursor.fetchone()
            if previous:
                if (previous["run_id"] != run_id or previous["charges_json"] != charges
                        or (integrity and previous.get('step_key') != step_key)):
                    raise ValueError("operation key already identifies a different reservation")
                return previous["reservation_id"]
        from .cognitive_budget import assert_node_budgets
        assert_node_budgets(cursor, root_id, run_id, step_key, charges)
        from .task_intent import assert_dispatch
        assert_dispatch(cursor, run_id, charges=charges)
        from .evolution_budget import assert_dispatch as evolution_dispatch
        evolution_dispatch(cursor, run_id, charges=charges)
        limits, used = dict(budget["limits_json"]), dict(budget["used_json"])
        if any(amount > limits.get(key, 0) for key, amount in used.items()):
            raise BudgetExceededError("shared run budget already has an actual-usage overrun")
        for key, count in charges.items():
            if used.get(key, 0)+count > limits.get(key, 0):
                raise BudgetExceededError(f"shared run budget exceeded: {key}")
            used[key] = used.get(key, 0)+count
        # P0 node ceilings are checked and incremented under the same
        # root-budget lock. This remains safe across workers, retries,
        # and child Runs; old databases simply skip this optional path.
        stage_usages = []
        if step_key is not None:
            # Mirror every real request (including delegated Actions)
            # into its parent's frozen reasoning-stage allowance. The
            # global ledger and stage ledger commit atomically.
            if hasattr(self, 'reserve_reasoning_stage'):
                from .reasoning_repository import reasoning_installed
                if reasoning_installed(cursor):
                    targets = self._reasoning_budget_targets(cursor, run_id, step_key)
                    if targets and not integrity:
                        raise RuntimeError('reasoning budget integrity migration 072 is required')
                    for target in targets:
                        usage = self.reserve_reasoning_stage(target['runId'], target['stageKey'], target['iteration'],
                            f'stage:{run_id}:{operation_key or new_id("request")}', charges,
                            cursor=cursor, billed_run_id=run_id)
                        stage_usages.append(usage)
            cursor.execute("""SELECT 1 FROM information_schema.tables
                              WHERE table_schema=current_schema()
                                AND table_name='agent_run_step_budget_usage'""")
            if cursor.fetchone():
                cursor.execute("""SELECT hsb.model_calls,hsb.tool_calls,hsb.http_calls,
                                        hsb.search_calls,hsb.scrape_calls,hsb.input_tokens,
                                        hsb.output_tokens,hsb.enforcement
                                   FROM agent_run r
                                   JOIN agent_harness_step_budget hsb
                                     ON hsb.harness_contract_id=r.harness_contract_id
                                    AND hsb.step_key=%s
                                  WHERE r.run_id=%s""", (step_key,run_id))
                ceiling = cursor.fetchone()
                # Dynamic research nodes are planned at runtime and do
                # not necessarily have a fixed execution-graph budget.
                # They remain governed by the shared root budget; only
                # declared Harness nodes receive an additional ceiling.
                if ceiling:
                    cursor.execute("""INSERT INTO agent_run_step_budget_usage(run_id,step_key,used_json)
                                      VALUES(%s,%s,'{}'::jsonb)
                                      ON CONFLICT(run_id,step_key) DO NOTHING""", (run_id,step_key))
                    cursor.execute("""SELECT used_json FROM agent_run_step_budget_usage
                                      WHERE run_id=%s AND step_key=%s FOR UPDATE""", (run_id,step_key))
                    node_used=dict(cursor.fetchone()["used_json"] or {})
                    for key, count in charges.items():
                        ceiling_value=ceiling.get(key)
                        if ceiling_value is None:
                            continue
                        if node_used.get(key,0)+count > int(ceiling_value):
                            raise BudgetExceededError(f"Harness step budget exceeded: {step_key}:{key}")
                        node_used[key]=node_used.get(key,0)+count
                    cursor.execute("UPDATE agent_run_step_budget_usage SET used_json=%s,updated_at=now() "
                                   "WHERE run_id=%s AND step_key=%s", (Json(node_used),run_id,step_key))
        reservation_id = new_id("reservation")
        if integrity:
            cursor.execute("INSERT INTO agent_budget_reservation(reservation_id,root_run_id,run_id,"
                           "operation_key,charges_json,step_key) VALUES(%s,%s,%s,%s,%s,%s)",
                           (reservation_id, root_id, run_id, operation_key, Json(charges), step_key))
            for usage in stage_usages:
                cursor.execute('''INSERT INTO agent_reasoning_budget_binding(
                    reservation_id,run_id,owner_run_id,stage_key,iteration,usage_id) VALUES(%s,%s,%s,%s,%s,%s)''',
                    (reservation_id, run_id, usage['runId'], usage['stageKey'], usage['iteration'], usage['usageId']))
        else:
            cursor.execute("INSERT INTO agent_budget_reservation(reservation_id,root_run_id,run_id,"
                           "operation_key,charges_json) VALUES(%s,%s,%s,%s,%s)",
                           (reservation_id, root_id, run_id, operation_key, Json(charges)))
        cursor.execute("UPDATE agent_run_budget SET used_json=%s,updated_at=now() "
                       "WHERE root_run_id=%s", (Json(used), root_id))
        self.event(cursor, run_id, "budget.reserved", None,
                   {"reservationId": reservation_id, "rootRunId": root_id, "charges": charges})
        return reservation_id

    def settle_budget(self, reservation_id: str, actual: dict[str, int]) -> dict[str, Any]:
        """Settle known usage once per dimension; unknown stays reserved.

        A zero call count never refunds an attempted request. Token usage may
        release unused reservation. An unexpectedly larger provider usage is
        recorded even beyond the limit, then blocks all subsequent calls.
        """
        actual = _counters(actual)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_budget_reservation WHERE reservation_id=%s",
                               (reservation_id,))
                reservation = self._one(cursor)
                if not reservation:
                    raise ValueError("reservation not found")
                if not set(actual).issubset(reservation["charges_json"]):
                    raise ValueError("cannot settle a resource that was not reserved")
                budget = self._lock_budget(cursor, reservation["run_id"])
                from .evolution_budget import lock_settlement
                lock_settlement(cursor,reservation['run_id'])
                cursor.execute("SELECT * FROM agent_budget_settlement WHERE reservation_id=%s "
                               "ORDER BY revision DESC LIMIT 1", (reservation_id,))
                previous = self._one(cursor)
                known = dict(previous["known_json"]) if previous else {}
                for key, amount in actual.items():
                    if key in known and known[key] != amount:
                        raise ValueError("settled usage cannot be rewritten")
                known.update(actual)
                before = dict(previous["effective_json"] if previous else reservation["charges_json"])
                effective = dict(reservation["charges_json"])
                for key, amount in known.items():
                    effective[key] = amount if key in TOKEN_RESOURCES else max(effective[key], amount)
                used = dict(budget["used_json"])
                for key in effective:
                    used[key] = used.get(key, 0)+effective[key]-before[key]
                    if not 0 <= used[key] <= MAX_COUNTER:
                        raise ValueError("settled budget counter outside supported range")
                overrun = {key: amount-budget["limits_json"].get(key, 0) for key, amount in used.items()
                           if amount > budget["limits_json"].get(key, 0)}
                stage_overrun = {}
                if not previous or previous["known_json"] != known:
                    revision = previous['revision'] + 1 if previous else 1
                    cursor.execute("INSERT INTO agent_budget_settlement(settlement_id,reservation_id,revision,"
                                   "known_json,effective_json,overrun_json) VALUES(%s,%s,%s,%s,%s,%s)",
                                   (new_id("settlement"), reservation_id,
                                    revision,
                                    Json(known), Json(effective), Json(overrun)))
                    if hasattr(self, '_settle_reasoning_budget'):
                        stage_overrun = self._settle_reasoning_budget(cursor, reservation, revision, effective)
                    cursor.execute("UPDATE agent_run_budget SET used_json=%s,updated_at=now() "
                                   "WHERE root_run_id=%s", (Json(used), budget["root_run_id"]))
                    self.event(cursor, reservation["run_id"], "budget.settled", None,
                               {"reservationId": reservation_id, "known": known,
                                "effective": effective, "overrun": overrun, "stageOverrun": stage_overrun})
                elif hasattr(self, '_reasoning_budget_installed') and self._reasoning_budget_installed(cursor):
                    cursor.execute('''SELECT owner_run_id,overrun_json,b.stage_key FROM agent_reasoning_stage_settlement s
                        JOIN agent_reasoning_budget_binding b USING(usage_id,reservation_id,run_id,owner_run_id)
                        WHERE s.reservation_id=%s AND s.revision=%s''', (reservation_id, previous['revision']))
                    stage_overrun = {r['owner_run_id'] + ':' + r['stage_key']: r['overrun_json'] for r in cursor.fetchall() if r['overrun_json']}
                return {"reservation_id": reservation_id, "known": known,
                        "effective": effective, "overrun": overrun, "stage_overrun": stage_overrun}

    def record_model_call(self, run_id: str, step_run_id: str | None = None, **metadata) -> str:
        """Append metadata only. Prompts/responses belong in controlled artifacts."""
        values = dict(metadata.pop("metadata", {}) or {})
        values.update(metadata)
        aliases = {"requestedModel": "requested_model", "actualModel": "actual_model",
                   "providerKey": "provider_key", "latency": "latency_ms", "retry": "retry_count",
                   "finishReason": "finish_reason", "inputArtifactRef": "input_artifact_id",
                   "outputArtifactRef": "output_artifact_id", "error": "error_code"}
        for old, new in aliases.items():
            if old in values:
                values.setdefault(new, values.pop(old))
        usage = values.pop("usage", {}) or {}
        if usage:
            values.setdefault("input_tokens", usage.get("input_tokens", usage.get("prompt_tokens")))
            values.setdefault("output_tokens", usage.get("output_tokens", usage.get("completion_tokens")))
        fields = ("provider_key", "requested_model", "actual_model", "status", "input_tokens",
                  "output_tokens", "latency_ms", "retry_count", "http_status", "finish_reason",
                  "error_code", "input_artifact_id", "output_artifact_id", "stage", "attempt_id",
                  "reservation_id", "context_id", "manifest_hash", "operation_key")
        if set(values)-set(fields):
            raise ValueError("unsupported model audit metadata")
        for key in ("provider_key", "requested_model", "status"):
            if not isinstance(values.get(key), str) or not values[key].strip():
                raise ValueError(f"model audit requires {key}")
        for key in ("input_tokens", "output_tokens", "latency_ms", "retry_count"):
            amount = values.get(key)
            if amount is not None and (isinstance(amount, bool) or not isinstance(amount, int)
                                       or not 0 <= amount <= MAX_COUNTER):
                raise ValueError(f"invalid model audit counter: {key}")
        if values.get("error_code") and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", values["error_code"]):
            raise ValueError("model audit accepts an error code, not raw provider errors")
        values.setdefault("latency_ms", 0)
        values.setdefault("retry_count", 0)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                # Context columns are introduced by migration 055.  Keep the
                # recorder compatible with pre-055 databases and fixtures.
                cursor.execute("""SELECT column_name FROM information_schema.columns
                                  WHERE table_schema=current_schema() AND table_name='agent_model_call'
                                    AND column_name IN ('context_id','manifest_hash','operation_key')""")
                context_columns = {row['column_name'] for row in cursor.fetchall()}
                insert_fields = tuple(field for field in fields if field not in {'context_id','manifest_hash','operation_key'}
                                      or field in context_columns)
                # Serializes idempotent attempt writes and protects audit count.
                cursor.execute("SELECT run_id FROM agent_run WHERE run_id=%s FOR UPDATE", (run_id,))
                if not cursor.fetchone():
                    raise ValueError("run not found")
                if values.get("attempt_id"):
                    cursor.execute("SELECT * FROM agent_model_call WHERE run_id=%s AND attempt_id=%s",
                                   (run_id, values["attempt_id"]))
                    previous = cursor.fetchone()
                    if previous:
                        if previous["step_run_id"] != step_run_id or any(
                                previous[field] != values.get(field) for field in insert_fields):
                            raise ValueError("model attempt audit cannot be rewritten")
                        return previous["call_id"]
                for table, key, object_id in (
                    ("agent_step_run", "step_run_id", step_run_id),
                    ("agent_run_artifact", "artifact_id", values.get("input_artifact_id")),
                    ("agent_run_artifact", "artifact_id", values.get("output_artifact_id")),
                    ("agent_budget_reservation", "reservation_id", values.get("reservation_id")),
                ):
                    if object_id:
                        # Names are hard-coded above; only values are caller input.
                        cursor.execute(f"SELECT run_id FROM {table} WHERE {key}=%s", (object_id,))
                        owner = cursor.fetchone()
                        if not owner or owner["run_id"] != run_id:
                            raise ValueError("model audit reference belongs to a different run or is absent")
                cursor.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s", (run_id,))
                member = cursor.fetchone()
                call_id = new_id("model-call")
                placeholders = ",".join(["%s"]*(len(insert_fields)+4))
                cursor.execute(f"INSERT INTO agent_model_call(call_id,run_id,step_run_id,root_run_id,"
                               f"{','.join(insert_fields)}) VALUES({placeholders})",
                               (call_id, run_id, step_run_id, member["root_run_id"] if member else None,
                                *(values.get(field) for field in insert_fields)))
                cursor.execute("UPDATE agent_run SET model_call_count=model_call_count+1,updated_at=now() "
                               "WHERE run_id=%s", (run_id,))
                self.event(cursor, run_id, "model.call", None,
                           {"callId": call_id, "providerKey": values["provider_key"],
                            "requestedModel": values["requested_model"], "actualModel": values.get("actual_model"),
                            "status": values["status"], "reservationId": values.get("reservation_id")})
                return call_id

    def list_model_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_model_call WHERE run_id=%s ORDER BY created_at,call_id",
                               (run_id,))
                return [dict(row) for row in cursor.fetchall()]

    def get_model_call(self, run_id: str, call_id: str) -> dict[str, Any] | None:
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_model_call WHERE run_id=%s AND call_id=%s", (run_id, call_id))
                row = cursor.fetchone(); return dict(row) if row else None

    def list_model_call_summaries(self, run_id: str) -> list[dict[str, Any]]:
        """Model-call metadata for the graph; output artifacts remain linked."""
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("""SELECT call_id,run_id,step_run_id,status,latency_ms,retry_count,
                                        input_artifact_id,output_artifact_id,stage,created_at,
                                        context_id,provider_key,requested_model,actual_model,error_code
                                   FROM agent_model_call WHERE run_id=%s ORDER BY created_at,call_id""", (run_id,))
                return [dict(row) for row in cursor.fetchall()]

    def store_plan(self, run_id: str, plan: dict[str, Any], reason: str | None = None,
                   validation: dict[str, Any] | None = None,
                   supersedes_plan_id: str | None = None,
                   baseline_hash: str | None = None,
                   parent_node_key: str | None = None,
                   context_id: str | None = None,
                   manifest_hash: str | None = None) -> str:
        if not isinstance(plan, dict) or not isinstance(validation or {}, dict):
            raise ValueError("plan and validation must be mappings")
        raw = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT run_id FROM agent_run WHERE run_id=%s FOR UPDATE", (run_id,))
                if not cursor.fetchone():
                    raise ValueError("run not found")
                cursor.execute("SELECT plan_id,plan_version FROM agent_run_plan WHERE run_id=%s "
                               "ORDER BY plan_version DESC LIMIT 1", (run_id,))
                previous = cursor.fetchone()
                if supersedes_plan_id is not None and (not previous or previous["plan_id"] != supersedes_plan_id):
                    raise ValueError("a replacement must supersede the current plan of this run")
                plan_id = new_id("plan")
                version = previous["plan_version"]+1 if previous else 1
                cursor.execute("""SELECT column_name FROM information_schema.columns
                                  WHERE table_schema=current_schema() AND table_name='agent_run_plan'
                                    AND column_name IN ('context_id','manifest_hash')""")
                context_columns = {row['column_name'] for row in cursor.fetchall()}
                extra_names = ',context_id,manifest_hash' if {'context_id','manifest_hash'} <= context_columns and context_id else ''
                extra_values = (context_id, manifest_hash) if extra_names else ()
                extra_placeholders = ',%s,%s' if extra_names else ''
                cursor.execute("INSERT INTO agent_run_plan(plan_id,run_id,plan_version,plan_json,plan_hash,"
                               "reason,validation_json,supersedes_plan_id,baseline_hash,parent_node_key" + extra_names + ") "
                               "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s" + extra_placeholders + ")",
                               (plan_id, run_id, version, Json(plan), digest, reason, Json(validation or {}),
                                previous["plan_id"] if previous else None, baseline_hash, parent_node_key, *extra_values))
                self.event(cursor, run_id, "plan.stored", None,
                           {"planId": plan_id, "version": version, "planHash": digest})
                return plan_id

    def list_plans(self, run_id: str) -> list[dict[str, Any]]:
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_run_plan WHERE run_id=%s ORDER BY plan_version", (run_id,))
                return [dict(row) for row in cursor.fetchall()]
