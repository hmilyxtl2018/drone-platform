"""Verify historical frozen references without consulting today's defaults."""
from ...goal_contract import digest as profile_hash
from ...task_intent import digest as intent_hash, narrow, parse_deadline, OPERATIONS
from ...skills import validate_manifest, validate_baseline_bindings
from ...execution_baseline import graph_hash, validate_baseline
from ..checks import budget_valid
from ..contracts import VerificationError


def check_item(identity, status, reason, ref):
    return {'checkId': identity, 'status': status, 'required': True,
            'reasonCode': reason, 'evidenceRefs': [ref]}


class RunContractAdapter:
    kind = 'run-contract'

    def load(self, subject, context):
        run = context.run(subject['run_id'])
        if run['run_id'] in context._contract_cache:
            return context._contract_cache[run['run_id']]
        snapshot = context.profile(run['run_id'])
        payload = snapshot.get('profile_json') if snapshot else None
        checks = []
        def add(n, state, reason): checks.append(check_item(f'RUN-{n:03}', state, reason, run['run_id']))
        if not isinstance(payload, dict):
            add(1, 'inconclusive', 'frozen-profile-unavailable-or-too-large')
            return {'run': run, 'checks': checks, 'profile': {}, 'authority': {'profileHash': None}}
        profile_ok = (profile_hash(payload) == snapshot['profile_hash'] and
                      payload.get('agentKey') == run['agent_key'] and payload.get('version') == run['agent_version'])
        add(1, 'passed' if profile_ok else 'failed', 'frozen-profile-valid' if profile_ok else 'frozen-profile-mismatch')
        if not profile_ok:
            add(2, 'inconclusive', 'frozen-contract-dependencies-unverified')
            return {'run': run, 'checks': checks, 'profile': {}, 'authority': {'profileVersion': run['agent_version']}}

        shape_ok = (isinstance(payload.get('steps'), list) and len(payload['steps']) <= 200 and
            all(isinstance(s, dict) and isinstance(s.get('key'), str) for s in payload['steps']) and
            isinstance(payload.get('tools', {}), dict) and all(isinstance(v, dict) for v in payload.get('tools', {}).values()) and
            all(payload.get(k) is None or isinstance(payload[k], dict) for k in
                ('executionBaseline', 'goalContract', 'harnessContract', 'skillManifest', 'reasoningPolicy')))
        if not shape_ok:
            add(7, 'failed', 'frozen-profile-schema-invalid')
            return {'run': run, 'checks': checks, 'profile': {}, 'authority': {'profileVersion': run['agent_version']}}

        baseline = payload.get('executionBaseline') or {}
        baseline_ok = False
        if not baseline:
            add(2, 'inconclusive', 'frozen-baseline-unrecorded')
        else:
            try:
                valid = graph_hash(baseline['graph']) == baseline['graphHash'] and not validate_baseline(baseline['graph'])
            except (TypeError, KeyError, ValueError, AttributeError): valid = False
            baseline_ok = valid
            add(2, 'passed' if valid else 'failed', 'frozen-baseline-valid' if valid else 'frozen-baseline-hash-mismatch')

        try:
            manifest = validate_manifest(payload.get('skillManifest') or {}, run['agent_key'], run['agent_version'])
            validate_baseline_bindings(manifest, baseline)
            add(3, 'passed', 'frozen-skill-bindings-valid')
        except (ValueError, TypeError, KeyError):
            add(3, 'failed', 'frozen-skill-bindings-mismatch')

        goal = payload.get('goalContract') or {}
        if not goal:
            add(4, 'inconclusive', 'frozen-goal-unrecorded')
        else:
            stored = context.query('''SELECT agent_key,revision,content_hash FROM agent_goal_contract_revision
                WHERE contract_id=%s''', (goal.get('contractId'),))
            valid = bool(stored and stored['agent_key'] == run['agent_key'] and
                stored['revision'] == goal.get('revision') and stored['content_hash'] == goal.get('contentHash') and
                profile_hash(goal.get('document')) == goal.get('contentHash'))
            add(4, 'passed' if valid else 'failed', 'frozen-goal-valid' if valid else 'frozen-goal-reference-mismatch')

        harness = payload.get('harnessContract') or {}
        row = context.query('''SELECT harness_contract_id,contract_hash,profile_hash,plan_hash,
            capability_graph_hash,composition_graph_hash,effective_tools_json,effective_data_grants_json,
            effective_network_grants_json,effective_child_grants_json,effective_limits_json,snapshot_hash,baseline_hash
            FROM agent_run_harness_snapshot WHERE run_id=%s''', (run['run_id'],))
        if not row or not harness:
            add(5, 'inconclusive', 'frozen-harness-unrecorded')
        else:
            revision = context.query('''SELECT agent_key,profile_version,contract_hash
                FROM agent_harness_contract WHERE harness_contract_id=%s''', (row['harness_contract_id'],))
            value = {'harnessContractId': row['harness_contract_id'], 'contractHash': row['contract_hash'],
                'profileHash': row['profile_hash'], 'planHash': row['plan_hash'],
                'capabilityGraphHash': row['capability_graph_hash'], 'compositionGraphHash': row['composition_graph_hash'],
                'effectiveTools': row['effective_tools_json'], 'effectiveDataGrants': row['effective_data_grants_json'],
                'effectiveNetworkGrants': row['effective_network_grants_json'],
                'effectiveChildGrants': row['effective_child_grants_json'], 'effectiveLimits': row['effective_limits_json']}
            if row['baseline_hash']: value['baselineHash'] = row['baseline_hash']
            valid = (profile_hash(value) == row['snapshot_hash'] and row['profile_hash'] == snapshot['profile_hash']
                and row['plan_hash'] == profile_hash(payload.get('steps'))
                and row['harness_contract_id'] == harness.get('harnessContractId')
                and row['contract_hash'] == harness.get('contractHash')
                and revision and revision['agent_key'] == run['agent_key']
                and revision['profile_version'] == run['agent_version']
                and revision['contract_hash'] == row['contract_hash']
                and row['baseline_hash'] == baseline.get('graphHash'))
            add(5, 'passed' if valid else 'failed', 'frozen-harness-valid' if valid else 'frozen-harness-mismatch')

        chain, seen, current = [], set(), run['run_id']
        intent_status = 'passed'
        while current:
            if current in seen or len(seen) >= 16:
                intent_status = 'failed'
                break
            seen.add(current)
            row = context.query('''SELECT b.run_id,b.parent_run_id,b.content_hash,r.parent_intent_id,
                r.intent_id,r.content_hash AS revision_hash,r.document_json FROM agent_run_task_intent b
                JOIN agent_task_intent_revision r USING(intent_id) WHERE b.run_id=%s''', (current,))
            if not row:
                intent_status = 'inconclusive' if not chain else 'failed'
                break
            if intent_hash(row['document_json']) != row['content_hash'] or row['revision_hash'] != row['content_hash']:
                intent_status = 'failed'
                break
            doc = row['document_json']
            try:
                if (row['parent_run_id'] != context.run(current)['parent_run_id']
                        or not row['parent_run_id'] and (row['parent_intent_id'] or doc.get('parentIntentHash'))):
                    raise ValueError()
                if (not budget_valid(doc['budgets']) or not isinstance(doc['allowedOperations'], list)
                        or not set(doc['allowedOperations']) <= OPERATIONS
                        or not isinstance(doc['allowedTools'], list)
                        or any(not isinstance(t, str) for t in doc['allowedTools'])):
                    raise ValueError()
                parse_deadline(doc['expiresAt'])
            except (ValueError, TypeError, KeyError):
                intent_status = 'failed'
                break
            chain.append(dict(row))
            current = row['parent_run_id']
        for child, parent in zip(chain, chain[1:]):
            doc, upper = child['document_json'], parent['document_json']
            try:
                narrow(upper, {k: doc[k] for k in ('allowedOperations', 'allowedTools', 'budgets', 'expiresAt')})
                if (child['parent_intent_id'] != parent['intent_id'] or doc.get('parentIntentHash') != parent['content_hash']
                        or any(doc.get(k) != upper.get(k) for k in ('objective', 'scope', 'sourcePolicy'))):
                    raise ValueError()
            except (ValueError, PermissionError, KeyError, TypeError): intent_status = 'failed'
        add(6, intent_status, {'passed': 'frozen-intent-chain-valid', 'failed': 'frozen-intent-chain-mismatch',
                              'inconclusive': 'historical-intent-unrecorded'}[intent_status])
        current = context.query('SELECT current_version FROM agent_definition WHERE agent_key=%s', (run['agent_key'],))
        result = {'run': run, 'profile': payload if baseline_ok else {}, 'checks': checks,
                'currentness': 'current' if current and current['current_version'] == run['agent_version'] else 'superseded' if current else 'unknown',
                'authority': {'profileVersion': run['agent_version'], 'profileHash': snapshot['profile_hash'],
                              'baselineHash': baseline.get('graphHash')}}
        context._contract_cache[run['run_id']] = result
        return result
