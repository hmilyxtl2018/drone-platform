from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS
import hashlib, json, secrets
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any
from contextlib import contextmanager
from copy import deepcopy
from psycopg2.extras import Json
from .snapshot_contracts import validate_credential_references

def new_id(prefix: str) -> str: return f"{prefix}-{secrets.token_hex(8)}"
def now(): return datetime.now(timezone.utc)
def as_json(v):
    if v is None: return {}
    if isinstance(v, str):
        try: return json.loads(v)
        except ValueError: return {}
    return v

class PostgresRepository:
    def __init__(self, connection_factory): self.connection_factory = connection_factory
    def connector_attempt(self, ctx, **identity):
        from .connectors.runtime import ConnectorRuntime
        return ConnectorRuntime(self.connection_factory).attempt(ctx, **identity)
    def annotate_connector_calls(self, rows, kind):
        """Join recorded identities only; never infer historical bindings."""
        if kind not in {'model', 'tool'}:
            raise ValueError('invalid-connector-call-kind')
        values = [dict(row) for row in rows]
        ids = [row.get('call_id') for row in values if row.get('call_id')]
        if not ids:
            return values
        with self.connection_factory() as con, con.cursor() as cursor:
            cursor.execute("SELECT to_regclass(current_schema() || '.connector_call_link') AS name")
            if not (cursor.fetchone() or {}).get('name'):
                return values
            cursor.execute('''SELECT connector_ref,connector_version,run_id,step_run_id,
                operation_key,attempt_key,call_kind,call_id,cache_reused FROM connector_call_link
                WHERE call_kind=%s AND call_id=ANY(%s)''', (kind, ids))
            bindings = {row['call_id']: dict(row) for row in cursor.fetchall()}
        for row in values:
            item = bindings.get(row.get('call_id'))
            if item and item['run_id'] == row.get('run_id'):
                row['connectorBinding'] = {'connectorRef': item['connector_ref'],
                    'connectorVersion': item['connector_version'], 'operationKey': item['operation_key'],
                    'attemptKey': item['attempt_key'], 'callKind': item['call_kind'], 'callId': item['call_id'],
                    'cacheReuse': bool(item.get('cache_reused'))}
        return values
    def _one(self, cur):
        row=cur.fetchone(); return dict(row) if row else None
    @staticmethod
    def _harness_installed(cursor):
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_harness_contract') AS table_name")
        row=cursor.fetchone()
        return bool(row and row['table_name'])

    def _load_harness_contract(self, cursor, agent_key, version):
        """Load the immutable authority contract when migration 053 is present."""
        if not self._harness_installed(cursor):
            return 'application', 'llm_loop', {}
        cursor.execute("""SELECT harness_contract_id,agent_layer,runtime_kind,invocation_mode,
                                default_deny,max_child_depth,lifecycle_policy_json,
                                failure_policy_json,memory_policy_json,side_effect_policy_json,
                                contract_json,contract_hash,contract_version,status
                           FROM agent_harness_contract
                          WHERE agent_key=%s AND profile_version=%s
                            AND status IN ('testing','published')
                          ORDER BY CASE WHEN status='published' THEN 0 ELSE 1 END,
                                   created_at DESC, contract_version DESC
                          LIMIT 1""", (agent_key, version))
        row=self._one(cursor)
        if not row:
            return 'application', 'llm_loop', {}
        contract={
            'harnessContractId': row['harness_contract_id'],
            'agentLayer': row['agent_layer'], 'runtimeKind': row['runtime_kind'],
            'invocationMode': row['invocation_mode'], 'defaultDeny': bool(row['default_deny']),
            'maxChildDepth': row['max_child_depth'], 'contractVersion': row['contract_version'],
            'lifecyclePolicy': as_json(row['lifecycle_policy_json']),
            'failurePolicy': as_json(row['failure_policy_json']),
            'memoryPolicy': as_json(row['memory_policy_json']),
            'sideEffectPolicy': as_json(row['side_effect_policy_json']),
            'contract': as_json(row['contract_json']), 'contractHash': row['contract_hash'],
            'status': row['status']
        }
        cursor.execute("""SELECT tool_key,tool_version,permission,approval_required,
                                input_constraint_json,grant_config_json
                           FROM agent_harness_tool_grant
                          WHERE harness_contract_id=%s
                          ORDER BY tool_key,tool_version""", (row['harness_contract_id'],))
        contract['toolGrants']=[{**dict(item),
            'approvalRequired': bool(item['approval_required']),
            'inputConstraint': as_json(item['input_constraint_json']),
            'grantConfig': as_json(item['grant_config_json'])} for item in cursor.fetchall()]
        cursor.execute("""SELECT grant_id,resource_type,resource_pattern,access_mode,
                                sensitivity_level,approval_required
                           FROM agent_harness_data_grant
                          WHERE harness_contract_id=%s ORDER BY grant_id""", (row['harness_contract_id'],))
        contract['dataGrants']=[{**dict(item), 'approvalRequired': bool(item['approval_required'])}
                                for item in cursor.fetchall()]
        cursor.execute("""SELECT network_grant_id,domain_pattern,methods_json,
                                max_response_bytes,approval_required
                           FROM agent_harness_network_grant
                          WHERE harness_contract_id=%s ORDER BY domain_pattern""", (row['harness_contract_id'],))
        contract['networkGrants']=[{**dict(item), 'methods': as_json(item['methods_json']),
                                    'approvalRequired': bool(item['approval_required'])}
                                   for item in cursor.fetchall()]
        cursor.execute("""SELECT edge_id,parent_agent_key,child_agent_key,parent_profile_version,
                                child_profile_version,handoff_schema_json,allowed_artifact_types_json,
                                budget_mode,max_depth,approval_required,enabled
                           FROM agent_composition_edge
                          WHERE parent_agent_key=%s AND parent_profile_version=%s
                            AND enabled=true ORDER BY child_agent_key""", (agent_key, version))
        contract['compositionEdges']=[{**dict(item),
            'handoffSchema': as_json(item['handoff_schema_json']),
            'allowedArtifactTypes': as_json(item['allowed_artifact_types_json']),
            'approvalRequired': bool(item['approval_required']),
            'enabled': bool(item['enabled'])} for item in cursor.fetchall()]
        cursor.execute("""SELECT column_name FROM information_schema.columns
                          WHERE table_schema=current_schema()
                            AND table_name='agent_harness_step_budget'
                            AND column_name IN ('max_iterations','max_retries','max_evidence_bytes','max_concurrency','estimated_cost_micros')""")
        extended_budget = len(cursor.fetchall()) == 5
        budget_fields = ("step_key,max_rounds,max_iterations,max_retries,model_calls,tool_calls,http_calls,"
                         "search_calls,scrape_calls,input_tokens,output_tokens,max_parallelism,max_runtime_seconds,"
                         "max_evidence_bytes,max_concurrency,estimated_cost_micros,enforcement") if extended_budget else (
                         "step_key,max_rounds,model_calls,tool_calls,http_calls,search_calls,scrape_calls,"
                         "input_tokens,output_tokens,max_parallelism,max_runtime_seconds,enforcement")
        cursor.execute(f"SELECT {budget_fields} FROM agent_harness_step_budget "
                       "WHERE harness_contract_id=%s ORDER BY step_key", (row['harness_contract_id'],))
        contract['stepBudgets']=[dict(item) for item in cursor.fetchall()]
        return row['agent_layer'], row['runtime_kind'], contract
    def load_profile(self, agent_key: str, version: str | None = None):
        from .domain import Profile, Step, ToolBinding
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT agent_key,display_name,current_version,system_prompt,status FROM agent_definition WHERE agent_key=%s",(agent_key,)); a=self._one(c)
                if not a: raise ValueError(f"unknown agent: {agent_key}")
                if a.get('status') in {'disabled','inactive','retired'}: raise ValueError('agent is disabled')
                version=version or a['current_version']
                if version != a['current_version']:
                    raise ValueError('historical profile creation is unavailable; resume an existing snapshot instead')
                c.execute("SELECT step_id,step_key,name,description,sequence_no,parallel_group,required,retry_limit,input_contract,output_contract,dependencies_json FROM agent_step WHERE agent_key=%s ORDER BY sequence_no",(agent_key,)); steps=[]
                for r in c.fetchall():
                    d=dict(r); c.execute("SELECT tool_key FROM agent_step_tool WHERE step_id=%s ORDER BY tool_key",(d['step_id'],)); d['tools']=tuple(x['tool_key'] for x in c.fetchall()); steps.append(Step(d['step_id'],d['step_key'],d['name'],d['sequence_no'],d['parallel_group'],bool(d['required']),d['retry_limit'],d['tools'],as_json(d.get('input_contract')),as_json(d.get('output_contract')),tuple(as_json(d.get('dependencies_json')) or ()),d.get('description','')))
                c.execute("""SELECT column_name FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name='tool_version'
                    AND column_name IN ('execution_transport','adapter_version')""")
                transport_columns = {item['column_name'] for item in c.fetchall()}
                version_transport = 'COALESCE(v.execution_transport,t.protocol)' if 'execution_transport' in transport_columns else 't.protocol'
                explicit_transport = 'v.execution_transport' if 'execution_transport' in transport_columns else 'NULL::text'
                version_adapter = 'v.adapter_version' if 'adapter_version' in transport_columns else 'NULL::text'
                c.execute(f"""SELECT b.tool_key,b.tool_version,b.enabled,b.approval_required,b.config_json,
                             CASE WHEN v.version IS NOT NULL THEN v.input_schema ELSE t.input_schema END AS input_schema,
                             CASE WHEN v.version IS NOT NULL THEN v.output_schema ELSE t.output_schema END AS output_schema,
                             t.risk_level,t.access_mode,{version_transport} AS protocol,
                             {explicit_transport} AS execution_transport,{version_adapter} AS adapter_version,
                             t.status AS registry_status,v.status AS version_status,
                             CASE WHEN v.version IS NOT NULL THEN v.endpoint ELSE t.endpoint END AS endpoint
                             FROM agent_tool_binding b JOIN tool_registry t ON t.tool_key=b.tool_key
                             LEFT JOIN tool_version v ON v.tool_key=b.tool_key AND v.version=b.tool_version
                             WHERE b.agent_key=%s""",(agent_key,)); tools={}
                for r in c.fetchall():
                    cfg=as_json(r.get('config_json'))
                    if r.get('execution_transport'):
                        cfg = {**cfg, 'executionTransport': r['execution_transport']}
                    if r.get('adapter_version'):
                        cfg = {**cfg, 'adapterVersion': r['adapter_version']}
                    validate_credential_references(cfg)
                    enabled = bool(r['enabled']) and r.get('registry_status') not in {'disabled','inactive','retired'} and r.get('version_status') not in {'disabled','inactive','retired'}
                    tools[r['tool_key']]=ToolBinding(r['tool_key'],r['tool_version'],enabled,bool(r['approval_required']),as_json(r['input_schema']),as_json(r['output_schema']),int(cfg.get('timeoutSeconds',60)),int(cfg.get('retryLimit',2)),r.get('risk_level') or 'medium',r.get('access_mode') or 'read_only',r.get('protocol') or 'python',r.get('endpoint'),cfg)
                def one(sql): c.execute(sql,(agent_key,)); return self._one(c) or {}
                conv=one("SELECT completion_conditions_json,allowed_terminal_states_json,max_tool_calls,max_model_calls,max_runtime_seconds,no_new_finding_rounds FROM agent_convergence_policy WHERE agent_key=%s")
                out=one("SELECT schema_json,required_evidence,required_assumptions,required_confidence FROM agent_output_contract WHERE agent_key=%s")
                ev=one("SELECT minimum_evidence_count,require_source_uri,require_data_timestamp,allow_inference,inference_disclosure_required,conflict_action FROM agent_evidence_policy WHERE agent_key=%s")
                res=one("SELECT max_parallel_tools,max_retries_per_tool,permitted_risk_levels_json FROM agent_resource_policy WHERE agent_key=%s")
                appr=one("SELECT approval_points_json,approver_roles_json FROM agent_approval_policy WHERE agent_key=%s")
                model=one("SELECT model_provider,model_name,temperature,max_output_tokens,structured_output_required,planner_enabled,prompt_version,config_json FROM agent_model_policy WHERE agent_key=%s")
                model['config_json']=as_json(model.get('config_json'))
                source=deepcopy(model['config_json'].get('sourcePolicy', {}))
                document_keys = model['config_json'].get('documentSourceKeys')
                if document_keys is not None:
                    if not isinstance(document_keys, list) or len(document_keys) > 100 or any(
                            not isinstance(key, str) or not key for key in document_keys):
                        raise ValueError('invalid-document-source-keys')
                    c.execute('''SELECT source_key,connector,root_uri,enabled,read_only,policy_version,config_json
                        FROM data_source_registry WHERE source_key=ANY(%s) ORDER BY source_key''', (document_keys,))
                    documents = [dict(item) for item in c.fetchall()]
                    if {item['source_key'] for item in documents} != set(document_keys) or any(
                            not item['enabled'] or not item['read_only'] or item['connector'] != 'files' for item in documents):
                        raise ValueError('document-source-unavailable')
                    source['documentSources'] = documents
                scope=one("SELECT responsibility_json,input_entities_json,output_entities_json,allowed_write_json,forbidden_actions_json FROM agent_scope WHERE agent_key=%s")
                for key,value in scope.items(): scope[key]=as_json(value)
                obs=one("SELECT trace_enabled,capture_tool_inputs,capture_tool_outputs,retain_days,emit_step_events,emit_metrics,policy_json FROM agent_observability_policy WHERE agent_key=%s")
                obs['policy_json']=as_json(obs.get('policy_json'))
                c.execute("SELECT rule_id,rule_type,expression,severity FROM agent_validation_rule WHERE agent_key=%s ORDER BY rule_id", (agent_key,))
                rules=tuple(dict(r) for r in c.fetchall())
                c.execute("SELECT loop_id,trigger_condition,return_step_key,max_iterations,no_progress_limit,stop_action FROM agent_loop_rule WHERE agent_key=%s",(agent_key,)); loops=tuple(dict(r) for r in c.fetchall())
                agent_layer, runtime_kind, harness = self._load_harness_contract(c, agent_key, version)
                validate_credential_references({'model':model,'scope':scope,'source':source,'observability':obs})
                profile = Profile(agent_key,version,a['display_name'],tuple(steps),tools,conv,out,ev,res,appr,loops,
                               a.get('system_prompt') or '', model, scope, source, rules, obs, {},
                               agent_layer, runtime_kind, harness)
                from .execution_baseline import build_baseline, profile_document, read_baseline
                from .skills import current_manifest, reference
                skills = current_manifest(c,agent_key,version)
                graph = build_baseline(agent_key, profile_document(profile),
                    {**harness,'skillBindings':[reference(b) for b in skills.get('bindings',[])]})
                from .goal_contract import current
                return replace(profile, execution_baseline=read_baseline(c, agent_key, graph),
                               goal_contract=current(c, agent_key),skill_manifest=skills)
    def load_profile_snapshot(self, run_id: str):
        """Rehydrate the immutable profile captured when a run was created."""
        from .domain import Profile, Step, ToolBinding
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT profile_json,profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s", (run_id,))
                row = c.fetchone()
        if not row:
            raise ValueError(f"profile snapshot not found for run: {run_id}")
        payload = row['profile_json'] if isinstance(row, dict) else row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()
        if digest != row['profile_hash']:
            raise ValueError('profile snapshot hash mismatch')
        baseline = payload.get('executionBaseline') or {}
        if baseline:
            from .execution_baseline import graph_hash
            if graph_hash(baseline['graph']) != baseline['graphHash']:
                raise ValueError('execution baseline snapshot hash mismatch')
        from .skills import validate_manifest, validate_baseline_bindings
        skills = validate_manifest(payload.get('skillManifest') or {},payload['agentKey'],payload['version'])
        validate_baseline_bindings(skills,baseline)
        def step_with_dependencies(item):
            value = item.get('dependencies', ())
            if isinstance(value, str):
                value = as_json(value)
            if not isinstance(value, (list, tuple)):
                value = ()
            return Step(**{**item, 'dependencies': tuple(value)})
        steps = tuple(step_with_dependencies(item) for item in payload.get('steps', []))
        tools = {key: ToolBinding(**item) for key, item in payload.get('tools', {}).items()}
        return Profile(payload['agentKey'], payload['version'], payload.get('displayName', payload['agentKey']), steps, tools, payload.get('convergence', {}), payload.get('outputContract', {}), payload.get('evidencePolicy', {}), payload.get('resourcePolicy', {}), payload.get('approvalPolicy', {}), tuple(payload.get('loopRules', [])),
                       payload.get('systemPrompt', ''), payload.get('modelPolicy', {}), payload.get('scopePolicy', {}),
                       payload.get('sourcePolicy', {}), tuple(payload.get('validationRules', [])), payload.get('observabilityPolicy', {}),
                       payload.get('capabilityGraph', {}), payload.get('agentLayer', 'application'),
                       payload.get('runtimeKind', 'llm_loop'), payload.get('harnessContract', {}), baseline,
                       payload.get('reasoningPolicy', {}), payload.get('goalContract', {}),skills)
    @staticmethod
    def _hash_json(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

    def _harness_snapshot(self, profile, profile_hash, snapshot):
        contract=profile.harness_contract
        if not contract:
            return None
        plan_hash=self._hash_json(snapshot['steps'])
        graph=profile.capability_graph or {}
        graph_hash=graph.get('graphHash') if isinstance(graph,dict) else None
        edges=contract.get('compositionEdges', [])
        composition_hash=self._hash_json(edges) if edges else None
        value={
            'harnessContractId': contract['harnessContractId'],
            'contractHash': contract['contractHash'], 'profileHash': profile_hash,
            'planHash': plan_hash, 'capabilityGraphHash': graph_hash,
            'compositionGraphHash': composition_hash,
            'effectiveTools': contract.get('toolGrants', []),
            'effectiveDataGrants': contract.get('dataGrants', []),
            'effectiveNetworkGrants': contract.get('networkGrants', []),
            'effectiveChildGrants': edges,
            'effectiveLimits': {'stepBudgets': contract.get('stepBudgets', [])}
        }
        if profile.execution_baseline:
            value['baselineHash'] = profile.execution_baseline['graphHash']
        value['snapshotHash']=self._hash_json(value)
        return value

    def create_run(self, profile, input_data: dict[str,Any], *, _cursor=None):
        rid=new_id('run'); snapshot={'agentKey':profile.agent_key,'version':profile.version,'displayName':profile.display_name,'steps':[asdict(s) for s in profile.steps],'tools':{k:asdict(v) for k,v in profile.tools.items()},'convergence':profile.convergence,'outputContract':profile.output_contract,'evidencePolicy':profile.evidence_policy,'resourcePolicy':profile.resource_policy,'approvalPolicy':profile.approval_policy,'loopRules':list(profile.loop_rules),
                                   'systemPrompt':profile.system_prompt,'modelPolicy':profile.model_policy,'scopePolicy':profile.scope_policy,
                                   'sourcePolicy':profile.source_policy,'validationRules':list(profile.validation_rules),'observabilityPolicy':profile.observability_policy,
                                   'capabilityGraph':deepcopy(profile.capability_graph),
                                   'agentLayer':profile.agent_layer,'runtimeKind':profile.runtime_kind,
                                   'harnessContract':deepcopy(profile.harness_contract),
                                   'executionBaseline':deepcopy(profile.execution_baseline),
                                   'reasoningPolicy':deepcopy(profile.reasoning_policy),
                                   'goalContract':deepcopy(profile.goal_contract)}
        if profile.skill_manifest:
            from .skills import validate_manifest, validate_baseline_bindings
            snapshot['skillManifest'] = deepcopy(validate_manifest(profile.skill_manifest,profile.agent_key,profile.version))
            validate_baseline_bindings(snapshot['skillManifest'],profile.execution_baseline)
        validate_credential_references(snapshot)
        def insert(c):
            # Every Run admission, including direct handoffs, converges here.
            # Existing Run loading/execution never enters this gate.
            from .uav_execution_contracts import require_ready
            require_ready(profile, c, lock=True)
            for binding in (snapshot.get('skillManifest') or {}).get('bindings',[]):
                c.execute('''SELECT content_hash FROM agent_step_skill_binding WHERE agent_key=%s
                    AND profile_version=%s AND step_key=%s AND stage=%s AND skill_key=%s AND skill_version=%s''',
                    (profile.agent_key,profile.version,binding['stepKey'],binding['stage'],binding['skillKey'],binding['version']))
                revision = c.fetchone()
                if not revision or revision['content_hash'] != binding['contentHash']:
                    raise ValueError('skill-revision-snapshot-mismatch')
            from .goal_contract import installed as goals_installed, current as goal_current
            has_goals = goals_installed(c)
            if has_goals:
                snapshot['goalContract'] = snapshot['goalContract'] or goal_current(c, profile.agent_key)
                contract = snapshot['goalContract']
                if not contract or contract.get('agentKey') != profile.agent_key:
                    raise ValueError('published goal contract required for new Runs')
                c.execute('SELECT content_hash FROM agent_goal_contract_revision WHERE contract_id=%s AND agent_key=%s',
                          (contract['contractId'], profile.agent_key))
                goal_revision = c.fetchone()
                if not goal_revision or goal_revision['content_hash'] != contract['contentHash'] or self._hash_json(contract['document']) != contract['contentHash']:
                    raise ValueError('goal contract snapshot mismatch')
            harness_available=self._harness_installed(c)
            if harness_available and not profile.harness_contract:
                raise ValueError('published Harness contract is required for new Runs')
            from .execution_baseline import baseline_installed
            has_baseline = baseline_installed(c)
            if has_baseline and (not profile.execution_baseline.get('baselineId') or
                                 not profile.execution_baseline.get('validation', {}).get('accepted')):
                raise ValueError('a registered, valid execution baseline is required for new Runs')
            if not snapshot['capabilityGraph'] and (profile.model_policy.get('config_json', {}).get('capabilityGraphPath') or profile.agent_key in FUTURE_AGENT_KEYS):
                from .capabilities import graph_snapshot
                snapshot['capabilityGraph'] = graph_snapshot(c, profile.model_policy['config_json'], profile.agent_key)
            raw=json.dumps(snapshot,sort_keys=True,default=str); ph=hashlib.sha256(raw.encode()).hexdigest()
            harness_snapshot=self._harness_snapshot(profile, ph, snapshot) if harness_available else None
            if harness_snapshot:
                c.execute("INSERT INTO agent_run(run_id,agent_key,agent_version,status,input_json,harness_contract_id,agent_layer,harness_snapshot_status) VALUES(%s,%s,%s,'created',%s,%s,%s,'complete')",
                          (rid,profile.agent_key,profile.version,Json(input_data),profile.harness_contract['harnessContractId'],profile.agent_layer))
            else:
                c.execute("INSERT INTO agent_run(run_id,agent_key,agent_version,status,input_json) VALUES(%s,%s,%s,'created',%s)",(rid,profile.agent_key,profile.version,Json(input_data)))
            if has_baseline:
                c.execute('UPDATE agent_run SET baseline_id=%s,baseline_hash=%s WHERE run_id=%s',
                          (profile.execution_baseline['baselineId'], profile.execution_baseline['graphHash'], rid))
            c.execute("INSERT INTO agent_run_profile_snapshot(run_id,profile_json,profile_hash) VALUES(%s,%s,%s)",(rid,Json(snapshot),ph))
            from .connectors.runtime import freeze_cursor
            freeze_cursor(c, rid, profile)
            from .mission_budget import bind as bind_mission
            bind_mission(c, rid, input_data)
            from .task_intent import bind
            bind(c, rid, profile, input_data)
            from .run_origin import record_origin
            record_origin(c, rid, input_data)
            if has_goals:
                from .goal_contract import bind as bind_goal
                bind_goal(c, rid, profile, input_data, snapshot['goalContract'])
            if profile.reasoning_policy:
                self.initialize_reasoning_run(rid, profile.reasoning_policy, cursor=c)
            if harness_snapshot:
                baseline_column = ',baseline_hash' if has_baseline else ''
                baseline_placeholder = ',%s' if has_baseline else ''
                baseline_values = (profile.execution_baseline['graphHash'],) if has_baseline else ()
                c.execute(f"""INSERT INTO agent_run_harness_snapshot(
                    run_id,harness_contract_id,contract_hash,profile_hash,plan_hash,
                    capability_graph_hash,composition_graph_hash,effective_tools_json,
                    effective_data_grants_json,effective_network_grants_json,
                    effective_child_grants_json,effective_limits_json,snapshot_hash{baseline_column})
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s{baseline_placeholder})""",
                    (rid,harness_snapshot['harnessContractId'],harness_snapshot['contractHash'],
                     ph,harness_snapshot['planHash'],harness_snapshot['capabilityGraphHash'],
                     harness_snapshot['compositionGraphHash'],Json(harness_snapshot['effectiveTools']),
                     Json(harness_snapshot['effectiveDataGrants']),Json(harness_snapshot['effectiveNetworkGrants']),
                     Json(harness_snapshot['effectiveChildGrants']),Json(harness_snapshot['effectiveLimits']),
                     harness_snapshot['snapshotHash'], *baseline_values))
            c.execute("INSERT INTO agent_run_state(run_id,state_json) VALUES(%s,%s)",(rid,Json({'steps':{},'findings':[],'evidence':[]})))
            if self._context_installed(c):
                initial = {'revision': 0, 'assignments': {}, 'status': 'created',
                           'gaps': [], 'conflicts': [], 'budget': {}}
                c.execute("""INSERT INTO agent_collaboration_revision(
                    root_run_id,revision,state_json,state_hash,actor_run_id)
                    VALUES(%s,0,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (rid, Json(initial), self._hash_json(initial), rid))
            self.event(c,rid,'run.created',None,{'agentKey':profile.agent_key,'agentLayer':profile.agent_layer,
                                                 'harnessSnapshotHash':harness_snapshot['snapshotHash'] if harness_snapshot else None})
        if _cursor is not None:
            insert(_cursor)
        else:
            with self.connection_factory() as con:
                with con.cursor() as c:
                    insert(c)
        return rid
    def start_skill_invocation(self,ctx,binding,**kwargs):
        from .skills import SkillRepository
        return SkillRepository(self.connection_factory).start(ctx,binding,**kwargs)

    def finish_skill_invocation(self,run_id,invocation_id,status,**kwargs):
        from .skills import SkillRepository
        return SkillRepository(self.connection_factory).finish(run_id,invocation_id,status,**kwargs)

    def list_skill_invocations(self,run_id):
        from .skills import SkillRepository
        return SkillRepository(self.connection_factory).list(run_id)

    def assert_task_dispatch(self, run_id, **kwargs):
        from .task_intent import check
        return check(self, run_id, **kwargs)

    def task_intent(self, run_id):
        from .task_intent import read
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                return read(cursor, run_id)

    def recover_safe_action_children(self, run_id):
        """Resume only deterministic children that never dispatched a call.

        Historical failures/events and all budget charges remain intact. An
        uncertain invocation is never retried by this infrastructure repair.
        """
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT to_regclass(current_schema() || '.agent_action_invocation') AS name")
                if not c.fetchone()['name']:
                    return
                c.execute('''SELECT r.run_id,r.error_message,r.model_call_count,r.tool_call_count,p.profile_json
                    FROM agent_action_invocation a JOIN agent_run r ON r.run_id=a.child_run_id
                    JOIN agent_run_profile_snapshot p ON p.run_id=r.run_id
                    WHERE a.run_id=%s AND r.status='failed' ''',(run_id,))
                candidates = list(c.fetchall())
        for child in candidates:
            if ('worker lease lost or missing' not in str(child['error_message']) or
                child['model_call_count'] or child['tool_call_count']):
                continue
            snapshot = child['profile_json']
            if snapshot.get('runtimeKind') != 'deterministic_action' or any(
                b.get('enabled') and b.get('access_mode') != 'read_only' for b in snapshot.get('tools',{}).values()):
                continue
            with self.connection_factory() as con:
                with con.cursor() as c:
                    c.execute("SELECT 1 FROM agent_invocation_slot WHERE run_id=%s AND status IN ('unknown','running')",(child['run_id'],))
                    if c.fetchone():
                        continue
            self.assert_task_dispatch(child['run_id'],operation='analyze')
            self.transition(child['run_id'],'queued',error='infrastructure retry; prior failure retained in events')
            self.append_event(child['run_id'],'run.infrastructure-recovered',detail={
                'parentRunId':run_id,'reason':'lease lost before any model/tool dispatch','budgetsReset':False})

    def can_continue_infrastructure_step(self, run_id, row):
        error = str(row.get('error_message') or '')
        if not ('worker lease lost or missing' in error or 'Action child Run did not complete: failed' in error):
            return False
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute('''SELECT 1 FROM agent_tool_call WHERE step_run_id=%s
                    UNION ALL SELECT 1 FROM agent_model_call WHERE step_run_id=%s LIMIT 1''',
                    (row['step_run_id'],row['step_run_id']))
                if c.fetchone():
                    return False
                if 'worker lease lost or missing' in error:
                    return True
                c.execute('''SELECT 1 FROM agent_action_invocation a JOIN agent_run_event e ON e.run_id=a.child_run_id
                    WHERE a.run_id=%s AND a.parent_step_key=%s AND e.event_type='run.infrastructure-recovered' LIMIT 1''',
                    (run_id,row['step_key']))
                return bool(c.fetchone())

    def event(self,c,rid,typ,step,detail): c.execute("INSERT INTO agent_run_event(run_id,event_type,step_key,detail_json) VALUES(%s,%s,%s,%s)",(rid,typ,step,Json(detail)))
    def transition(self,rid,status,step=None,error=None,output=None,recovery=False):
        from .state_machine import StateMachine
        target=status.value if hasattr(status,'value') else status
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute('SELECT status FROM agent_run WHERE run_id=%s FOR UPDATE',(rid,)); row=c.fetchone()
                if not row: raise ValueError('run not found')
                if recovery:
                    if target != 'queued': raise ValueError('recovery target must be queued')
                    StateMachine().validate_recovery(row['status'])
                else: StateMachine().validate(row['status'],target)
                c.execute("UPDATE agent_run SET status=%s,current_step_key=COALESCE(%s,current_step_key),error_message=%s,started_at=COALESCE(started_at,CASE WHEN %s IN ('running','planning') THEN now() ELSE started_at END),updated_at=now(),completed_at=CASE WHEN %s IN ('completed','completed-with-gaps','requires-human-review','blocked','failed','cancelled') THEN now() ELSE NULL END,output_json=COALESCE(%s,output_json) WHERE run_id=%s",(target,step,error,target,target,Json(output) if output is not None else None,rid))
                self.event(c,rid,'run.status',step,{'status':target,'error':error,'recovery':recovery})
                if output is not None:
                    digest=hashlib.sha256(json.dumps(output,sort_keys=True,default=str).encode()).hexdigest()
                    c.execute('INSERT INTO agent_run_artifact(artifact_id,run_id,artifact_type,content_json,content_hash) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(run_id,content_hash) DO NOTHING',(new_id('artifact'),rid,'agent-output',Json(output),digest))
                    self.event(c,rid,'output.persisted',step,{'contentHash':digest})
                from .evolution_repository import capture_terminal
                from .goal_contract import capture_terminal as capture_goal_terminal
                capture_goal_terminal(c,rid,target,output)
                capture_terminal(c,rid,target,output)
    @contextmanager
    def execution_lock(self,run_id):
        """A session advisory lock prevents two workers from executing one run."""
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',(run_id,))
                if not c.fetchone()['locked']: raise RuntimeError('run already has an active worker')
            try: yield
            finally:
                with con.cursor() as c: c.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(run_id,))
    def save_state(self,ctx):
        with self.connection_factory() as con:
            with con.cursor() as c: c.execute("UPDATE agent_run_state SET state_json=%s,revision=revision+1,updated_at=now() WHERE run_id=%s",(Json(ctx.state),ctx.run_id))
    def set_iteration(self, run_id, iteration):
        with self.connection_factory() as con:
            with con.cursor() as c: c.execute("UPDATE agent_run SET iteration=%s,updated_at=now() WHERE run_id=%s",(iteration,run_id))
    def set_output(self, run_id, output):
        with self.connection_factory() as con:
            with con.cursor() as c: c.execute("UPDATE agent_run SET output_json=%s,updated_at=now() WHERE run_id=%s",(Json(output),run_id))
    def add_artifact(self, run_id, artifact_type, content, step_run_id=None):
        raw=json.dumps(content,sort_keys=True,default=str); digest=hashlib.sha256(raw.encode()).hexdigest(); aid=new_id('artifact')
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("INSERT INTO agent_run_artifact(artifact_id,run_id,step_run_id,artifact_type,content_json,content_hash) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id,content_hash) DO NOTHING RETURNING artifact_id",(aid,run_id,step_run_id,artifact_type,Json(content),digest))
                inserted=c.fetchone()
                if inserted:
                    aid=inserted['artifact_id']
                else:
                    c.execute('SELECT artifact_id FROM agent_run_artifact WHERE run_id=%s AND content_hash=%s',(run_id,digest))
                    existing=c.fetchone()
                    if not existing: raise RuntimeError('artifact conflict did not resolve to a saved record')
                    aid=existing['artifact_id']
        return aid

    def _context_installed(self, cursor):
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_context_snapshot') AS table_name")
        row = cursor.fetchone()
        return bool(row and row.get('table_name'))

    def create_context_snapshot(self, run_id, manifest, *, operation_key, step_run_id=None,
                               role_key=None, node_key=None, iteration=0):
        """Persist one immutable invocation context when migration 055 exists.

        The method is optional for older databases and test repositories.  A
        retry with the same operation key must return the original context only
        when its manifest hash is identical.
        """
        if not isinstance(manifest, dict) or not operation_key:
            raise ValueError('context manifest and operation key are required')
        manifest_hash = manifest.get('manifestHash')
        if not isinstance(manifest_hash, str) or len(manifest_hash) != 64:
            raise ValueError('context manifest hash is required')
        with self.connection_factory() as con:
            with con.cursor() as c:
                if not self._context_installed(c):
                    return None
                c.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s", (run_id,))
                member = c.fetchone()
                root_run_id = member['root_run_id'] if member else run_id
                # Serialize chain-head selection with the root Run.  This
                # keeps concurrent workers from creating two contexts that
                # both claim the same predecessor.
                c.execute("SELECT run_id FROM agent_run WHERE run_id=%s FOR UPDATE", (root_run_id,))
                c.execute("""SELECT column_name FROM information_schema.columns
                             WHERE table_schema=current_schema()
                               AND table_name='agent_context_snapshot'
                               AND column_name IN ('previous_chain_hash','chain_hash')""")
                chain_columns = {row['column_name'] for row in c.fetchall()}
                c.execute("SELECT context_id,manifest_hash FROM agent_context_snapshot "
                          "WHERE run_id=%s AND operation_key=%s", (run_id, operation_key))
                previous = c.fetchone()
                if previous:
                    if previous['manifest_hash'] != manifest_hash:
                        raise ValueError('operation key already identifies a different context')
                    return previous['context_id']
                context_id = new_id('context')
                task_hash = self._hash_json(manifest.get('task', {}))
                previous_chain = None
                chain = None
                if {'previous_chain_hash', 'chain_hash'} <= chain_columns:
                    c.execute("""SELECT chain_hash FROM agent_context_snapshot
                                  WHERE root_run_id=%s AND chain_hash IS NOT NULL
                               ORDER BY created_at DESC,context_id DESC LIMIT 1""", (root_run_id,))
                    row = c.fetchone()
                    previous_chain = row['chain_hash'] if row else None
                    chain = self._hash_json({'previousChainHash': previous_chain,
                                             'manifestHash': manifest_hash,
                                             'operationKey': operation_key})
                names = """context_id,run_id,root_run_id,step_run_id,operation_key,role_key,node_key,
                    iteration,profile_hash,baseline_hash,task_hash,w_revision,policy_json,
                    manifest_json,manifest_hash"""
                values = [context_id, run_id, root_run_id, step_run_id, operation_key,
                          role_key or manifest.get('roleKey', 'agent'), node_key or manifest.get('nodeKey', 'run'),
                          int(iteration), manifest.get('profileHash', ''), manifest.get('baselineHash'),
                          task_hash, manifest.get('coordination', {}).get('revision'),
                          Json(manifest.get('policy', {})), Json(manifest), manifest_hash]
                if {'previous_chain_hash', 'chain_hash'} <= chain_columns:
                    names += ",previous_chain_hash,chain_hash"
                    values.extend([previous_chain, chain])
                names += ",status"
                values.append('frozen')
                c.execute(f"""INSERT INTO agent_context_snapshot(
                    {names}) VALUES({','.join(['%s'] * len(values))})""", values)
                c.execute("""INSERT INTO agent_invocation_slot(
                    run_id,operation_key,node_key,iteration,context_id,schema_hash,status)
                    VALUES(%s,%s,%s,%s,%s,%s,'planned') ON CONFLICT DO NOTHING""",
                    (run_id, operation_key, node_key or manifest.get('nodeKey', 'run'),
                     int(iteration), context_id, manifest.get('schemaHash', '')))
                for item in manifest.get('dependencies', []):
                    if not isinstance(item, dict) or not item.get('dependencyId'):
                        continue
                    c.execute("""INSERT INTO agent_context_dependency(
                        context_id,dependency_id,dependency_kind,version_ref,content_hash,
                        predicate_json,read_mode) VALUES(%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING""",
                        (context_id, item['dependencyId'], item.get('dependencyKind', 'artifact'),
                         item.get('versionRef'), item.get('contentHash'), Json(item.get('predicate'))
                         if item.get('predicate') is not None else None, item.get('readMode', 'read')))
                return context_id

    def get_context(self, context_id):
        with self.connection_factory() as con:
            with con.cursor() as c:
                if not self._context_installed(c):
                    return None
                c.execute("SELECT * FROM agent_context_snapshot WHERE context_id=%s", (context_id,))
                row = self._one(c)
                if row:
                    c.execute("SELECT * FROM agent_context_dependency WHERE context_id=%s "
                              "ORDER BY dependency_kind,dependency_id", (context_id,))
                    row['dependencies'] = [dict(item) for item in c.fetchall()]
                return row

    def profile_snapshot_hash(self, run_id):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s", (run_id,))
                row = c.fetchone()
                return row['profile_hash'] if row else None

    def list_contexts(self, run_id):
        with self.connection_factory() as con:
            with con.cursor() as c:
                if not self._context_installed(c):
                    return []
                c.execute("""SELECT column_name FROM information_schema.columns
                             WHERE table_schema=current_schema()
                               AND table_name='agent_context_snapshot'
                               AND column_name IN ('previous_chain_hash','chain_hash')""")
                chain_columns = {row['column_name'] for row in c.fetchall()}
                chain_select = ("s.previous_chain_hash,s.chain_hash"
                                if {'previous_chain_hash', 'chain_hash'} <= chain_columns
                                else "NULL AS previous_chain_hash,NULL AS chain_hash")
                c.execute(f"""SELECT s.context_id,s.run_id,s.step_run_id,s.operation_key,s.role_key,s.node_key,s.iteration,
                                  s.profile_hash,s.baseline_hash,s.task_hash,s.w_revision,s.manifest_hash,s.status,
                                  s.created_at,s.manifest_json,{chain_select},
                                  i.status AS invocation_status,i.attempt_count,i.result_artifact_id
                             FROM agent_context_snapshot s
                             LEFT JOIN agent_invocation_slot i
                               ON i.run_id=s.run_id AND i.operation_key=s.operation_key
                            WHERE s.run_id=%s ORDER BY s.created_at,s.context_id""", (run_id,))
                return [dict(item) for item in c.fetchall()]

    def invocation_slot(self, run_id, operation_key):
        with self.connection_factory() as con:
            with con.cursor() as c:
                if not self._context_installed(c):
                    return None
                c.execute("SELECT * FROM agent_invocation_slot WHERE run_id=%s AND operation_key=%s",
                          (run_id, operation_key))
                return self._one(c)

    def collaboration(self, run_id):
        with self.connection_factory() as con:
            with con.cursor() as c:
                if not self._context_installed(c):
                    return {'rootRunId': run_id, 'revision': None, 'state': None, 'proposals': []}
                c.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s", (run_id,))
                member = c.fetchone(); root = member['root_run_id'] if member else run_id
                c.execute("SELECT * FROM agent_collaboration_revision WHERE root_run_id=%s "
                          "ORDER BY revision DESC LIMIT 1", (root,))
                revision = self._one(c)
                c.execute("SELECT * FROM agent_collaboration_proposal WHERE root_run_id=%s "
                          "ORDER BY created_at,proposal_id", (root,))
                proposals = [dict(item) for item in c.fetchall()]
                return {'rootRunId': root, 'revision': revision, 'proposals': proposals}

    def propose_collaboration(self, run_id, *, role_key, operation_key, expected_revision, patch):
        """Apply a bounded shared-state proposal with an optimistic CAS fence."""
        if not isinstance(patch, dict) or not patch:
            raise ValueError('collaboration patch must be a non-empty object')
        allowed = {'assignments', 'status', 'gaps', 'conflicts', 'budget', 'iteration', 'blocked', 'lastEvent'}
        if set(patch) - allowed:
            raise ValueError('collaboration patch contains a non-shared field')
        if not isinstance(operation_key, str) or not operation_key.strip():
            raise ValueError('collaboration operation key is required')
        proposal_id = new_id('proposal')
        with self.connection_factory() as con:
            with con.cursor() as c:
                if not self._context_installed(c):
                    raise ValueError('context migration is not installed')
                c.execute("SELECT root_run_id FROM agent_run_budget_member WHERE run_id=%s", (run_id,))
                member = c.fetchone(); root = member['root_run_id'] if member else run_id
                c.execute("SELECT * FROM agent_collaboration_revision WHERE root_run_id=%s "
                          "ORDER BY revision DESC LIMIT 1 FOR UPDATE", (root,))
                current = self._one(c)
                current_revision = int(current['revision']) if current else 0
                if current_revision != int(expected_revision):
                    c.execute("""INSERT INTO agent_collaboration_proposal(
                        proposal_id,root_run_id,expected_revision,actor_run_id,role_key,
                        operation_key,patch_json,decision) VALUES(%s,%s,%s,%s,%s,%s,%s,'conflict')""",
                        (proposal_id, root, expected_revision, run_id, role_key, operation_key, Json(patch)))
                    return {'proposalId': proposal_id, 'rootRunId': root, 'revision': current_revision,
                            'decision': 'conflict'}
                state = deepcopy(current['state_json']) if current else {}
                state.update(deepcopy(patch))
                next_revision = current_revision + 1
                c.execute("""INSERT INTO agent_collaboration_proposal(
                    proposal_id,root_run_id,expected_revision,actor_run_id,role_key,
                    operation_key,patch_json,decision) VALUES(%s,%s,%s,%s,%s,%s,%s,'accepted')""",
                    (proposal_id, root, expected_revision, run_id, role_key, operation_key, Json(patch)))
                c.execute("""INSERT INTO agent_collaboration_revision(
                    root_run_id,revision,state_json,state_hash,actor_run_id)
                    VALUES(%s,%s,%s,%s,%s)""",
                    (root, next_revision, Json(state), self._hash_json(state), run_id))
                c.execute("UPDATE agent_run SET collaboration_revision=%s,updated_at=now() WHERE run_id=%s",
                          (next_revision, root))
                self.event(c, run_id, 'collaboration.revision', None,
                           {'rootRunId': root, 'revision': next_revision, 'proposalId': proposal_id})
                return {'proposalId': proposal_id, 'rootRunId': root, 'revision': next_revision,
                        'decision': 'accepted', 'stateHash': self._hash_json(state)}

    def update_invocation_slot(self, run_id, operation_key, status, result_artifact_id=None, *, increment=False):
        if status not in {'planned', 'running', 'succeeded', 'failed', 'unknown'}:
            raise ValueError('invalid invocation slot status')
        with self.connection_factory() as con:
            with con.cursor() as c:
                if not self._context_installed(c):
                    return
                c.execute("""UPDATE agent_invocation_slot SET status=%s,
                          result_artifact_id=COALESCE(%s,result_artifact_id),
                          attempt_count=attempt_count+%s WHERE run_id=%s AND operation_key=%s""",
                          (status, result_artifact_id, 1 if increment else 0, run_id, operation_key))
    def step(self,rid,step,status,input_data=None,output=None,attempt=0,error=None):
        sid=new_id('step');
        with self.connection_factory() as con:
            with con.cursor() as c:
                if status == 'running':
                    c.execute("INSERT INTO agent_step_run(step_run_id,run_id,step_key,status,attempt,input_json,output_json,error_message,started_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,now())",(sid,rid,step,status,attempt,Json(input_data or {}),Json(output) if output is not None else None,error))
                else:
                    c.execute("UPDATE agent_step_run SET status=%s,output_json=%s,error_message=%s,finished_at=now() WHERE run_id=%s AND step_key=%s AND attempt=%s",(status,Json(output) if output is not None else None,error,rid,step,attempt))
                    if c.rowcount == 0:
                        c.execute("INSERT INTO agent_step_run(step_run_id,run_id,step_key,status,attempt,input_json,output_json,error_message,finished_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,now())",(sid,rid,step,status,attempt,Json(input_data or {}),Json(output) if output is not None else None,error))
                self.event(c,rid,'step.status',step,{'status':status,'attempt':attempt,'error':error})
        return sid
    def next_step_attempt(self, rid, step):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT COALESCE(MAX(attempt),-1)+1 AS attempt FROM agent_step_run WHERE run_id=%s AND step_key=%s", (rid, step))
                return int(c.fetchone()['attempt'])
    def mark_interrupted_steps(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("UPDATE agent_step_run SET status='failed',error_message='worker interrupted; recover with a new attempt',finished_at=now() WHERE run_id=%s AND status='running' RETURNING step_key,attempt", (rid,))
                for row in c.fetchall():
                    self.event(c, rid, 'step.interrupted', row['step_key'], {'attempt': row['attempt']})
    def tool_call(self,rid,sid,key,version,status,input_data,output=None,error=None,latency=0,retry=0,
                  context_id=None, manifest_hash=None, operation_key=None):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("""SELECT column_name FROM information_schema.columns
                            WHERE table_schema=current_schema() AND table_name='agent_tool_call'
                              AND column_name IN ('context_id','manifest_hash','operation_key')""")
                optional = {row['column_name'] for row in c.fetchall()}
                names = ['call_id','run_id','step_run_id','tool_key','tool_version','status','input_json','output_json',
                         'error_message','latency_ms','retry_count','trace_id']
                values = [new_id('call'),rid,sid,key,version,status,Json(input_data),
                          Json(output) if output is not None else None,error,latency,retry,new_id('trace')]
                for name, value in (('context_id', context_id), ('manifest_hash', manifest_hash), ('operation_key', operation_key)):
                    if name in optional:
                        names.append(name); values.append(value)
                optional_values = values[12:]
                values = values[:12]
                names.insert(12, 'started_at'); names.insert(13, 'finished_at')
                placeholders = ','.join(['%s'] * len(values))
                suffix = ','.join(['%s'] * len(optional_values))
                sql = f"INSERT INTO agent_tool_call({','.join(names)}) VALUES({placeholders},now(),now()"
                if suffix:
                    sql += ',' + suffix
                sql += ')'
                c.execute(sql, values + optional_values)
                c.execute("UPDATE agent_run SET tool_call_count=tool_call_count+1,updated_at=now() WHERE run_id=%s",(rid,))
                self.event(c,rid,'tool.call',None,{'toolKey':key,'status':status,'retryCount':retry,'latencyMs':latency})
                return values[0]
    def append_event(self, rid, event_type, step_key=None, detail=None):
        with self.connection_factory() as con:
            with con.cursor() as c: self.event(c, rid, event_type, step_key, detail or {})
    def list_steps(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_step_run WHERE run_id=%s ORDER BY created_at,step_key,attempt",(rid,)); return [dict(x) for x in c.fetchall()]
    def list_execution_delegations(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute('''SELECT a.invocation_id,a.parent_step_run_id,a.child_run_id,a.status,a.created_at,
                    r.agent_key,r.agent_version,p.profile_json->>'displayName' AS display_name
                    FROM agent_action_invocation a JOIN agent_run r ON r.run_id=a.child_run_id
                    JOIN agent_run_profile_snapshot p ON p.run_id=r.run_id
                    WHERE a.run_id=%s ORDER BY a.created_at,a.invocation_id''', (rid,))
                rows = [dict(row) for row in c.fetchall()]
                children = [row['child_run_id'] for row in rows]
                from .execution_models import model_call_metadata, tool_call_metadata
                c.execute('''SELECT call_id,run_id,step_run_id,provider_key,requested_model,actual_model,status,
                    stage,error_code,created_at FROM agent_model_call WHERE run_id=ANY(%s) ORDER BY created_at,call_id''', (children,))
                models = [model_call_metadata(row) for row in c.fetchall()]
                c.execute('''SELECT call_id,run_id,step_run_id,tool_key,tool_version,status,created_at
                    FROM agent_tool_call WHERE run_id=ANY(%s) ORDER BY created_at,call_id''', (children,))
                tools = [tool_call_metadata(row) for row in c.fetchall()]
                for row in rows:
                    row['llmCalls'] = [call for call in models if call['runId'] == row['child_run_id']]
                    row['toolCalls'] = [call for call in tools if call['runId'] == row['child_run_id']]
                return rows

    def list_step_summaries(self, rid):
        """Return the bounded projection used by the execution graph.

        Step outputs can contain complete source pages and extracted quotations.
        The graph only needs counts and a few scalar facts; returning the full
        JSON here made a single run's graph response hundreds of megabytes.
        The full payload remains available through ``list_steps`` for the
        per-node audit endpoint.
        """
        metric = """
          jsonb_build_object(
            'perspectiveCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'perspectives')='array' THEN jsonb_array_length(s.output_json::jsonb->'perspectives') ELSE 0 END,
            'hypothesisCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'hypotheses')='array' THEN jsonb_array_length(s.output_json::jsonb->'hypotheses') ELSE 0 END,
            'researchQuestionCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'researchQuestions')='array' THEN jsonb_array_length(s.output_json::jsonb->'researchQuestions') ELSE NULL END,
            'sourcePlanCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'sourcePlan')='array' THEN jsonb_array_length(s.output_json::jsonb->'sourcePlan') ELSE 0 END,
            'sourceCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'sourceBundle'->'sources')='array' THEN jsonb_array_length(s.output_json::jsonb->'sourceBundle'->'sources') ELSE 0 END,
            'searchCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'sourceBundle'->'searches')='array' THEN jsonb_array_length(s.output_json::jsonb->'sourceBundle'->'searches') ELSE 0 END,
            'evidenceCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'evidence')='array' THEN jsonb_array_length(s.output_json::jsonb->'evidence') ELSE 0 END,
            'conflictCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'conflicts')='array' THEN jsonb_array_length(s.output_json::jsonb->'conflicts') ELSE 0 END,
            'systemFindingCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'systemFindings')='array' THEN jsonb_array_length(s.output_json::jsonb->'systemFindings') ELSE 0 END,
            'perspectiveFindingCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'perspectiveFindings')='array' THEN jsonb_array_length(s.output_json::jsonb->'perspectiveFindings') ELSE 0 END,
            'auditFindingCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'auditFindings')='array' THEN jsonb_array_length(s.output_json::jsonb->'auditFindings') ELSE 0 END,
            'reportEvidenceCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'judgmentReport'->'evidence')='array' THEN jsonb_array_length(s.output_json::jsonb->'judgmentReport'->'evidence') ELSE 0 END,
            'reportAssertionCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'judgmentReport'->'ontology'->'assertions')='array' THEN jsonb_array_length(s.output_json::jsonb->'judgmentReport'->'ontology'->'assertions') ELSE 0 END,
            'publicationStatus', s.output_json::jsonb->>'publicationStatus',
            'gapCount', CASE WHEN jsonb_typeof(s.output_json::jsonb->'gaps')='array' THEN jsonb_array_length(s.output_json::jsonb->'gaps') ELSE 0 END
          )
        """
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute(f"""
                    SELECT s.step_run_id,s.run_id,s.step_key,s.status,s.attempt,
                           s.error_message,s.started_at,s.finished_at,s.created_at,
                           CASE WHEN s.output_json IS NULL THEN NULL ELSE {metric} END AS output_metrics,
                           s.output_json::jsonb->'actionInvocation' AS action_invocation,
                           COALESCE(array_agg(a.artifact_id) FILTER (WHERE a.artifact_id IS NOT NULL), '{{}}') AS artifact_ids
                      FROM agent_step_run s
                      LEFT JOIN agent_run_artifact a ON a.run_id=s.run_id AND a.step_run_id=s.step_run_id
                     WHERE s.run_id=%s
                     GROUP BY s.step_run_id
                     ORDER BY s.created_at,s.step_key,s.attempt
                """, (rid,))
                return [dict(x) for x in c.fetchall()]

    def get_step(self, rid, step_run_id):
        """Load one full step record after the operator selects a node."""
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_step_run WHERE run_id=%s AND step_run_id=%s", (rid, step_run_id))
                row = c.fetchone()
                return dict(row) if row else None
    def list_events(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_run_event WHERE run_id=%s ORDER BY event_id",(rid,)); return [dict(x) for x in c.fetchall()]
    def list_artifacts(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_run_artifact WHERE run_id=%s ORDER BY created_at",(rid,)); return [dict(x) for x in c.fetchall()]
    def list_artifact_summaries(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT artifact_id,run_id,step_run_id,artifact_type,content_hash,created_at FROM agent_run_artifact WHERE run_id=%s ORDER BY created_at", (rid,))
                return [dict(x) for x in c.fetchall()]
    def get_artifact(self, rid, artifact_id):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_run_artifact WHERE run_id=%s AND artifact_id=%s", (rid, artifact_id))
                row = c.fetchone(); return dict(row) if row else None
    def list_artifact_summaries(self, rid):
        """Return artifact metadata without loading potentially large content."""
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("""SELECT artifact_id,run_id,step_run_id,artifact_type,content_hash,created_at
                               FROM agent_run_artifact WHERE run_id=%s ORDER BY created_at""", (rid,))
                return [dict(x) for x in c.fetchall()]

    def published_memory(self, *, stream_key, limit=8, max_age_seconds=None):
        """Read only explicitly published knowledge for a context projection.

        Publication tables are optional during legacy bootstrap. Withdrawn
        versions are excluded by their latest immutable publication event.
        Returned records keep provenance and remain labelled as memory.
        """
        if not isinstance(stream_key, str) or not stream_key:
            return []
        if type(limit) is not int or not 0 <= limit <= 512:
            raise ValueError("memory limit must be between 0 and 512")
        if limit == 0:
            return []
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT to_regclass(current_schema() || '.agent_knowledge_publication') AS table_name")
                if not c.fetchone()['table_name']:
                    return []
                params = [stream_key]
                age_sql = ""
                if max_age_seconds is not None:
                    if (isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int)
                            or not 0 <= max_age_seconds <= 31_536_000):
                        raise ValueError("invalid memory age")
                    age_sql = " AND p.published_at >= now() - (%s * interval '1 second')"
                    params.append(max_age_seconds)
                params.append(limit)
                c.execute("""SELECT p.publication_id,p.stream_key,p.version,p.payload_json,
                                   p.payload_hash,p.evidence_set_hash,p.published_by,p.published_at
                              FROM agent_knowledge_publication p
                             WHERE p.stream_key=%s""" + age_sql + """ AND NOT EXISTS (
                                   SELECT 1 FROM agent_knowledge_publication_event e
                                    WHERE e.publication_id=p.publication_id
                                      AND e.event_type='withdrawn'
                                      AND e.created_at=(SELECT max(e2.created_at)
                                          FROM agent_knowledge_publication_event e2
                                         WHERE e2.publication_id=p.publication_id)
                               )
                             ORDER BY p.version DESC,p.publication_id DESC LIMIT %s""", params)
                rows = [dict(item) for item in c.fetchall()]
        result = []
        for row in rows:
            payload = row.get('payload_json') or {}
            records = payload.get('records') if isinstance(payload, dict) else []
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                result.append({
                    'memoryId': f"{row['publication_id']}:{record.get('candidateId', len(result))}",
                    'streamKey': row['stream_key'],
                    'publicationId': row['publication_id'],
                    'publicationVersion': row['version'],
                    'payloadHash': row['payload_hash'],
                    'evidenceSetHash': row['evidence_set_hash'],
                    'publishedBy': row['published_by'],
                    'publishedAt': row['published_at'],
                    'candidateId': record.get('candidateId'),
                    'candidate': deepcopy(record.get('candidate', record)),
                    'source': deepcopy(record.get('source', {})),
                    'memoryStatus': 'published-human-reviewed-unverified',
                })
                if len(result) >= limit:
                    return result
        return result
    def list_tool_calls(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_tool_call WHERE run_id=%s ORDER BY created_at",(rid,))
                rows = [dict(x) for x in c.fetchall()]
        return self.annotate_connector_calls(rows, 'tool')
    def list_tool_call_summaries(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT call_id,run_id,step_run_id,tool_key,tool_version,status,error_message,latency_ms,retry_count,trace_id,created_at,started_at,finished_at,context_id,manifest_hash,operation_key FROM agent_tool_call WHERE run_id=%s ORDER BY created_at", (rid,))
                return [dict(x) for x in c.fetchall()]
    def get_tool_call(self, rid, call_id):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_tool_call WHERE run_id=%s AND call_id=%s", (rid, call_id))
                row = c.fetchone()
        return self.annotate_connector_calls([dict(row)], 'tool')[0] if row else None
    def list_tool_call_summaries(self, rid):
        """Tool-call metadata for graph projection; omit input/output bodies."""
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("""SELECT call_id,run_id,step_run_id,tool_key,tool_version,status,
                                    error_message,latency_ms,retry_count,trace_id,created_at,
                                    started_at,finished_at,context_id,manifest_hash,operation_key
                               FROM agent_tool_call WHERE run_id=%s ORDER BY created_at""", (rid,))
                rows = [dict(x) for x in c.fetchall()]
        return self.annotate_connector_calls(rows, 'tool')
    def list_approvals(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_approval_request WHERE run_id=%s ORDER BY created_at",(rid,)); return [dict(x) for x in c.fetchall()]
    def get_approval(self, aid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute('SELECT * FROM agent_approval_request WHERE approval_id=%s',(aid,)); return self._one(c)
    def record_loop(self, rid, rule, iteration, before, after, stop_reason=None):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("INSERT INTO agent_loop_run(loop_run_id,run_id,loop_id,iteration,trigger_reason,return_step_key,progress_before_json,progress_after_json,stop_reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(new_id('loop'),rid,rule['loop_id'],iteration,rule['trigger_condition'],rule['return_step_key'],Json(before),Json(after),stop_reason))
                c.execute("UPDATE agent_run SET iteration=%s,updated_at=now() WHERE run_id=%s",(iteration,rid))
                self.event(c,rid,'loop.iteration',rule['return_step_key'],{'loopId':rule['loop_id'],'iteration':iteration,'stopReason':stop_reason})
    def request_approval(self, rid, action, reason, requested_by, step_key=None):
        aid=new_id('approval')
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("INSERT INTO agent_approval_request(approval_id,run_id,step_key,action,reason,requested_by) VALUES(%s,%s,%s,%s,%s,%s)",(aid,rid,step_key,action,reason,requested_by))
                self.event(c,rid,'approval.requested',step_key,{'approvalId':aid,'action':action})
        return aid
    def decide_approval(self, aid, approved, reviewer, comment=''):
        status='approved' if approved else 'rejected'
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("UPDATE agent_approval_request SET status=%s,reviewed_by=%s,review_comment=%s,reviewed_at=now() WHERE approval_id=%s AND status='pending' RETURNING run_id",(status,reviewer,comment,aid)); row=c.fetchone()
                if not row: raise ValueError('approval not found or already decided')
                self.event(c,row['run_id'],'approval.decided',None,{'approvalId':aid,'status':status,'reviewer':reviewer})
        return row['run_id']
    def get_run(self, rid):
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_run WHERE run_id=%s",(rid,)); run=self._one(c)
                if not run: return None
                c.execute("SELECT state_json,revision FROM agent_run_state WHERE run_id=%s",(rid,)); state=self._one(c); run['state']=state; return run
