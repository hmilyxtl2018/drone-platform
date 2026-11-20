"""Bounded whole-Run coverage. Enumerate expected nodes before existing calls."""
from .adapters.run_contract import RunContractAdapter, check_item
from .adapters.invocation import InvocationAdapter
from .adapters.report_evidence import ReportEvidenceAdapter
from ..goal_contract import digest
from .contracts import VerificationError

TERMINAL = {'completed', 'completed-with-gaps', 'requires-human-review', 'blocked', 'failed', 'cancelled'}


class RunIntegrity:
    kind = 'run-integrity'

    def load(self, subject, context):
        root = RunContractAdapter().load(subject, context)
        checks, todo, seen = [], [(subject['run_id'], frozenset())], set()
        calls_seen = 0
        def merge(items, prefix):
            for item in items: checks.append({**item, 'checkId': prefix + ':' + item['checkId']})
        def gap(code, reason, ref, state='inconclusive'):
            checks.append(check_item(code, state, reason, ref))
        try:
            # Preserve the root contract/report checks even when descendants
            # exhaust the fixed scan allowance later in this same snapshot.
            merge(root['checks'], subject['run_id'])
            report = context.query('''SELECT artifact_id FROM agent_run_artifact
                WHERE run_id=%s AND (artifact_type LIKE '%%-report' OR artifact_type='agent-output')
                ORDER BY CASE WHEN artifact_type='agent-output' THEN 1 ELSE 0 END,created_at DESC,artifact_id DESC LIMIT 1''', (subject['run_id'],))
            if report:
                result = ReportEvidenceAdapter().load({'run_id': subject['run_id'], 'artifact_id': report['artifact_id']}, context)
                merge([x for x in result['checks'] if x['checkId'].startswith('REP-')], report['artifact_id'])
            else:
                gap('ALL-REPORT', 'report-artifact-unavailable', subject['run_id'])
            while todo:
                run_id, ancestors = todo.pop(0)
                if run_id in ancestors:
                    gap('ALL-CYCLE', 'delegation-cycle', run_id, 'failed')
                    continue
                if run_id in seen: continue
                if len(seen) >= 32:
                    gap('ALL-LIMIT', 'child-run-coverage-limit', subject['run_id'])
                    break
                seen.add(run_id)
                frozen = RunContractAdapter().load({'run_id': run_id}, context)
                if run_id != subject['run_id']: merge(frozen['checks'], run_id)
                run, profile = frozen['run'], frozen['profile']
                nodes = (profile.get('executionBaseline') or {}).get('graph', {}).get('nodes', [])
                declared = {b['stepKey'] for b in (profile.get('skillManifest') or {}).get('bindings', [])}
                declared.update(s.get('stepKey', s.get('key')) for s in (profile.get('reasoningPolicy') or {}).get('stages', [])
                                if type(s.get('modelRequired')) is bool)
                if any(n.get('required', True) and n.get('nodeType') != 'human_review' and
                       not n.get('childAgentKey') and not n.get('executorKey') and n['id'] not in declared for n in nodes):
                    gap(run_id + ':REQUIREMENTS', 'expected-invocation-requirements-unrecorded', run_id)
                if run['status'] not in TERMINAL:
                    gap(run_id + ':STATE', 'execution-in-progress', run_id)
                elif run['status'] != 'completed':
                    gap(run_id + ':STATE', 'execution-completed-with-gaps' if run['status'] == 'completed-with-gaps'
                        else 'execution-not-successful', run_id,
                        'failed' if run['status'] in {'failed', 'cancelled'} else 'inconclusive')
                steps = context.query('''SELECT step_run_id,step_key,status,attempt FROM agent_step_run
                    WHERE run_id=%s ORDER BY attempt DESC,created_at DESC,step_run_id DESC LIMIT 201''', (run_id,), many=True)
                latest = {}
                for step in steps: latest.setdefault(step['step_key'], step)
                if len(steps) > 200:
                    gap(run_id + ':STEPS', 'step-coverage-limit', run_id)
                actions = context.query('''SELECT invocation_id,parent_step_key,child_run_id,composition_edge_id,
                    budget_grant_json,grant_hash FROM agent_action_invocation WHERE run_id=%s ORDER BY created_at,invocation_id LIMIT 33''',
                    (run_id,), many=True)
                for node in nodes:
                    if not node.get('required', True): continue
                    step = latest.get(node['id'])
                    done = bool(step and step['status'] == 'succeeded')
                    state = ('passed' if done else 'inconclusive' if step and step['status'] in
                             {'completed-with-gaps', 'running', 'waiting-human', 'pending'} else
                             'failed' if run['status'] in TERMINAL else 'inconclusive')
                    gap(run_id + ':NODE:' + node['id'], 'required-node-recorded' if done else
                        'required-node-has-gaps' if step and step['status'] == 'completed-with-gaps'
                        else 'required-node-not-completed', run_id, state)
                    if node.get('childAgentKey'):
                        delegated = [a for a in actions if a['parent_step_key'] == node['id']]
                        gap(run_id + ':DELEGATE:' + node['id'], 'required-delegation-recorded' if delegated else 'required-delegation-missing',
                            run_id, 'passed' if delegated else 'failed' if done else 'inconclusive')
                for action in actions[:32]:
                    try:
                        child = context.run(action['child_run_id'])
                    except VerificationError as exc:
                        if exc.status != 404: raise
                        gap(run_id + ':GRANT:' + action['invocation_id'], 'delegation-child-unavailable', run_id, 'failed')
                        continue
                    edges = (profile.get('harnessContract') or {}).get('compositionEdges', [])
                    edge = next((e for e in edges if e.get('edge_id') == action['composition_edge_id']), None)
                    valid = bool(edge and edge.get('child_agent_key') == child['agent_key'] and
                        edge.get('child_profile_version') == child['agent_version'] and child['parent_run_id'] == run_id
                        and digest(action['budget_grant_json']) == action['grant_hash'])
                    gap(run_id + ':GRANT:' + action['invocation_id'], 'delegation-binding-match' if valid else 'delegation-binding-mismatch',
                        action['child_run_id'], 'passed' if valid else 'failed')
                    todo.append((action['child_run_id'], ancestors | {run_id}))
                if len(actions) > 32: gap(run_id + ':ACTIONS', 'delegation-coverage-limit', run_id)
                call_lists = []
                for kind, query in [
                    ('model', 'SELECT call_id AS identity,step_run_id,stage,operation_key,status,created_at FROM agent_model_call WHERE run_id=%s ORDER BY created_at,call_id LIMIT 101'),
                    ('tool', 'SELECT call_id AS identity,step_run_id,tool_key AS stage,operation_key,status,created_at FROM agent_tool_call WHERE run_id=%s ORDER BY created_at,call_id LIMIT 101'),
                    ('skill', 'SELECT invocation_id AS identity,step_run_id,stage,operation_key,status,created_at FROM agent_run_skill_invocation WHERE run_id=%s ORDER BY created_at,invocation_id LIMIT 101')]:
                    call_lists.extend((kind, row) for row in context.query(query, (run_id,), many=True))
                for kind, record in call_lists:
                    identity = record['identity']
                    if calls_seen >= 100:
                        gap('ALL-CALLS', 'invocation-coverage-limit', run_id)
                        break
                    calls_seen += 1
                    result = InvocationAdapter().load({'run_id': run_id, 'invocation_kind': kind, 'invocation_id': identity}, context)
                    items = [x for x in result['checks'] if x['checkId'].startswith('INV-')]
                    # A failed attempt is retained, but a later successful retry of
                    # the same logical operation can satisfy completion. Never
                    # suppress hash/ownership/ledger failures during recovery.
                    recovered = record['status'] in {'failed', 'cancelled', 'interrupted'} and any(
                        k == kind and other['status'] == 'succeeded' and
                        other['operation_key'] == record['operation_key'] and other['stage'] == record['stage']
                        and other['created_at'] > record['created_at'] for k, other in call_lists)
                    if recovered:
                        items = [{**x, 'status': 'not-applicable', 'required': False,
                                  'reasonCode': 'unsuccessful-attempt-recovered'} if x['checkId'] == 'INV-002' else x for x in items]
                    merge(items, identity)
                # All frozen Skill bindings applicable to completed steps need a receipt.
                for binding in (profile.get('skillManifest') or {}).get('bindings', []):
                    applicable = binding['stepKey'] in latest and latest[binding['stepKey']]['status'] == 'succeeded'
                    receipt = context.query('''SELECT invocation_id FROM agent_run_skill_invocation
                        WHERE run_id=%s AND step_key=%s AND stage=%s LIMIT 1''', (run_id, binding['stepKey'], binding['stage']))
                    if applicable and not receipt:
                        gap(run_id + ':SKILL:' + binding['stepKey'] + ':' + binding['stage'],
                            'skill-applicability-or-receipt-unrecorded', run_id)
                # Frozen reasoning policies can explicitly require direct model
                # receipts. Budgets alone never imply a mandatory model call.
                for stage in (profile.get('reasoningPolicy') or {}).get('stages', []):
                    if not stage.get('required', True) or not stage.get('modelRequired'): continue
                    step = latest.get(stage.get('stepKey', stage.get('key')))
                    proof = bool(step and any(k == 'model' and c['step_run_id'] == step['step_run_id']
                        and c['status'] == 'succeeded' for k, c in call_lists))
                    gap(run_id + ':MODEL:' + stage['key'], 'required-model-receipt-recorded' if proof
                        else 'required-model-receipt-missing', run_id, 'passed' if proof else
                        'failed' if step and step['status'] == 'succeeded' else 'inconclusive')
        except VerificationError as exc:
            if exc.code != 'verification-read-limit': raise
            gap('ALL-READ-LIMIT', 'verification-read-coverage-limit', subject['run_id'])
        return {**root, 'checks': checks, 'limitations': [
            'Verification covers frozen contracts and recorded execution/evidence; professional correctness is not assessed.',
            'Missing conditional Skill receipts remain unknown when applicability was not recorded; budgets alone do not define required calls.',
            'Tool payloads lack a separate immutable digest; no payload tamper certification is issued.'],
            'scan': {'runsChecked': len(seen), 'invocationsChecked': calls_seen}}
