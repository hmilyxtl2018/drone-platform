"""Read-only Skill projections. Configuration never proves execution."""
from copy import deepcopy
from .skills import reference, digest


def bindings(profile, *, full=False):
    result = []
    for b in (profile.skill_manifest or {}).get('bindings', []):
        item = reference(b)
        item.update(name=b['package']['manifest']['name'],
                    description=b['package']['manifest']['description'])
        if full:
            item['package'] = deepcopy(b['package'])
        result.append(item)
    return result


def configuration(engine, agent_key, version=None, path=()):
    profile = engine.loader.load(agent_key, version)
    identity = (profile.agent_key, profile.version)
    if identity in path or len(path) >= 32:
        return {'agentKey': agent_key, 'profileVersion': profile.version, 'bindings': [], 'delegated': []}
    delegated = []
    for edge in (profile.harness_contract or {}).get('compositionEdges', []):
        if edge.get('parent_agent_key') != agent_key:
            continue
        child = configuration(engine, edge['child_agent_key'], edge['child_profile_version'], (*path, identity))
        delegated.append(child)
    return {'agentKey': agent_key, 'profileVersion': profile.version,
            'bindings': bindings(profile, full=True), 'delegated': delegated}


def run_view(engine, run_id):
    profile = engine.loader.load_for_run(run_id)
    return {'runId': run_id, 'bindings': bindings(profile, full=True),
            'invocations': engine.repo.list_skill_invocations(run_id),
            'note': '配置不代表执行；成功调用不代表事实真实或报告完整。'}


def invocation_detail(engine, run_id, invocation_id):
    view = run_view(engine, run_id)
    invocation = next((i for i in view['invocations'] if i['invocation_id'] == invocation_id), None)
    if not invocation:
        raise LookupError('Skill invocation not found in this Run')
    binding = next((b for b in view['bindings'] if b['stepKey'] == invocation['step_key']
                    and b['stage'] == invocation['stage'] and b['contentHash'] == invocation['content_hash']), None)
    calls = []
    for call in engine.repo.list_model_calls(run_id):
        if call['call_id'] not in invocation['model_call_ids']:
            continue
        # Even a corrupted ID cannot borrow another operation's proof.
        if (call.get('run_id') != run_id or call.get('operation_key') != invocation['operation_key']
                or call.get('stage') != invocation['stage']):
            continue
        item = {k: call.get(k) for k in ('call_id','provider_key','requested_model','actual_model','status',
                'input_tokens','output_tokens','latency_ms','error_code','created_at')}
        for key, field in (('input_artifact_id', 'input'), ('output_artifact_id', 'output')):
            artifact = engine.repo.get_artifact(run_id, call.get(key)) if call.get(key) else None
            item[field] = artifact.get('content_json') if artifact else None
        calls.append(item)
    successful = [c for c in calls if c['status'] == 'succeeded' and c['output'] is not None]
    output = (successful[-1]['output'] or {}).get('output') if successful else None
    proof = bool(binding and successful and digest(output) == invocation.get('output_hash'))
    return {'runId': run_id, 'invocation': invocation, 'binding': binding, 'calls': calls,
            'validation': {'receiptVerified': proof, 'status': invocation['status'],
                           'errorCode': invocation.get('error_code'), 'factualCorrectness': 'unverified'},
            'billing': '已计入模型调用预算；Skill 不重复计费；未配置价格时不估算金额。'}
