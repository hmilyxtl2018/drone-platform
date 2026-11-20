"""Versioned reasoning policies and immutable, Run-scoped reasoning records.

This mixin reuses the evidence ledger: a relation is a proposal, never an
ontology write. Human acceptance leaves truthStatus unverified; publication is
an explicitly reviewed artifact belonging to the same Run.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import re
import secrets

from psycopg2.extras import Json

from .worker import control_context


REVIEW_ACTIONS = frozenset({'accept', 'reject', 'request-evidence', 'mark-conflict', 'withdraw', 'publish'})
RESOURCE_KEYS = frozenset({'reasoning_rounds', 'gate_checks', 'relation_reviews', 'model_calls', 'tool_calls',
                          'http_calls', 'search_calls', 'scrape_calls', 'input_tokens', 'output_tokens'})


def reasoning_hash(value):
    """Use the existing artifact JSON canonicalization, including Unicode."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def reasoning_installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_run_reasoning_snapshot') AS table_name")
    row = cursor.fetchone()
    return bool(row and row['table_name'])


def _id(prefix):
    return prefix + '-' + secrets.token_hex(12)


def _text(value, name, limit=240):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('invalid ' + name)
    return value.strip()


def _json(value, name, maximum=1_000_000):
    if not isinstance(value, dict):
        raise ValueError(name + ' must be an object')
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(name + ' must be finite JSON') from exc
    if len(encoded.encode('utf-8')) > maximum:
        raise ValueError(name + ' exceeds storage limit')
    return deepcopy(value)


def validate_reasoning_policy(policy, *, execution_graph=None):
    from .reasoning_gates import normalize_quality_gates
    result = _json(policy, 'reasoning policy')
    for key in ('agentKey', 'profileVersion', 'policyId'):
        if key in result:
            result[key] = _text(result[key], key)
    if 'version' in result and (type(result['version']) is not int or result['version'] < 1):
        raise ValueError('reasoning policy version must be a positive integer')
    stages = result.get('stages')
    if not isinstance(stages, list) or not stages or len(stages) > 100:
        raise ValueError('reasoning policy requires 1..100 stages')
    seen, steps = set(), set()
    for index, stage in enumerate(stages, 1):
        if not isinstance(stage, dict):
            raise ValueError('reasoning stage must be an object')
        key = _text(stage.get('key', stage.get('stageKey')), 'stage key')
        if key in seen:
            raise ValueError('reasoning stages must have unique keys')
        seen.add(key)
        stage['key'] = key
        step_key = _text(stage.get('stepKey', key), 'stage stepKey')
        if step_key in steps:
            raise ValueError('reasoning stages must have unique stepKey values')
        steps.add(step_key)
        stage['stepKey'] = step_key
        stage['sequence'] = index
        stage.setdefault('required', True)
        if type(stage['required']) is not bool:
            raise ValueError('reasoning stage required must be boolean')
        budget = stage.get('budget', {})
        _charges(budget, allow_extra=True)
        limit = stage.get('maxRounds', budget.get('maxRounds', 4))
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('stage maxRounds must be 1..100')
        stage['maxRounds'] = limit
    if execution_graph is not None:
        nodes = execution_graph.get('nodes') if isinstance(execution_graph, dict) else None
        if not isinstance(nodes, list) or not nodes or any(not isinstance(node, dict) for node in nodes):
            raise ValueError('Profile execution DAG is unavailable')
        node_keys = [_text(node.get('id'), 'execution node key') for node in nodes]
        if len(node_keys) != len(set(node_keys)) or steps != set(node_keys):
            raise ValueError('reasoning stages must exactly cover the Profile execution DAG')
    configs = normalize_quality_gates(result.get('qualityGates', result.get('gates', [])), stage_keys=seen)
    gates = {config['gateId']: config for config in configs}
    for stage in stages:
        stage_gates = stage.get('qualityGates', [])
        if (not isinstance(stage_gates, list) or len(stage_gates) > 100
                or any(not isinstance(key, str) or key not in gates for key in stage_gates)
                or len(set(stage_gates)) != len(stage_gates)):
            raise ValueError('stage qualityGates must reference unique configured gates')
        for key in stage_gates:
            selected = gates[key].get('stages', gates[key].get('stageKeys'))
            if gates[key].get('enabled') is False or (selected is not None and stage['key'] not in selected):
                raise ValueError('stage references a disabled or unselected quality gate')
    _charges(result.get('budgetLimits', {}))
    ontology = result.get('ontology', {})
    if not isinstance(ontology, dict):
        raise ValueError('reasoning ontology must be an object')
    for key in ('entityTypes', 'predicates'):
        values = ontology.get(key, [])
        if not isinstance(values, list) or len(values) > 500 or any(not isinstance(v, str) or not v for v in values):
            raise ValueError('invalid reasoning ontology ' + key)
    for key in ('allowAutomaticPublication', 'requireEvidenceBinding'):
        if key in result and type(result[key]) is not bool:
            raise ValueError(key + ' must be boolean')
    if result.get('allowAutomaticPublication') is True:
        raise ValueError('source ontology publication cannot be enabled by a reasoning policy')
    result['allowAutomaticPublication'] = False
    result.setdefault('requireEvidenceBinding', True)
    quality_bar = result.get('qualityBar', {})
    if not isinstance(quality_bar, dict):
        raise ValueError('reasoning qualityBar must be an object')
    minimum = quality_bar.get('minimumIndependentSources', 2)
    if type(minimum) is not int or not 1 <= minimum <= 500:
        raise ValueError('invalid minimum independent sources')
    return result


def _charges(value, allow_extra=False):
    if not isinstance(value, dict):
        raise ValueError('reasoning resource charges must be an object')
    charges = {}
    for key, amount in value.items():
        if allow_extra and key in {'maxRounds', 'maxRuntimeSeconds', 'maxParallelism'}:
            if type(amount) is not int or amount < 0:
                raise ValueError('invalid reasoning resource limit')
            continue
        if key not in RESOURCE_KEYS or type(amount) is not int or amount < 0:
            raise ValueError('invalid reasoning resource ' + str(key))
        charges[key] = amount
    return charges


def _identity(cursor, principal, role='reviewer'):
    from agent_admin.credentials import Principal
    if not isinstance(principal, Principal) or principal.kind != 'human' or not principal.permits(role):
        raise PermissionError('an authenticated human ' + role + ' is required')
    cursor.execute('SELECT * FROM agent_admin_identity WHERE principal_id=%s FOR SHARE', (principal.principal_id,))
    row = cursor.fetchone()
    if not row or not row['enabled'] or row['kind'] != 'human' or not ({role, 'admin'} & set(row['roles_json'])):
        raise PermissionError('human identity is disabled or lacks permission')
    if set(principal.roles) != set(row['roles_json']) or principal.display_name != row['display_name']:
        raise PermissionError('authenticated identity changed; authenticate again')


