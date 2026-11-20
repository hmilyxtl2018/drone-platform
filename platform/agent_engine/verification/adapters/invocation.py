"""Call receipt checks, always bound to the authorized Run and frozen Profile."""
from ...evidence_repository import artifact_json_hash
from ...skills import digest as skill_hash
from ..contracts import VerificationError
from .run_contract import RunContractAdapter, check_item


class InvocationAdapter:
    kind = 'invocation'

    def load(self, subject, context):
        run_id, kind, identity = subject['run_id'], subject['invocation_kind'], subject['invocation_id']
        frozen = RunContractAdapter().load({'run_id': run_id}, context)
        profile, checks = frozen['profile'], []
        def add(n, status, reason): checks.append(check_item(f'INV-{n:03}', status, reason, identity))
        if kind == 'model':
            row = context.query('''SELECT call_id,run_id,step_run_id,reservation_id,status,
                input_artifact_id,output_artifact_id,operation_key,stage FROM agent_model_call
                WHERE run_id=%s AND call_id=%s''', (run_id, identity))
        elif kind == 'tool':
            row = context.query('''SELECT call_id,run_id,step_run_id,tool_key,tool_version,status,
                (input_json IS NOT NULL) AS has_input,(output_json IS NOT NULL) AS has_output,operation_key,retry_count FROM agent_tool_call
                WHERE run_id=%s AND call_id=%s''', (run_id, identity))
        else:
            row = context.query('''SELECT invocation_id,run_id,step_run_id,step_key,stage,operation_key,
                skill_key,skill_version,content_hash,model_call_ids,output_hash,status
                FROM agent_run_skill_invocation WHERE run_id=%s AND invocation_id=%s''', (run_id, identity))
        if not row:
            raise VerificationError('verification-subject-not-found', 404)
        row = dict(row)
        add(1, 'passed', 'invocation-run-ownership-match')
        status = row['status']
        add(2, 'passed' if status == 'succeeded' else 'inconclusive' if status in {'running', 'unknown', 'pending', 'reserved'} else 'failed',
            'invocation-succeeded' if status == 'succeeded' else 'execution-in-progress-or-unknown' if status in {'running', 'unknown', 'pending', 'reserved'} else 'invocation-not-successful')
        step = context.query('SELECT run_id,step_key FROM agent_step_run WHERE step_run_id=%s', (row['step_run_id'],)) if row['step_run_id'] else None
        valid_step = bool(step and step['run_id'] == run_id and
                          any(s.get('key') == step['step_key'] for s in profile.get('steps', [])))
        add(3, 'passed' if valid_step else 'failed' if step else 'inconclusive',
            'frozen-step-ownership-match' if valid_step else 'frozen-step-ownership-unverified')
        if kind == 'model':
            artifacts = [context.artifact(run_id, row[k]) if row[k] else None
                         for k in ('input_artifact_id', 'output_artifact_id')]
            valid = all(a and a['content_json'] is not None and artifact_json_hash(a['content_json']) == a['content_hash'] for a in artifacts)
            add(4, 'passed' if valid else 'inconclusive' if status != 'succeeded' else 'failed',
                'model-input-output-hashes-match' if valid else 'model-input-output-unverified')
            reservation = context.query('SELECT run_id,charges_json FROM agent_budget_reservation WHERE reservation_id=%s', (row['reservation_id'],)) if row['reservation_id'] else None
            valid = bool(reservation and reservation['run_id'] == run_id and
                         isinstance(reservation['charges_json'], dict) and
                         type(reservation['charges_json'].get('model_calls')) is int and
                         reservation['charges_json']['model_calls'] >= 1)
            add(5, 'passed' if valid else 'failed' if reservation else 'inconclusive',
                'model-budget-record-match' if valid else 'model-budget-record-mismatch' if reservation else 'model-budget-record-unavailable')
        elif kind == 'tool':
            binding = profile.get('tools', {}).get(row['tool_key']) or {}
            valid = bool(binding.get('enabled') and binding.get('version') == row['tool_version'])
            # ToolBinding serializes tool_version as version in current Profiles.
            valid = valid or bool(binding.get('enabled') and binding.get('tool_version') == row['tool_version'])
            add(4, 'passed' if valid else 'failed', 'frozen-tool-binding-match' if valid else 'frozen-tool-binding-mismatch')
            operation = str(row.get('operation_key') or '') + ':attempt:' + str(row['retry_count'])
            ledger = context.query('''SELECT status,incomplete FROM agent_run_budget_ledger
                WHERE run_id=%s AND operation_key=%s ORDER BY created_at DESC LIMIT 1''', (run_id, operation))
            valid = bool(ledger and ledger['status'] == status and not ledger['incomplete'])
            add(5, 'passed' if valid else 'inconclusive', 'tool-budget-ledger-match' if valid else 'tool-budget-ledger-unavailable')
            recorded = row['has_input'] and (status != 'succeeded' or row['has_output'])
            add(6, 'passed' if recorded else 'inconclusive',
                'tool-input-output-recorded' if recorded else 'tool-input-output-unavailable')
        else:
            binding = next((b for b in (profile.get('skillManifest') or {}).get('bindings', []) if
                b['stepKey'] == row['step_key'] and b['stage'] == row['stage'] and b['skillKey'] == row['skill_key']
                and b['version'] == row['skill_version'] and b['contentHash'] == row['content_hash']), None)
            add(4, 'passed' if binding else 'failed', 'frozen-skill-binding-match' if binding else 'frozen-skill-binding-mismatch')
            proof, mismatch, budgets = False, False, []
            ids = row['model_call_ids']
            if not isinstance(ids, list) or len(ids) > 32:
                mismatch = True
                ids = []
            for call_id in ids:
                call = context.query('''SELECT run_id,step_run_id,reservation_id,operation_key,stage,status,output_artifact_id FROM agent_model_call
                    WHERE call_id=%s''', (call_id,))
                if not call or (call['run_id'], call['operation_key'], call['stage'], call['step_run_id']) != (run_id, row['operation_key'], row['stage'], row['step_run_id']):
                    mismatch = True
                    continue
                reservation = context.query('SELECT run_id,charges_json FROM agent_budget_reservation WHERE reservation_id=%s',
                    (call['reservation_id'],)) if call['reservation_id'] else None
                budgets.append('inconclusive' if not reservation else 'passed' if reservation['run_id'] == run_id
                    and isinstance(reservation['charges_json'], dict) and type(reservation['charges_json'].get('model_calls')) is int
                    and reservation['charges_json']['model_calls'] >= 1 else 'failed')
                artifact = context.artifact(run_id, call['output_artifact_id']) if call['output_artifact_id'] else None
                if call['status'] == 'succeeded' and artifact and isinstance(artifact['content_json'], dict):
                    proof = proof or (artifact_json_hash(artifact['content_json']) == artifact['content_hash']
                        and skill_hash(artifact['content_json'].get('output')) == row['output_hash'])
            add(5, 'passed' if proof and not mismatch else 'inconclusive' if status != 'succeeded' and not mismatch else 'failed',
                'skill-model-output-proof-match' if proof and not mismatch else 'skill-model-output-proof-unverified')
            state = 'failed' if mismatch or 'failed' in budgets else 'passed' if budgets and all(b == 'passed' for b in budgets) else 'inconclusive'
            add(6, state, 'skill-model-budget-chain-match' if state == 'passed' else 'skill-model-budget-chain-unverified')
        return {'run': frozen['run'], 'checks': [*frozen['checks'], *checks], 'authority': frozen['authority'],
                'currentness': frozen.get('currentness', 'unknown'), 'invocationStatus': status,
                'limitations': ['Tool input/output records have no separate immutable payload digest.' ] if kind == 'tool' else []}
