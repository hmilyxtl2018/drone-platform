"""Register five testing cognitive Profiles; default is a read-only preview.

No existing Profile is overwritten. This reviewed installer creates only new
configuration and tool contract versions, never Runs, credentials, or facts.
Runtime readiness is checked separately by the server before dispatch.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from urllib.parse import urlsplit

from psycopg2 import sql
from psycopg2.extras import Json
from jsonschema import Draft202012Validator

from agent_admin.config_publication import ConfigPublicationService, SECTIONS
from agent_engine.cognitive_profiles import (AGENT_KEYS, APPLICATION_KEY, ENGINE_MODE,
    VERSION, build_document, composition_edges, design, document_hash, effective_node_budget)
from agent_engine.execution_baseline import build_baseline, graph_hash, register_baseline, validate_baseline
from agent_engine.capabilities import graph_errors, load_graph
from agent_engine.postgres import connection_factory
from agent_tools.cognitive import TOOL_SCHEMAS, VERSION as TOOL_VERSION


CREATED_BY = 'register-cognitive-suite-v1'
PROFILE_TABLES = tuple(table for table, _ in SECTIONS.values())
HARNESS_TABLES = ('agent_harness_tool_grant','agent_harness_data_grant',
                  'agent_harness_network_grant','agent_harness_step_budget')
ORDER = ('tool_registry','tool_version',*PROFILE_TABLES,'agent_version','agent_step',
         'agent_step_tool','agent_tool_binding','agent_validation_rule','agent_harness_contract',
         *HARNESS_TABLES,'agent_composition_edge')
WRITE_TABLES = frozenset(ORDER)


def _stable(kind, value):
    return 'cognitive-' + kind + '-' + hashlib.sha256(str(value).encode()).hexdigest()[:24]


def _existing(cursor, key):
    cursor.execute('SELECT agent_key,current_version,status FROM agent_definition WHERE agent_key=%s',(key,))
    return cursor.fetchone()


def _registered_tool_versions(cursor, tools):
    versions = {}
    for key in sorted(tools):
        if key in TOOL_SCHEMAS:
            versions[key] = TOOL_VERSION
            continue
        cursor.execute('''SELECT t.current_version,v.version FROM tool_registry t JOIN tool_version v USING(tool_key)
            WHERE t.tool_key=%s AND t.status NOT IN ('disabled','inactive','retired')
            AND v.status NOT IN ('disabled','inactive','retired') ORDER BY v.version''',(key,))
        candidates = list(cursor.fetchall())
        if not candidates: raise ValueError('required tool unavailable: ' + key)
        current = next((r for r in candidates if r['version'] == r['current_version']), None)
        versions[key] = (current or candidates[-1])['version']
    return versions


def _canonical_tool_configs(cursor, tool_versions):
    """Reuse an installed tool's reviewed execution config for new bindings.

    A binding config (implementation hash, timeout and retry policy) is part of
    the executable contract.  Assigning a generic config to a new parent Agent
    would make its delegated legacy Actions appear disabled after constrained
    projection, even when the tool/version itself is registered.
    """
    result = {}
    for key in sorted(tool_versions):
        cursor.execute('''SELECT config_json,enabled FROM agent_tool_binding
            WHERE tool_key=%s AND tool_version=%s ORDER BY agent_key''', (key, tool_versions[key]))
        rows = list(cursor.fetchall())
        row = next((candidate for candidate in rows if bool(candidate.get('enabled'))), None)
        if row and row.get('config_json') is not None:
            value = row['config_json']
            result[key] = json.loads(value) if isinstance(value, str) else deepcopy(value)
    return result


def _new_tool_rows(cursor):
    rows = {'tool_registry': [], 'tool_version': []}
    for key,(inp,out) in TOOL_SCHEMAS.items():
        Draft202012Validator.check_schema(inp); Draft202012Validator.check_schema(out)
        cursor.execute('SELECT tool_key FROM tool_registry WHERE tool_key=%s',(key,))
        if cursor.fetchone() is None:
            rows['tool_registry'].append({'tool_key':key,'tool_name':key,'category':'cognitive-research',
                'description':'Deterministic read-only ontology research tool.','protocol':'python',
                'input_schema':inp,'output_schema':out,'tags_json':['ontology','research'],
                'access_mode':'read_only','risk_level':'low','status':'testing','current_version':TOOL_VERSION,
                'endpoint':None,'execution_kind':'atomic_tool'})
        cursor.execute('SELECT input_schema,output_schema FROM tool_version WHERE tool_key=%s AND version=%s',(key,TOOL_VERSION))
        existing = cursor.fetchone()
        if existing:
            if existing['input_schema'] != inp or existing['output_schema'] != out:
                raise ValueError('immutable cognitive tool version differs: ' + key)
        else:
            rows['tool_version'].append({'tool_key':key,'version':TOOL_VERSION,'endpoint':None,
                'input_schema':inp,'output_schema':out,'changelog':'Reviewed deterministic research contract v1.',
                'status':'testing','side_effect_class':'none','idempotency_required':False,
                'network_access':False,'data_classification':'public',
                'contract_hash':document_hash({'input':inp,'output':out}),
                'execution_transport':'python','adapter_version':TOOL_VERSION})
    return rows


def build_plan(cursor):
    docs = {key:build_document(key) for key in AGENT_KEYS}
    versions = _registered_tool_versions(cursor,{b['tool_key'] for d in docs.values() for b in d['bindings']})
    canonical_configs = _canonical_tool_configs(cursor, versions)
    rows = {table:[] for table in WRITE_TABLES}
    rows.update(_new_tool_rows(cursor))
    specs = {p['agentKey']:p for p in design()['profiles']}
    edges = composition_edges(VERSION)
    # Only actual configured provider hosts are used; never copy credentials.
    cursor.execute('SELECT provider_key,base_url FROM provider_registry WHERE provider_key=ANY(%s) AND enabled=true',
                   (['deepseek','firecrawl'],))
    provider_hosts = {urlsplit(r['base_url']).hostname for r in cursor.fetchall()}
    networks = {'w3.org':['GET'],'*.w3.org':['GET'],'nist.gov':['GET'],'*.nist.gov':['GET']}
    networks.update({host:['POST'] for host in provider_hosts if host})
    graphs = {}
    for key,doc in docs.items():
        if _existing(cursor,key): raise ValueError('refusing to overwrite existing Agent: '+key)
        is_app = key == APPLICATION_KEY
        cfg = doc['models']['config_json']
        cfg['engineMode'] = ENGINE_MODE  # server-owned installation value
        for binding in doc['bindings']:
            binding['tool_version'] = versions[binding['tool_key']]
            if binding['tool_key'] in canonical_configs:
                binding['config_json'] = deepcopy(canonical_configs[binding['tool_key']])
        for section,(table,fields) in SECTIONS.items():
            rows[table].append({'agent_key':key,**{field:deepcopy(doc[section][field]) for field in fields}})
        definition = rows['agent_definition'][-1]
        definition.update(agent_layer='application' if is_app else 'action',
            runtime_kind='llm_loop' if is_app else 'deterministic_action',
            profile_schema_version='1.0.0',default_harness_contract_id=None)
        rows['agent_version'].append({'agent_key':key,'version':VERSION,
            'prompt':doc['definition']['system_prompt'],'config_json':cfg,'status':'testing'})
        for step in doc['steps']:
            sid = _stable('step',key+':'+step['step_key'])
            rows['agent_step'].append({'step_id':sid,'agent_key':key,
                **{f:deepcopy(step[f]) for f in ('sequence_no','step_key','name','description','input_contract',
                    'output_contract','parallel_group','required','retry_limit')},'dependencies_json':step['dependencies']})
            rows['agent_step_tool'].extend({'step_id':sid,'tool_key':tool} for tool in step['tools'])
        rows['agent_tool_binding'].extend({'agent_key':key,**deepcopy(b)} for b in doc['bindings'])
        rows['agent_validation_rule'].extend({**deepcopy(r),'agent_key':key,
            'rule_id':_stable('rule',key+':'+r['rule_id'])} for r in doc['validationRules'])
        hid = _stable('harness',key+':'+VERSION)
        contract = {'schemaVersion':'cognitive-harness.v1','agentKey':key,'profileVersion':VERSION,
            'scope':['public-research','simulation'],'defaultDeny':True,'directArtifactRefsOnly':True,
            'sharedBudget':True,'allowAutomaticPublication':False}
        rows['agent_harness_contract'].append({'harness_contract_id':hid,'agent_key':key,
            'profile_version':VERSION,'contract_version':'1.0.0','agent_layer':definition['agent_layer'],
            'runtime_kind':definition['runtime_kind'],'invocation_mode':'task' if is_app else 'child',
            'default_deny':True,'max_child_depth':1 if is_app else 0,
            'lifecycle_policy_json':{'status':'testing'},'failure_policy_json':{'hardError':'stop'},
            'memory_policy_json':{'scope':'run'},'side_effect_policy_json':{'allowed':[]},
            'contract_json':contract,'contract_hash':graph_hash(contract),'status':'testing'})
        rows['agent_harness_tool_grant'].extend({'harness_contract_id':hid,'tool_key':b['tool_key'],
            'tool_version':b['tool_version'],'permission':'invoke','approval_required':False,
            'input_constraint_json':{},'grant_config_json':{}} for b in doc['bindings'])
        rows['agent_harness_data_grant'].append({'grant_id':_stable('data',key),'harness_contract_id':hid,
            'resource_type':'ontology','resource_pattern':'uav-ontology','access_mode':'read',
            'sensitivity_level':'public','approval_required':False})
        rows['agent_harness_network_grant'].extend({'network_grant_id':_stable('network',key+':'+domain),
            'harness_contract_id':hid,'domain_pattern':domain,'methods_json':methods,
            'max_response_bytes':8_000_000,'approval_required':False} for domain,methods in networks.items())
        for node in specs[key]['nodes']:
            rows['agent_harness_step_budget'].append({'harness_contract_id':hid,'step_key':node['id'],
                **effective_node_budget(key, node),'max_rounds':node['max_rounds'],'max_parallelism':3,
                'max_runtime_seconds':node['max_runtime_seconds'],'enforcement':'hard','max_iterations':1,
                'max_retries':1,'max_evidence_bytes':8_000_000,'max_concurrency':3,'estimated_cost_micros':0})
        harness = {'stepBudgets':[r for r in rows['agent_harness_step_budget'] if r['harness_contract_id']==hid],
                   'compositionEdges':edges if is_app else []}
        graph = build_baseline(key,doc,harness)
        errors = validate_baseline(graph,{b['tool_key'] for b in doc['bindings']})
        if errors: raise ValueError('invalid baseline: '+ '; '.join(e['message'] for e in errors))
        capability = load_graph(cfg['capabilityGraphPath'],key)
        errors = graph_errors({'graph':capability,'actions':{}},doc)
        if errors: raise ValueError('invalid capability contract: '+ '; '.join(e['message'] for e in errors))
        graphs[key] = graph
    rows['agent_composition_edge'] = edges
    return {'rows':rows,'documents':docs,'graphs':graphs,'toolVersions':versions,
        'planHash':document_hash(rows),'profileCount':len(docs),'stepCount':sum(len(d['steps']) for d in docs.values())}


def _insert(cursor,table,row):
    if table not in WRITE_TABLES: raise ValueError('registration table is not allowed')
    cursor.execute('''SELECT column_name,data_type FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name=%s''',(table,))
    types = {r['column_name']:r['data_type'] for r in cursor.fetchall()}
    if set(row)-set(types): raise ValueError('required registration columns missing: '+table)
    fields = list(row)
    def value(field):
        v = row[field]
        if isinstance(v,(dict,list)):
            return Json(v) if types[field] in {'json','jsonb'} else json.dumps(v,ensure_ascii=False)
        if type(v) is bool and types[field] in {'integer','bigint','smallint'}: return int(v)
        return v
    cursor.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),
        sql.SQL(',').join(map(sql.Identifier,fields)),sql.SQL(',').join(sql.Placeholder() for _ in fields)),
        tuple(value(f) for f in fields))


def register_connection(connection,*,apply=False):
    with connection.cursor() as cursor:
        if apply: ConfigPublicationService._config_lock(cursor,True)
        existing = [key for key in AGENT_KEYS if _existing(cursor,key)]
        if existing: return {'status':'already-exists-preserved','applied':False,'existing':existing}
        plan = build_plan(cursor)
        result = {'status':'dry-run','applied':False,'agentKeys':list(AGENT_KEYS),
            'profileCount':plan['profileCount'],'stepCount':plan['stepCount'],'planHash':plan['planHash'],
            'newTools':len(plan['rows']['tool_registry']),'newToolVersions':len(plan['rows']['tool_version']),
            'baselineHashes':{key:graph_hash(g) for key,g in plan['graphs'].items()},'enabledByDefault':False}
        if not apply:return result
        for table in ORDER:
            for row in plan['rows'][table]:_insert(cursor,table,row)
        baseline_results = {}
        for key in AGENT_KEYS:
            hid = _stable('harness',key+':'+VERSION)
            cursor.execute('UPDATE agent_definition SET default_harness_contract_id=%s WHERE agent_key=%s',(hid,key))
            persisted = ConfigPublicationService._export(cursor,key)
            # engineMode is server-owned, not part of editable validation.
            validation_doc = deepcopy(persisted)
            validation_doc['models']['config_json'].pop('engineMode',None)
            errors = ConfigPublicationService._validate(cursor,validation_doc,key)
            if errors: raise ValueError('persisted Profile rejected: '+key+' '+json.dumps(errors,ensure_ascii=True))
            graph = build_baseline(key,persisted,ConfigPublicationService._baseline_harness(cursor,key))
            if graph_hash(graph) != result['baselineHashes'][key]:
                raise ValueError('persisted baseline differs from preview: '+key)
            baseline_results[key] = register_baseline(cursor,key,graph,status='testing',created_by=CREATED_BY)
        from agent_engine.goal_contract import seed
        seed(cursor)
        result.update(status='created-testing',applied=True,
            baselines={key:{'baselineId':v['baselineId'],'graphHash':v['graphHash']} for key,v in baseline_results.items()})
        return result


def register(*,apply=False):
    with connection_factory(database='ontology_agent_admin')() as connection:
        connection.set_session(readonly=not apply,isolation_level='SERIALIZABLE' if apply else 'REPEATABLE READ')
        return register_connection(connection,apply=apply)


def repair_testing_delegation_bindings(connection):
    """Repair only an unexecuted testing suite's legacy delegation contracts.

    Existing Agents and immutable tool versions are preserved. Once any suite
    Run exists this deployment repair is unavailable; a new reviewed version is
    required instead.
    """
    with connection.cursor() as c:
        ConfigPublicationService._config_lock(c, True)
        c.execute('SELECT agent_key,status FROM agent_definition WHERE agent_key=ANY(%s)', (list(AGENT_KEYS),))
        definitions = list(c.fetchall())
        if len(definitions) != len(AGENT_KEYS) or any(r['status'] != 'testing' for r in definitions):
            raise ValueError('repair requires the complete unpublished testing suite')
        c.execute('SELECT 1 FROM agent_run WHERE agent_key=ANY(%s) LIMIT 1', (list(AGENT_KEYS),))
        if c.fetchone():
            raise ValueError('executed suite requires a new reviewed version')
        expected = {}
        for child in ('evidence-collection-action', 'evidence-extraction-action'):
            doc = ConfigPublicationService._export(c, child)
            for binding in doc['bindings']:
                if not binding['enabled']:
                    continue
                key = binding['tool_key']
                value = {'tool_version': binding['tool_version'], 'config_json': binding['config_json']}
                if key in expected and expected[key] != value:
                    raise ValueError('legacy child tool contracts conflict: ' + key)
                expected[key] = deepcopy(value)
        changed = []
        for key, value in expected.items():
            c.execute('SELECT tool_version,config_json FROM agent_tool_binding WHERE agent_key=%s AND tool_key=%s',
                      (APPLICATION_KEY, key))
            current = c.fetchone()
            if not current:
                raise ValueError('parent is missing delegated tool: ' + key)
            if current['tool_version'] != value['tool_version']:
                raise ValueError('tool version repair requires an explicit Harness revision: ' + key)
            if dict(current) != value:
                c.execute('UPDATE agent_tool_binding SET tool_version=%s,config_json=%s WHERE agent_key=%s AND tool_key=%s',
                          (value['tool_version'], Json(value['config_json']), APPLICATION_KEY, key))
                changed.append(key)
        baseline_results = {}
        for key in AGENT_KEYS:
            doc = ConfigPublicationService._export(c, key)
            editable = deepcopy(doc)
            editable['models']['config_json'].pop('engineMode', None)
            errors = ConfigPublicationService._validate(c, editable, key)
            if errors:
                raise ValueError('repaired testing Profile rejected: ' + key + ' ' + json.dumps(errors))
            graph = build_baseline(key, doc, ConfigPublicationService._baseline_harness(c, key))
            baseline = register_baseline(c, key, graph, status='testing', created_by=CREATED_BY + ':binding-repair')
            baseline_results[key] = {'baselineId': baseline['baselineId'], 'graphHash': baseline['graphHash']}
        return {'status': 'testing-delegation-contracts-repaired', 'changedParentBindings': changed,
                'version': VERSION, 'baselines': baseline_results,
                'createdRuns': 0, 'publishedProfiles': 0}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    try:result=register(apply=args.apply)
    except (ValueError,RuntimeError) as exc:parser.exit(1,str(exc)+'\n')
    print(json.dumps(result,ensure_ascii=True,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