class ReasoningRepository:
    reasoning_installed = staticmethod(reasoning_installed)

    @contextmanager
    def _reasoning_cursor(self, cursor=None):
        if cursor is not None:
            yield cursor
        else:
            with self.connection_factory() as con:
                with con.cursor() as own:
                    yield own

    @staticmethod
    def _profile_execution_graph(cursor, agent_key, profile_version):
        cursor.execute('SELECT current_version FROM agent_definition WHERE agent_key=%s', (agent_key,))
        definition = cursor.fetchone()
        if not definition or str(definition['current_version']) != str(profile_version):
            raise ValueError('reasoning policy Profile version is unavailable')
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_execution_baseline') AS name")
        has_baseline = bool(cursor.fetchone()['name'])
        if has_baseline:
            cursor.execute('''SELECT graph_json FROM agent_execution_baseline
                          WHERE agent_key=%s AND profile_version=%s AND status='published'
                          ORDER BY baseline_version DESC LIMIT 1''', (agent_key, profile_version))
            row = cursor.fetchone()
        else:
            row = None
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_step') AS name")
        has_steps = bool(cursor.fetchone()['name'])
        if not has_steps:
            return row['graph_json'] if row else None
        cursor.execute('SELECT step_key FROM agent_step WHERE agent_key=%s ORDER BY sequence_no', (agent_key,))
        nodes = [{'id': item['step_key']} for item in cursor.fetchall()] or [{'id': 'research-envelope'}]
        if row and {item.get('id') for item in row['graph_json'].get('nodes', [])} == {item['id'] for item in nodes}:
            return row['graph_json']
        return {'nodes': nodes}

    def load_reasoning_policy(self, agent_key, version=None, *, profile_version=None, execution_graph=None):
        with self._reasoning_cursor() as cursor:
            if not reasoning_installed(cursor):
                return {}
            cursor.execute('SELECT current_version FROM agent_definition WHERE agent_key=%s', (agent_key,))
            definition = cursor.fetchone()
            if not definition:
                raise ValueError('agent not found')
            selected_profile = str(profile_version or definition['current_version'])
            if selected_profile != str(definition['current_version']):
                raise ValueError('reasoning policy Profile version is unavailable')
            if execution_graph is None:
                execution_graph = self._profile_execution_graph(cursor, agent_key, selected_profile)
            sql = 'SELECT profile_version,version,policy_json FROM agent_reasoning_policy WHERE agent_key=%s AND profile_version=%s'
            args = [agent_key, selected_profile]
            if version is not None:
                if type(version) is not int or version < 1:
                    raise ValueError('reasoning policy version must be a positive integer')
                sql += ' AND version=%s'
                args.append(version)
            cursor.execute(sql + ' ORDER BY version DESC LIMIT 1', args)
            row = cursor.fetchone()
            if not row:
                return {}
            value = validate_reasoning_policy(row['policy_json'], execution_graph=execution_graph)
            if value.get('agentKey') != agent_key or str(value.get('profileVersion')) != selected_profile or value.get('version') != row['version']:
                raise ValueError('reasoning policy database metadata mismatch')
            return value

    def save_reasoning_policy(self, agent_key, policy, actor, expected_version=None):
        normalized = validate_reasoning_policy(policy)
        with self._reasoning_cursor() as cursor:
            _identity(cursor, actor, 'admin')
            cursor.execute('SELECT current_version FROM agent_definition WHERE agent_key=%s FOR UPDATE', (agent_key,))
            definition = cursor.fetchone()
            if not definition:
                raise ValueError('agent not found')
            requested_profile = str(normalized.get('profileVersion') or definition['current_version'])
            if normalized.get('agentKey', agent_key) != agent_key or requested_profile != str(definition['current_version']):
                raise ValueError('reasoning policy agent/Profile version mismatch')
            graph = self._profile_execution_graph(cursor, agent_key, requested_profile)
            normalized = validate_reasoning_policy(normalized, execution_graph=graph)
            cursor.execute('SELECT version FROM agent_reasoning_policy WHERE agent_key=%s ORDER BY version DESC LIMIT 1', (agent_key,))
            row = cursor.fetchone()
            current = row['version'] if row else 0
            if expected_version is not None and expected_version != current:
                raise ValueError('reasoning policy version conflict; reload before saving')
            normalized.update({'policyId': _id('reasoning-policy'), 'agentKey': agent_key,
                               'profileVersion': requested_profile,
                               'version': current + 1})
            cursor.execute('''INSERT INTO agent_reasoning_policy(policy_id,agent_key,profile_version,version,policy_json,created_by)
                VALUES(%s,%s,%s,%s,%s,%s)''', (normalized['policyId'], agent_key, normalized['profileVersion'],
                normalized['version'], Json(normalized), actor.principal_id))
            for stage in normalized['stages']:
                cursor.execute('INSERT INTO agent_reasoning_stage(policy_id,stage_key,sequence_no,stage_json) VALUES(%s,%s,%s,%s)',
                               (normalized['policyId'], stage['key'], stage['sequence'], Json(stage)))
            return normalized

    def initialize_reasoning_run(self, run_id, policy, cursor=None):
        if not policy:
            return {'runId': run_id, 'status': 'unavailable', 'reason': 'profile-without-reasoning-policy'}
        normalized = validate_reasoning_policy(policy)
        version = normalized.get('version', 1)
        if type(version) is not int or version < 1:
            raise ValueError('reasoning policy version must be a positive integer')
        digest = reasoning_hash(normalized)
        with self._reasoning_cursor(cursor) as cur:
            if not reasoning_installed(cur):
                raise ValueError('reasoning persistence migration is not installed')
            cur.execute('SELECT * FROM agent_run_reasoning_snapshot WHERE run_id=%s', (run_id,))
            existing = cur.fetchone()
            if existing:
                if existing['policy_hash'] != digest:
                    raise ValueError('immutable run reasoning policy differs')
                return self._policy(existing)
            cur.execute('''INSERT INTO agent_run_reasoning_snapshot(run_id,policy_id,policy_version,policy_json,policy_hash)
                VALUES(%s,%s,%s,%s,%s) RETURNING *''',
                (run_id, normalized.get('policyId'), version, Json(normalized), digest))
            return self._policy(cur.fetchone())

    @staticmethod
    def _policy(row):
        if reasoning_hash(row['policy_json']) != row['policy_hash']:
            raise ValueError('reasoning policy snapshot hash mismatch')
        return {**deepcopy(row['policy_json']), 'runId': row['run_id'], 'policyHash': row['policy_hash'], 'status': 'active'}

    def get_reasoning_policy(self, run_id):
        with self._reasoning_cursor() as cursor:
            if reasoning_installed(cursor):
                cursor.execute('SELECT * FROM agent_run_reasoning_snapshot WHERE run_id=%s', (run_id,))
                row = cursor.fetchone()
                if row:
                    return self._policy(row)
        return {'runId': run_id, 'status': 'unavailable', 'reason': 'legacy-run-without-reasoning-snapshot'}

    @staticmethod
    def _reasoning_budget_installed(cursor):
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_reasoning_budget_binding') AS name")
        return bool(cursor.fetchone()['name'])

    @staticmethod
    def _reasoning_stage_usage(cursor, run_id, stage_key, settled=False):
        if settled:
            cursor.execute('''SELECT u.iteration,coalesce(s.effective_json,u.charges_json) AS charges_json
                FROM agent_reasoning_stage_usage u LEFT JOIN LATERAL(
                    SELECT effective_json FROM agent_reasoning_stage_settlement
                    WHERE usage_id=u.usage_id ORDER BY revision DESC LIMIT 1) s ON true
                WHERE u.run_id=%s AND u.stage_key=%s''', (run_id, stage_key))
        else:
            cursor.execute('SELECT iteration,charges_json FROM agent_reasoning_stage_usage WHERE run_id=%s AND stage_key=%s', (run_id, stage_key))
        return list(cursor.fetchall())

    def _reasoning_budget_targets(self, cursor, run_id, step_key):
        """Resolve every frozen ancestor allowance using canonical membership.

        Each delegation carries its parent's node in the immutable Run input;
        nested Actions therefore retain the original application's stage.
        """
        targets, seen, node = [], set(), step_key
        current = run_id
        while current:
            if current in seen or len(seen) >= 128:
                raise ValueError('budget parent chain is cyclic or too deep')
            seen.add(current)
            cursor.execute('''SELECT r.input_json,r.iteration,m.parent_run_id,m.root_run_id
                FROM agent_run r JOIN agent_run_budget_member m ON m.run_id=r.run_id WHERE r.run_id=%s''', (current,))
            row = cursor.fetchone()
            if not row:
                raise ValueError('canonical budget parent is absent')
            cursor.execute('SELECT policy_json FROM agent_run_reasoning_snapshot WHERE run_id=%s', (current,))
            snapshot = cursor.fetchone()
            if snapshot:
                stage = next((s for s in snapshot['policy_json'].get('stages', []) if s.get('stepKey', s['key']) == node), None)
                if not stage:
                    raise PermissionError('request has no frozen reasoning stage')
                targets.append({'runId': current, 'stageKey': stage['key'], 'iteration': int(row['iteration'] or 0) + 1})
            parent = row['parent_run_id']
            if not parent:
                if current != row['root_run_id']:
                    raise ValueError('budget parent chain does not reach its root')
                break
            envelope = row['input_json'] or {}
            if envelope.get('parentRunId') not in (None, parent):
                raise PermissionError('Run parent input disagrees with canonical membership')
            node = envelope.get('parentStepKey')
            current = parent
        return targets

    def enter_reasoning_stage(self, run_id, stage_key, iteration, operation_key):
        """Charge one global stage entry and enter the stage in one transaction."""
        with self._reasoning_cursor() as cursor:
            self._lock_budget(cursor, run_id)
            stage = self.reserve_reasoning_stage(run_id, stage_key, iteration, operation_key, {}, cursor=cursor)
            if stage.get('status') == 'unavailable':
                raise ValueError('reasoning snapshot absent')
            reservation = self._reserve_budget(run_id, {'reasoning_rounds': 1},
                operation_key=f'reasoning-round:{run_id}:{stage["stageKey"]}:{iteration}', cursor=cursor,
                reasoning_stage_entry=True)
            return {**stage, 'reservationId': reservation}

    def reserve_reasoning_stage(self, run_id, stage_key, iteration, operation_key, charges, cursor=None, billed_run_id=None):
        """Reserve stage allowances atomically with the caller's global ledger.

        This never charges the root budget itself. Passing its locked cursor
        makes global and stage reservations succeed or roll back together.
        """
        operation_key = _text(operation_key, 'stage operation key', 500)
        if type(iteration) is not int or iteration < 1:
            raise ValueError('reasoning iteration must be a positive integer')
        charges = _charges(charges)
        payload = {'stageKey': stage_key, 'iteration': iteration, 'charges': charges}
        if billed_run_id is not None and billed_run_id != run_id:
            payload['billedRunId'] = billed_run_id
        digest = reasoning_hash(payload)
        with self._reasoning_cursor(cursor) as cur:
            cur.execute('SELECT * FROM agent_run_reasoning_snapshot WHERE run_id=%s FOR UPDATE', (run_id,))
            row = cur.fetchone()
            if not row:
                return {'runId': run_id, 'status': 'unavailable'}
            policy = self._policy(row)
            stage = next((s for s in policy['stages'] if s['key'] == stage_key or s.get('stepKey') == stage_key), None)
            if not stage:
                raise ValueError('reasoning stage outside frozen policy')
            if stage_key != stage['key']:
                stage_key = stage['key']
                payload['stageKey'] = stage_key
                digest = reasoning_hash(payload)
            cur.execute('SELECT * FROM agent_reasoning_stage_usage WHERE run_id=%s AND operation_key=%s', (run_id, operation_key))
            existing = cur.fetchone()
            if existing:
                if existing['content_hash'] != digest:
                    raise ValueError('stage reservation idempotency payload changed')
                return {'usageId': existing['usage_id'], 'runId': run_id, 'stageKey': stage_key,
                        'iteration': iteration, 'charges': charges, 'idempotent': True}
            integrity = self._reasoning_budget_installed(cur)
            prior = self._reasoning_stage_usage(cur, run_id, stage_key, settled=integrity)
            iterations = {p['iteration'] for p in prior} | {iteration}
            if iteration > stage['maxRounds'] or len(iterations) > stage['maxRounds']:
                from .phase3_repository import BudgetExceededError
                raise BudgetExceededError('reasoning stage round budget exhausted')
            for resource, limit in _charges(stage.get('budget', {}), allow_extra=True).items():
                used = sum(p['charges_json'].get(resource, 0) for p in prior)
                if used + charges.get(resource, 0) > limit:
                    from .phase3_repository import BudgetExceededError
                    raise BudgetExceededError('reasoning stage resource budget exhausted: ' + resource)
            usage_id = _id('reasoning-usage')
            if billed_run_id is not None and not integrity:
                raise RuntimeError('reasoning budget integrity migration 072 is required')
            if integrity:
                cur.execute('''INSERT INTO agent_reasoning_stage_usage(usage_id,run_id,stage_key,iteration,operation_key,charges_json,content_hash,billed_run_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (usage_id, run_id, stage_key, iteration, operation_key, Json(charges), digest, billed_run_id or run_id))
            else:
                cur.execute('''INSERT INTO agent_reasoning_stage_usage(usage_id,run_id,stage_key,iteration,operation_key,charges_json,content_hash)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)''', (usage_id, run_id, stage_key, iteration, operation_key, Json(charges), digest))
            return {'usageId': usage_id, 'runId': run_id, 'stageKey': stage_key, 'iteration': iteration,
                    'charges': charges, 'idempotent': False}

    def _settle_reasoning_budget(self, cursor, reservation, revision, effective):
        if not self._reasoning_budget_installed(cursor):
            return {}
        cursor.execute('SELECT * FROM agent_reasoning_budget_binding WHERE reservation_id=%s ORDER BY owner_run_id', (reservation['reservation_id'],))
        bindings = list(cursor.fetchall())
        overruns = {}
        for binding in bindings:
            owner, stage_key = binding['owner_run_id'], binding['stage_key']
            cursor.execute('SELECT policy_json FROM agent_run_reasoning_snapshot WHERE run_id=%s FOR UPDATE', (owner,))
            policy = cursor.fetchone()['policy_json']
            stage = next(s for s in policy['stages'] if s['key'] == stage_key)
            prior = self._reasoning_stage_usage(cursor, owner, stage_key, settled=True)
            totals = {}
            for row in prior:
                for key, amount in row['charges_json'].items():
                    totals[key] = totals.get(key, 0) + amount
            cursor.execute('''SELECT effective_json FROM agent_reasoning_stage_settlement
                WHERE usage_id=%s ORDER BY revision DESC LIMIT 1''', (binding['usage_id'],))
            previous = cursor.fetchone()
            before = previous['effective_json'] if previous else reservation['charges_json']
            for key, amount in effective.items():
                totals[key] = totals.get(key, 0) + amount - before.get(key, 0)
            overrun = {key: totals.get(key, 0) - limit for key, limit in stage.get('budget', {}).items()
                       if key in RESOURCE_KEYS and totals.get(key, 0) > limit}
            cursor.execute('''INSERT INTO agent_reasoning_stage_settlement(
                settlement_id,usage_id,reservation_id,run_id,owner_run_id,revision,effective_json,overrun_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''', (_id('stage-settlement'), binding['usage_id'],
                reservation['reservation_id'], reservation['run_id'], owner, revision, Json(effective), Json(overrun)))
            if overrun:
                overruns[owner + ':' + stage_key] = overrun
        return overruns

    def append_reasoning_checkpoint(self, run_id, stage_key, checkpoint, operation_key, step_run_id=None):
        payload = _json(checkpoint, 'reasoning checkpoint')
        operation_key = _text(operation_key, 'checkpoint operation key', 500)
        stage_key = _text(stage_key, 'stage key')
        for key in ('knownEvidence', 'missingEvidence', 'candidateRelations', 'uncertainties'):
            payload.setdefault(key, [])
            if not isinstance(payload[key], list):
                raise ValueError('checkpoint ' + key + ' must be an array')
        payload.setdefault('iteration', 1)
        if type(payload['iteration']) is not int or payload['iteration'] < 1:
            raise ValueError('checkpoint iteration must be positive')
        digest = reasoning_hash({'stageKey': stage_key, 'stepRunId': step_run_id, 'checkpoint': payload})
        with self._reasoning_cursor() as cursor:
            cursor.execute('SELECT run_id FROM agent_run_reasoning_snapshot WHERE run_id=%s FOR UPDATE', (run_id,))
            if not cursor.fetchone():
                raise ValueError('reasoning snapshot absent')
            cursor.execute('SELECT * FROM agent_reasoning_checkpoint WHERE run_id=%s AND operation_key=%s', (run_id, operation_key))
            existing = cursor.fetchone()
            if existing:
                if existing['checkpoint_hash'] != digest:
                    raise ValueError('checkpoint idempotency payload changed')
                return self._checkpoint(existing)
            cursor.execute('''INSERT INTO agent_reasoning_checkpoint(checkpoint_id,run_id,stage_key,step_run_id,operation_key,checkpoint_json,checkpoint_hash)
                VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (_id('reasoning-checkpoint'), run_id, stage_key, step_run_id, operation_key, Json(payload), digest))
            return self._checkpoint(cursor.fetchone())

    @staticmethod
    def _checkpoint(row):
        return {**deepcopy(row['checkpoint_json']), 'checkpointId': row['checkpoint_id'], 'runId': row['run_id'],
                'stageKey': row['stage_key'], 'stepRunId': row['step_run_id'], 'operationKey': row['operation_key'],
                'checkpointHash': row['checkpoint_hash'], 'createdAt': row['created_at'].isoformat()}

    def list_reasoning_checkpoints(self, run_id):
        with self._reasoning_cursor() as cursor:
            if not reasoning_installed(cursor):
                return []
            cursor.execute('SELECT * FROM agent_reasoning_checkpoint WHERE run_id=%s ORDER BY checkpoint_seq', (run_id,))
            return [self._checkpoint(r) for r in cursor.fetchall()]

    def record_reasoning_gates(self, run_id, checkpoint_id, results):
        if not isinstance(results, list) or len(results) > 200:
            raise ValueError('quality gate results must be a bounded array')
        output = []
        with self._reasoning_cursor() as cursor:
            cursor.execute('SELECT run_id FROM agent_run_reasoning_snapshot WHERE run_id=%s FOR UPDATE', (run_id,))
            if not cursor.fetchone():
                raise ValueError('reasoning snapshot absent')
            for index, result in enumerate(results):
                payload = _json(result, 'quality gate result')
                gate_key = _text(payload.get('gateKey', payload.get('gateId', payload.get('key'))), 'quality gate key')
                status = payload.get('status')
                aliases = {'pass': 'passed', 'fail': 'failed', 'warn': 'warning'}
                status = aliases.get(status, status)
                if status not in {'passed', 'failed', 'warning', 'skipped', 'unavailable', 'not-applicable'}:
                    raise ValueError('invalid quality gate status')
                payload['status'] = status
                payload['gateKey'] = gate_key
                operation = _text(payload.get('operationKey') or checkpoint_id + ':' + gate_key + ':' + str(index), 'gate operation key', 500)
                digest = reasoning_hash({'checkpointId': checkpoint_id, 'result': payload})
                cursor.execute('SELECT * FROM agent_reasoning_gate_result WHERE run_id=%s AND operation_key=%s', (run_id, operation))
                prior = cursor.fetchone()
                if prior:
                    if prior['result_hash'] != digest:
                        raise ValueError('quality gate idempotency payload changed')
                    output.append(self._gate(prior))
                    continue
                cursor.execute('''INSERT INTO agent_reasoning_gate_result(result_id,run_id,checkpoint_id,gate_key,operation_key,status,blocking,result_json,result_hash)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                    (_id('reasoning-gate'), run_id, checkpoint_id, gate_key, operation, status, bool(payload.get('blocking', False)), Json(payload), digest))
                output.append(self._gate(cursor.fetchone()))
        return output

    @staticmethod
    def _gate(row):
        return {**deepcopy(row['result_json']), 'resultId': row['result_id'], 'runId': row['run_id'],
                'checkpointId': row['checkpoint_id'], 'gateKey': row['gate_key'], 'status': row['status'],
                'blocking': row['blocking'], 'resultHash': row['result_hash'], 'operationKey': row['operation_key'],
                'createdAt': row['created_at'].isoformat()}

    def list_reasoning_gates(self, run_id):
        with self._reasoning_cursor() as cursor:
            if not reasoning_installed(cursor):
                return []
            cursor.execute('SELECT * FROM agent_reasoning_gate_result WHERE run_id=%s ORDER BY result_seq', (run_id,))
            return [self._gate(r) for r in cursor.fetchall()]

    @staticmethod
    def _candidate(row, events):
        payload = deepcopy(row['candidate_json'])
        support = 'supported' if events and events[-1]['status'] == 'support-reviewed' else 'unreviewed'
        payload.update({'candidateId': row['candidate_id'], 'runId': row['run_id'],
                        'checkpointId': row['checkpoint_id'], 'contentHash': row['content_hash'],
                        'truthStatus': 'unverified', 'supportStatus': support,
                        'status': events[-1]['status'] if events else 'proposed', 'history': events,
                        'createdAt': row['created_at'].isoformat()})
        return payload

    @staticmethod
    def _model_artifact(cursor, run_id, artifact_id):
        if not artifact_id:
            return None
        cursor.execute('SELECT artifact_id,artifact_type,content_json FROM agent_run_artifact WHERE run_id=%s AND artifact_id=%s',
                       (run_id, artifact_id))
        row = cursor.fetchone()
        return dict(row) if row else None

    @staticmethod
    def _artifact_request(content):
        """Recover the JSON request payload from the redacted model-input artifact."""
        content = content if isinstance(content, dict) else {}
        request = content.get('request', content)
        messages = request.get('messages') if isinstance(request, dict) else None
        values = []
        for message in messages or []:
            value = message.get('content') if isinstance(message, dict) else None
            if isinstance(value, str):
                try:
                    values.append(json.loads(value))
                except (TypeError, ValueError):
                    continue
        return values

    def validate_finding_reviews(self, run_id, findings, cursor=None):
        """Bind whole findings to immutable generation and skeptical review calls.

        Only this Run and its hash-bound perspective Actions are eligible.
        Caller-supplied verdicts and call IDs select records, never prove support.
        """
        if not findings:
            return []
        with self._reasoning_cursor(cursor) as cur:
            cur.execute('''SELECT * FROM agent_model_call WHERE (run_id=%s OR run_id IN (
                SELECT a.child_run_id FROM agent_action_invocation a
                JOIN agent_run_artifact o ON o.artifact_id=a.output_artifact_id AND o.run_id=a.child_run_id
                WHERE a.run_id=%s AND a.parent_step_key='analyze-by-perspective'
                  AND a.status IN ('completed','completed-with-gaps','requires-human-review')
                  AND o.content_hash=a.output_artifact_hash)) AND status='succeeded'
                AND stage IN ('future-battlefield-perspectives','future-battlefield-support-review')''', (run_id, run_id))
            calls = {}
            for row in cur.fetchall():
                call = dict(row)
                source = self._model_artifact(cur, call['run_id'], call.get('input_artifact_id'))
                output = self._model_artifact(cur, call['run_id'], call.get('output_artifact_id'))
                if source and output and source['artifact_type'] == 'model-input' and output['artifact_type'] == 'model-output':
                    calls[call['call_id']] = (call, self._artifact_request(source.get('content_json')),
                                            (output.get('content_json') or {}).get('output') or {})
            proofs = []
            fields = ('perspectiveKey', 'statement', 'supportingClaims', 'conditions', 'counterArguments', 'unknowns')
            for finding in findings:
                exact_fields = fields + (('findingId', 'questionIds', 'epistemicKind', 'modality') if finding.get('findingId') else ())
                support = finding.get('supportReview') or {}
                generator = calls.get(support.get('generatorCallId'))
                reviewer = calls.get(support.get('reviewCallId'))
                if not generator or not reviewer or generator[0]['run_id'] != reviewer[0]['run_id']:
                    continue
                if generator[0]['stage'] != 'future-battlefield-perspectives' or reviewer[0]['stage'] != 'future-battlefield-support-review':
                    continue
                proposals = [p for p in generator[2].get('findings', []) if
                             all(p.get(k, []) == finding.get(k, []) for k in exact_fields)]
                if len(proposals) != 1:
                    continue
                proposal = proposals[0]
                reviewed = [p for p in reviewer[2].get('reviews', []) if
                            p.get('perspectiveKey') == finding.get('perspectiveKey') and p.get('verdict') == 'supported'
                            and (not finding.get('findingId') or p.get('findingId') == finding['findingId'])]
                if len(reviewed) != 1:
                    continue
                from .decision_semantics import review_scope_preserved
                if (not review_scope_preserved(reviewed[0], allow_legacy=True) or
                        support.get('scopeAssessment') != reviewed[0].get('scopeAssessment')):
                    continue
                if finding.get('findingId'):
                    question_reviews = reviewed[0].get('questionVerdicts', [])
                    answered = [q['questionId'] for q in question_reviews if q.get('verdict') == 'answered']
                    if (finding.get('answeredQuestionIds', []) != answered or
                            support.get('questionVerdicts') != question_reviews):
                        continue
                inputs = [p for request in reviewer[1] if isinstance(request, dict)
                          for p in request.get('proposals', []) if p.get('perspective', {}).get('key') == finding.get('perspectiveKey')
                          and (not finding.get('findingId') or p.get('proposal', {}).get('findingId') == finding['findingId'])]
                if not any(reasoning_hash(p.get('proposal')) == reasoning_hash(proposal) and
                           set(proposal.get('supportingClaims', [])) <= {e.get('evidenceId') for e in p.get('evidence', [])}
                           for p in inputs):
                    continue
                checked = {v.get('id') for v in reviewed[0].get('scenarioAssessments', []) if v.get('verdict') == 'supported'}
                scenarios = [deepcopy(v) for v in proposal.get('scenarioAssessments', []) if v.get('id') in checked]
                proofs.append({'perspectiveKey': finding['perspectiveKey'], 'statement': finding['statement'],
                    **({'findingId': finding['findingId'], 'questionVerdicts': deepcopy(reviewed[0].get('questionVerdicts', []))} if finding.get('findingId') else {}),
                    'generatorCallId': generator[0]['call_id'], 'reviewCallId': reviewer[0]['call_id'],
                    'scenarioAssessments': scenarios})
            return proofs

    def validate_support_reviews(self, run_id, relations, cursor=None):
        """Return proofs from this Run or a completed, hash-bound perspective Action.

        A proof is deliberately independent of the relation's ``supportReview``
        payload. It binds the exact proposal triple, perspective and evidence
        IDs to the persisted generation/review call pair and review output.
        """
        if not isinstance(relations, list) or not relations:
            return []
        from .reasoning_gates import relation_review_binding
        with self._reasoning_cursor(cursor) as cur:
            cur.execute("SELECT to_regclass(current_schema() || '.agent_model_call') AS name")
            if not cur.fetchone()['name']:
                return []
            cur.execute('''SELECT * FROM agent_model_call WHERE (run_id=%s OR run_id IN (
                           SELECT a.child_run_id FROM agent_action_invocation a
                           JOIN agent_run_artifact o ON o.artifact_id=a.output_artifact_id AND o.run_id=a.child_run_id
                           WHERE a.run_id=%s AND a.parent_step_key='analyze-by-perspective'
                             AND a.status IN ('completed','completed-with-gaps','requires-human-review')
                             AND o.content_hash=a.output_artifact_hash))
                           AND status='succeeded' AND stage IN ('future-battlefield-perspectives','future-battlefield-support-review')
                           ORDER BY created_at,call_id''', (run_id,run_id))
            calls = [dict(row) for row in cur.fetchall()]
            generated, reviewed = [], []
            for call in calls:
                input_artifact = self._model_artifact(cur, call['run_id'], call.get('input_artifact_id'))
                output_artifact = self._model_artifact(cur, call['run_id'], call.get('output_artifact_id'))
                if not input_artifact or not output_artifact or input_artifact['artifact_type'] != 'model-input' \
                        or output_artifact['artifact_type'] != 'model-output':
                    continue
                output = (output_artifact.get('content_json') or {}).get('output')
                if not isinstance(output, dict):
                    continue
                entry = {'call': call, 'input': input_artifact.get('content_json') or {},
                         'output': output, 'inputJson': self._artifact_request(input_artifact.get('content_json'))}
                if call.get('stage') == 'future-battlefield-perspectives':
                    generated.append(entry)
                else:
                    reviewed.append(entry)
            proofs = []
            for relation in relations:
                binding = relation_review_binding(relation)
                perspective = relation.get('perspectiveKey')
                namespace = relation.get('findingId') or perspective
                def matches(candidate):
                    original = {**candidate, 'perspectiveKey': perspective}
                    if relation.get('findingId'):
                        original['findingId'] = relation['findingId']
                    if relation_review_binding(original) == binding:
                        return True
                    # build_ontology namespaces IDs for display. Reconstruct
                    # exactly that projection from the immutable proposal;
                    # never trust a caller-supplied "original relation" ID.
                    if not all(isinstance(candidate.get(k), str) for k in ('id', 'subject', 'object')):
                        return False
                    projected = {**original, 'id': 'assertion:' + str(namespace) + ':' + candidate['id'],
                        'subject': 'entity:' + str(namespace) + ':' + candidate['subject'],
                        'object': 'entity:' + str(namespace) + ':' + candidate['object']}
                    return relation_review_binding(projected) == binding
                # The runtime carries the exact call ids used for this relation
                # when a perspective Action iterates.  Use them as selectors
                # when present, then re-validate the calls and their payloads
                # below.  Without this filter an older child iteration with an
                # identical proposal could accidentally be selected.
                supplied_support = relation.get('supportReview') if isinstance(relation.get('supportReview'), dict) else {}
                supplied_generator_id = supplied_support.get('generatorCallId')
                supplied_review_id = supplied_support.get('reviewCallId')
                generator, original_relation_id = None, None
                for item in generated:
                    if supplied_generator_id and item['call'].get('call_id') != supplied_generator_id:
                        continue
                    findings = item['output'].get('findings', [])
                    for finding in findings:
                        if finding.get('perspectiveKey') != perspective:
                            continue
                        if relation.get('findingId') and finding.get('findingId') != relation['findingId']:
                            continue
                        candidate = next((candidate for candidate in finding.get('relations', [])
                                          if isinstance(candidate, dict) and matches(candidate)), None)
                        if candidate:
                            generator = item
                            original_relation_id = candidate.get('id')
                            break
                    if generator:
                        break
                if not generator:
                    continue
                review = None
                for item in reviewed:
                    if item['call']['run_id'] != generator['call']['run_id']:
                        continue
                    if supplied_review_id and item['call'].get('call_id') != supplied_review_id:
                        continue
                    for reviewed_row in item['output'].get('reviews', []):
                        from .decision_semantics import review_scope_preserved
                        if (reviewed_row.get('perspectiveKey') != perspective or reviewed_row.get('verdict') != 'supported'
                                or not review_scope_preserved(reviewed_row, allow_legacy=True)):
                            continue
                        if relation.get('findingId') and reviewed_row.get('findingId') != relation['findingId']:
                            continue
                        relation_result = next((row for row in reviewed_row.get('relations', [])
                                                if row.get('id') == original_relation_id and row.get('verdict') == 'supported'), None)
                        if not relation_result:
                            continue
                        # The review input must carry the exact proposal/evidence.
                        exact_proposal = False
                        for proposal in item.get('inputJson', []):
                            if not isinstance(proposal, dict):
                                continue
                            for supplied in proposal.get('proposals', []):
                                if supplied.get('perspective', {}).get('key') != perspective:
                                    continue
                                body = supplied.get('proposal', {})
                                if relation.get('findingId') and body.get('findingId') != relation['findingId']:
                                    continue
                                if any(matches(edge)
                                       for edge in body.get('relations', []) if isinstance(edge, dict)):
                                    exact_proposal = True
                                    break
                            if exact_proposal:
                                break
                        if not exact_proposal:
                            continue
                        review = (item, relation_result)
                        break
                    if review:
                        break
                if not review:
                    continue
                review_item, _ = review
                generator_call, review_call = generator['call'], review_item['call']
                proofs.append({'bindingHash': binding, 'verdict': 'supported',
                               'reviewCallId': review_call.get('call_id'),
                               'reviewArtifactId': review_call.get('output_artifact_id'),
                               'generatorCallId': generator_call.get('call_id'),
                               'generatorArtifactId': generator_call.get('output_artifact_id'),
                               'generatorModel': generator_call.get('actual_model') or generator_call.get('requested_model'),
                               'reviewerModel': review_call.get('actual_model') or review_call.get('requested_model'),
                               'reviewIndependence': 'independent-model-review' if
                                   (generator_call.get('actual_model') or generator_call.get('requested_model')) !=
                                   (review_call.get('actual_model') or review_call.get('requested_model'))
                                   else 'correlated-same-model'})
            return proofs

    @staticmethod
    def _events(cursor, run_id, candidate_id):
        cursor.execute('SELECT * FROM agent_relation_candidate_event WHERE run_id=%s AND candidate_id=%s ORDER BY event_seq', (run_id, candidate_id))
        return [{**deepcopy(r['event_json']), 'eventId': r['event_id'], 'status': r['status'], 'action': r['action'],
                 'actorId': r['actor_id'], 'operationKey': r['operation_key'], 'createdAt': r['created_at'].isoformat()}
                for r in cursor.fetchall()]

    @staticmethod
    def _event(cursor, run_id, candidate_id, status, action, actor_id, operation_key, payload):
        event_id = _id('relation-event')
        cursor.execute('''INSERT INTO agent_relation_candidate_event(event_id,run_id,candidate_id,status,action,actor_id,operation_key,event_json,content_hash)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (event_id, run_id, candidate_id, status, action, actor_id, operation_key, Json(payload), reasoning_hash(payload)))
        return event_id

    def upsert_relation_candidates(self, run_id, checkpoint_id, candidates):
        if not isinstance(candidates, list) or len(candidates) > 1000:
            raise ValueError('relation candidates must be a bounded array')
        output = []
        with self._reasoning_cursor() as cursor:
            cursor.execute('SELECT run_id FROM agent_run_reasoning_snapshot WHERE run_id=%s FOR UPDATE', (run_id,))
            if not cursor.fetchone():
                raise ValueError('reasoning snapshot absent')
            for supplied in candidates:
                payload = _json(supplied, 'relation candidate')
                candidate_id = payload.pop('candidateId', None)
                for name in ('status', 'history', 'runId', 'checkpointId', 'contentHash', 'createdAt', 'supportStatus'):
                    payload.pop(name, None)
                for name in ('subject', 'predicate', 'object'):
                    if not isinstance(payload.get(name), (str, dict)) or not payload[name]:
                        raise ValueError('relation candidate requires ' + name)
                evidence_ids = payload.get('evidenceIds', [])
                if not isinstance(evidence_ids, list) or len(evidence_ids) > 500:
                    raise ValueError('invalid relation evidence references')
                payload['evidenceIds'] = sorted({_text(e, 'relation evidence ID') for e in evidence_ids})
                payload['truthStatus'] = 'unverified'
                payload['truthVerified'] = False
                payload['verificationStatus'] = 'unverified'
                payload['reviewStatus'] = 'pending'
                support = payload.get('supportReview', {})
                if not isinstance(support, dict):
                    raise ValueError('relation supportReview must be an object')
                reviewed_evidence = support.get('evidenceIds', [])
                if not isinstance(reviewed_evidence, list) or len(reviewed_evidence) > 500:
                    raise ValueError('invalid relation support review evidence references')
                if reviewed_evidence:
                    support['evidenceIds'] = sorted({_text(e, 'support review evidence ID') for e in reviewed_evidence})
                trusted_proofs = self.validate_support_reviews(run_id, [payload], cursor=cursor)
                trusted_proof = trusted_proofs[0] if trusted_proofs else None
                if trusted_proof:
                    support.update({key: trusted_proof[key] for key in
                                    ('reviewCallId', 'reviewArtifactId', 'generatorCallId', 'generatorArtifactId')})
                    support['reviewIndependence'] = trusted_proof['reviewIndependence']
                    support['method'] = 'separate-model-review'
                    support['verdict'] = 'supported'
                    support['evidenceIds'] = list(payload['evidenceIds'])
                payload['supportReview'] = support
                digest = reasoning_hash(payload)
                relation_key = reasoning_hash({k: payload[k] for k in ('subject', 'predicate', 'object')})
                if candidate_id:
                    candidate_id = _text(candidate_id, 'relation candidate ID')
                    cursor.execute('SELECT * FROM agent_relation_candidate WHERE candidate_id=%s', (candidate_id,))
                else:
                    cursor.execute('SELECT * FROM agent_relation_candidate WHERE run_id=%s AND relation_key=%s AND content_hash=%s', (run_id, relation_key, digest))
                row = cursor.fetchone()
                if row:
                    if row['run_id'] != run_id or row['content_hash'] != digest:
                        raise ValueError('immutable relation candidate idempotency payload changed')
                    output.append(self._candidate(row, self._events(cursor, run_id, row['candidate_id'])))
                    continue
                candidate_id = candidate_id or _id('relation-candidate')
                cursor.execute('''INSERT INTO agent_relation_candidate(candidate_id,run_id,checkpoint_id,relation_key,candidate_json,content_hash)
                    VALUES(%s,%s,%s,%s,%s,%s) RETURNING *''', (candidate_id, run_id, checkpoint_id, relation_key, Json(payload), digest))
                row = cursor.fetchone()
                statuses = ['proposed']
                if payload['evidenceIds']:
                    statuses.append('evidence-bound')
                if trusted_proof and support.get('verdict') == 'supported' and payload['evidenceIds'] and support.get('method') \
                        and support.get('evidenceIds') == payload['evidenceIds']:
                    statuses.append('support-reviewed')
                elif support.get('verdict') in {'conflicted', 'contradicted'} or payload.get('conflicts'):
                    statuses.append('conflicted')
                elif support.get('verdict') in {'unsupported', 'insufficient', 'not-supported'}:
                    statuses.append('needs-more-evidence')
                for status in statuses:
                    self._event(cursor, run_id, candidate_id, status, 'initialize', 'reasoning-harness',
                                candidate_id + ':initialize:' + status, {'checkpointId': checkpoint_id, 'contentHash': digest})
                output.append(self._candidate(row, self._events(cursor, run_id, candidate_id)))
        return output

    def list_relation_candidates(self, run_id):
        with self._reasoning_cursor() as cursor:
            if not reasoning_installed(cursor):
                return []
            cursor.execute('SELECT * FROM agent_relation_candidate WHERE run_id=%s ORDER BY created_at,candidate_id', (run_id,))
            rows = list(cursor.fetchall())
            return [self._candidate(r, self._events(cursor, run_id, r['candidate_id'])) for r in rows]

    def review_relation_candidate(self, run_id, candidate_id, action, reviewer, comment='', idempotency_key=None):
        from agent_admin.credentials import Principal
        if action not in REVIEW_ACTIONS:
            raise ValueError('unknown relation review action')
        if not isinstance(comment, str) or len(comment) > 4000:
            raise ValueError('invalid relation review comment')
        if not isinstance(reviewer, Principal) or not reviewer.permits('reviewer'):
            raise PermissionError('an authenticated human reviewer is required')
        operation = _text(idempotency_key or _id('relation-review'), 'relation review operation key', 500)
        payload = {'candidateId': candidate_id, 'action': action, 'reviewerId': reviewer.principal_id, 'comment': comment}
        with control_context(run_id), self._reasoning_cursor() as cursor:
            _identity(cursor, reviewer)
            # Explicit context also works for an unwrapped factory in trusted
            # service callers. It is never constructed from model/browser data.
            cursor.execute("SELECT set_config('agent_engine.control_run_id',%s,true)", (run_id,))
            cursor.execute('SELECT run_id FROM agent_run WHERE run_id=%s FOR UPDATE', (run_id,))
            if not cursor.fetchone():
                raise ValueError('run not found')
            cursor.execute('SELECT * FROM agent_relation_candidate WHERE run_id=%s AND candidate_id=%s FOR UPDATE', (run_id, candidate_id))
            row = cursor.fetchone()
            if not row:
                raise ValueError('relation candidate absent or belongs to a different run')
            cursor.execute('SELECT * FROM agent_relation_candidate_event WHERE run_id=%s AND operation_key=%s', (run_id, operation))
            prior = cursor.fetchone()
            if prior:
                if prior['content_hash'] != reasoning_hash(payload):
                    raise ValueError('relation review idempotency payload changed')
                return self._candidate(row, self._events(cursor, run_id, candidate_id))
            events = self._events(cursor, run_id, candidate_id)
            current = events[-1]['status'] if events else 'proposed'
            if current in {'withdrawn', 'rejected'} and action not in {'request-evidence'}:
                raise ValueError('withdrawn or rejected candidate requires new evidence before review')
            if current == 'published' and action != 'withdraw':
                raise ValueError('published relation can only be withdrawn')
            support = row['candidate_json'].get('supportReview', {})
            if action == 'accept':
                trusted = self.validate_support_reviews(run_id, [row['candidate_json']], cursor=cursor)
                if current != 'support-reviewed' or support.get('verdict') != 'supported' or not support.get('method') \
                        or sorted(set(support.get('evidenceIds', []))) != row['candidate_json']['evidenceIds'] \
                        or not row['candidate_json']['evidenceIds'] or not trusted:
                    raise ValueError('acceptance requires bound supported evidence and resolved conflicts')
                cursor.execute("SELECT 1 FROM agent_reasoning_gate_result WHERE run_id=%s AND checkpoint_id=%s AND gate_key='relation-support-reviewed' AND status='passed' LIMIT 1", (run_id, row['checkpoint_id']))
                if not cursor.fetchone():
                    raise ValueError('acceptance requires a persisted passing relation support quality gate')
            if action == 'publish':
                if current != 'accepted-as-unverified':
                    raise ValueError('publication requires prior human acceptance')
                cursor.execute("SELECT 1 FROM agent_reasoning_gate_result WHERE run_id=%s AND blocking AND status NOT IN ('passed','skipped','not-applicable') LIMIT 1", (run_id,))
                if cursor.fetchone():
                    raise ValueError('publication blocked by unresolved quality gates')
                cursor.execute('SELECT 1 FROM agent_reasoning_gate_result WHERE run_id=%s LIMIT 1', (run_id,))
                if not cursor.fetchone():
                    raise ValueError('publication requires quality gate results')
            status = {'accept': 'accepted-as-unverified', 'reject': 'rejected', 'request-evidence': 'needs-more-evidence',
                      'mark-conflict': 'conflicted', 'withdraw': 'withdrawn', 'publish': 'published'}[action]
            event_id = self._event(cursor, run_id, candidate_id, status, action, reviewer.principal_id, operation, payload)
            if action == 'publish':
                publication_id, artifact_id = _id('relation-publication'), _id('artifact')
                artifact = {'publicationId': publication_id, 'runId': run_id, 'candidateId': candidate_id,
                            'candidate': deepcopy(row['candidate_json']), 'contentHash': row['content_hash'],
                            'reviewerId': reviewer.principal_id, 'reviewEventId': event_id,
                            'truthStatus': 'unverified', 'scope': 'run-only', 'sourceOntologyWritten': False}
                cursor.execute('''INSERT INTO agent_run_artifact(artifact_id,run_id,artifact_type,content_json,content_hash)
                    VALUES(%s,%s,'reviewed-relation-candidate',%s,%s)''', (artifact_id, run_id, Json(artifact), reasoning_hash(artifact)))
                cursor.execute('''INSERT INTO agent_relation_publication(publication_id,run_id,candidate_id,review_event_id,artifact_id,publication_json)
                    VALUES(%s,%s,%s,%s,%s,%s)''', (publication_id, run_id, candidate_id, event_id, artifact_id, Json(artifact)))
            return self._candidate(row, self._events(cursor, run_id, candidate_id))
