"""Shared, versioned connector admission; never owns retries or budgets.

Connection addresses and models come exclusively from a Run's immutable
manifest (or its legacy Profile). Live policy may stop or tighten access, but
cannot replace a provider, endpoint, credential, or data source.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import asyncio
import hashlib
import json
import logging
import re
import secrets
import time

from psycopg2.extras import Json


DEFAULT_POLICY = {
    'maxConcurrent': 4, 'callsPerMinute': 60, 'leaseSeconds': 300,
    'failureThreshold': 5, 'cooldownSeconds': 60, 'enabled': True,
}
POLICY_BOUNDS = {
    'maxConcurrent': (1, 128), 'callsPerMinute': (1, 100000),
    'leaseSeconds': (5, 3600), 'failureThreshold': (1, 100),
    'cooldownSeconds': (1, 3600),
}
_COLUMNS = {
    'maxConcurrent': 'max_concurrency', 'callsPerMinute': 'requests_per_minute',
    'leaseSeconds': 'lease_seconds', 'failureThreshold': 'failure_threshold',
    'cooldownSeconds': 'cooldown_seconds', 'enabled': 'enabled',
}
_REF = re.compile(r'(?:provider|source|local|http):[A-Za-z0-9_.:-]{1,200}')
_LOG = logging.getLogger(__name__)


class ConnectorAdmissionError(PermissionError):
    def __init__(self, code, retry_after=None):
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False, default=str).encode()).hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not _REF.fullmatch(value):
        raise ValueError('invalid-connector-reference')
    return value


def installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_run_connector_snapshot') AS name")
    return bool((cursor.fetchone() or {}).get('name'))


def _policy(row=None, ref=None):
    row = row or {}
    result = {key: row.get(column, default) for key, default in DEFAULT_POLICY.items()
              for column in [_COLUMNS[key]]}
    result.update(connectorRef=ref or row.get('connector_ref'),
                  policyVersion=row.get('policy_version', 0))
    for field, column in (('updatedAt', 'updated_at'), ('updatedBy', 'updated_by')):
        if row.get(column) is not None:
            value = row[column]
            result[field] = value.isoformat() if hasattr(value, 'isoformat') else value
    return result


def _normalise_policy(value):
    result = {**DEFAULT_POLICY, **{k: v for k, v in (value or {}).items() if k in DEFAULT_POLICY}}
    for key, (lower, upper) in POLICY_BOUNDS.items():
        if type(result[key]) is not int or not lower <= result[key] <= upper:
            raise ValueError('invalid-connector-policy-' + key)
    if type(result['enabled']) is not bool:
        raise ValueError('invalid-connector-policy-enabled')
    return result


def freeze_cursor(cursor, run_id, profile):
    """Append the separate manifest without changing historical Profile hashes."""
    if not installed(cursor):
        return None
    from .contracts import connector_manifest
    manifest = deepcopy(connector_manifest(profile))
    for connection in manifest.get('connections', []):
        ref = _identifier(connection['ref'])
        cursor.execute('SELECT * FROM connector_policy WHERE connector_ref=%s', (ref,))
        row = cursor.fetchone()
        connection['policy'] = _policy(row, ref)
        if not connection['policy']['enabled']:
            raise ConnectorAdmissionError('connector-disabled')
    manifest.pop('manifestHash', None)
    digest = _hash(manifest)
    manifest['manifestHash'] = digest
    cursor.execute('''INSERT INTO agent_run_connector_snapshot(run_id,manifest_json,manifest_hash)
        VALUES(%s,%s,%s)''', (run_id, Json(manifest), digest))
    return digest


class AttemptReceipt:
    def __init__(self, runtime=None, lease=None, identity=None):
        self.runtime, self.lease, self.identity = runtime, lease, identity or {}
        self.result = None
        self.dispatched = False
        self.cached = False
        self.started = time.monotonic()

    def start_call(self):
        self.dispatched = True

    def cache_hit(self):
        self.cached = True

    def record(self, status, *, error_code=None, call_id=None, latency_ms=None):
        self.result = {'status': status, 'error_code': error_code,
            'call_id': call_id, 'latency_ms': latency_ms}


@asynccontextmanager
async def attempt_scope(repository, ctx, **identity):
    """Injected small repositories need no connector DB or transport mocks."""
    method = getattr(repository, 'connector_attempt', None)
    if callable(method):
        async with method(ctx, **identity) as receipt:
            yield receipt
    else:
        yield AttemptReceipt()


def _failure(code):
    """Return safe class and whether an outcome indicates service health."""
    code = code or 'connector-operation-failed'
    if 'invalid-output' in code or 'schema' in code or 'validation' in code:
        return 'output-contract', False
    if 'input-invalid' in code or 'not-dispatched' in code:
        return 'input-contract', False
    if 'policy-denied' in code:
        return 'authorization', False
    if 'auth' in code or 'credential' in code or 'permission' in code:
        return 'authentication', True
    if 'rate' in code or '429' in code:
        return 'rate-limit', True
    if 'timeout' in code or 'connection' in code or 'transport' in code:
        return 'transport', True
    if 'cancel' in code or 'budget' in code:
        return 'execution', False
    return 'service', True


class ConnectorRuntime:
    def __init__(self, connection_factory):
        self.factory = connection_factory

    @staticmethod
    def _lock(cursor, ref):
        cursor.execute("SET LOCAL lock_timeout='5s'")
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                       ('connector-runtime:' + ref,))

    def get_policy(self, ref):
        ref = _identifier(ref)
        with self.factory() as con, con.cursor() as cursor:
            cursor.execute('SELECT * FROM connector_policy WHERE connector_ref=%s', (ref,))
            return _policy(cursor.fetchone(), ref)

    def set_policy(self, ref, body, actor_id):
        ref = _identifier(ref)
        if not isinstance(body, dict) or set(body) - {*DEFAULT_POLICY, 'expectedPolicyVersion', 'reason'}:
            raise ValueError('invalid-connector-policy-fields')
        if not isinstance(actor_id, str) or not 1 <= len(actor_id) <= 200:
            raise ValueError('invalid-policy-actor')
        reason = body.get('reason', '')
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError('invalid-policy-reason')
        expected = body.get('expectedPolicyVersion')
        if expected is not None and (type(expected) is not int or expected < 0):
            raise ValueError('invalid-policy-version')
        with self.factory() as con, con.cursor() as cursor:
            self._lock(cursor, ref)
            cursor.execute('SELECT * FROM connector_policy WHERE connector_ref=%s', (ref,))
            current = _policy(cursor.fetchone(), ref)
            if expected is not None and expected != current['policyVersion']:
                raise ConnectorAdmissionError('connector-policy-version-conflict')
            value = _normalise_policy({**current, **body})
            version = current['policyVersion'] + 1
            cursor.execute('''INSERT INTO connector_policy(connector_ref,policy_version,max_concurrency,
                requests_per_minute,lease_seconds,failure_threshold,cooldown_seconds,enabled,updated_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(connector_ref) DO UPDATE SET policy_version=EXCLUDED.policy_version,
                max_concurrency=EXCLUDED.max_concurrency,requests_per_minute=EXCLUDED.requests_per_minute,
                lease_seconds=EXCLUDED.lease_seconds,failure_threshold=EXCLUDED.failure_threshold,
                cooldown_seconds=EXCLUDED.cooldown_seconds,enabled=EXCLUDED.enabled,
                updated_at=clock_timestamp(),updated_by=EXCLUDED.updated_by RETURNING *''',
                (ref, version, value['maxConcurrent'], value['callsPerMinute'], value['leaseSeconds'],
                 value['failureThreshold'], value['cooldownSeconds'], value['enabled'], actor_id))
            result = _policy(cursor.fetchone(), ref)
            cursor.execute('''INSERT INTO connector_policy_revision(connector_ref,policy_version,
                policy_json,actor_id,reason) VALUES(%s,%s,%s,%s,%s)''',
                (ref, version, Json(result), actor_id, reason))
            return result

    def manifest(self, run_id, profile):
        with self.factory() as con, con.cursor() as cursor:
            if not installed(cursor):
                return None
            cursor.execute('SELECT manifest_json,manifest_hash FROM agent_run_connector_snapshot WHERE run_id=%s', (run_id,))
            row = cursor.fetchone()
        if not row:
            # Old Runs derive only from their own immutable Profile, never
            # current provider/tool registry transport configuration.
            from .contracts import connector_manifest
            return connector_manifest(profile)
        manifest = deepcopy(row['manifest_json'])
        supplied = manifest.pop('manifestHash', None)
        if _hash(manifest) != row['manifest_hash'] or supplied != row['manifest_hash']:
            raise ConnectorAdmissionError('connector-manifest-integrity')
        manifest['manifestHash'] = supplied
        from .contracts import connector_manifest
        expected = connector_manifest(profile)
        def configuration(value):
            result = deepcopy(value)
            result.pop('manifestHash', None)
            for item in result.get('connections', []):
                item.pop('policy', None)
            return result
        if _hash(configuration(manifest)) != _hash(configuration(expected)):
            raise ConnectorAdmissionError('connector-profile-binding-mismatch')
        return manifest

    @staticmethod
    def _resolve(manifest, profile, kind, key, version=None, model=None):
        if kind == 'model':
            ref = 'provider:' + key
            operations = [op for op in manifest.get('operations', []) if op.get('connectorRef') == ref
                and (op.get('modelName') == model or (op.get('config') or {}).get('modelName') == model
                     or op.get('ref') == f'model:{key}@{model}')]
        else:
            operations = [op for op in manifest.get('operations', [])
                if op.get('toolKey') == key and op.get('toolVersion') == version]
            ref = operations[0].get('connectorRef') if operations else None
        if len(operations) != 1 or not ref:
            raise ConnectorAdmissionError('connector-operation-not-frozen')
        connections = [item for item in manifest.get('connections', []) if item.get('ref') == ref]
        if len(connections) != 1:
            raise ConnectorAdmissionError('connector-binding-not-frozen')
        return connections[0], operations[0]

    def acquire(self, run_id, connection, operation_key, attempt_key, *, step_run_id=None, call_kind=None,
                timeout_seconds=60):
        ref = _identifier(connection['ref'])
        connection_version = str(connection.get('version', 'unknown'))
        binding_hash = _hash({key: value for key, value in connection.items() if key != 'policy'})
        frozen = _normalise_policy(connection.get('policy'))
        with self.factory() as con, con.cursor() as cursor:
            self._lock(cursor, ref)
            cursor.execute('SELECT * FROM connector_policy WHERE connector_ref=%s', (ref,))
            live = _policy(cursor.fetchone(), ref)
            effective = {k: min(frozen[k], live[k]) for k in POLICY_BOUNDS}
            effective['cooldownSeconds'] = max(frozen['cooldownSeconds'], live['cooldownSeconds'])
            if not frozen['enabled'] or not live['enabled']:
                raise ConnectorAdmissionError('connector-disabled')
            # A bounded lease must exceed the actual timeout so a live call
            # cannot lose its concurrency slot while it is still executing.
            lease_seconds = max(effective['leaseSeconds'], min(86400, int(timeout_seconds) + 5))
            cursor.execute('SELECT clock_timestamp() AS now')
            current = cursor.fetchone()['now']
            cursor.execute('''SELECT count(*) AS active FROM connector_lease WHERE connector_ref=%s
                AND released_at IS NULL AND expires_at>%s''', (ref, current))
            active = cursor.fetchone()['active']
            cursor.execute('''SELECT status,error_class,observed_at FROM connector_health_observation
                WHERE connector_ref=%s AND connector_version=%s AND binding_hash=%s
                AND status IN ('success','failed','unknown')
                ORDER BY observed_at DESC,observation_id DESC LIMIT %s''',
                (ref, connection_version, binding_hash, effective['failureThreshold']))
            observations = list(cursor.fetchall())
            unhealthy = [item for item in observations if item['status'] != 'success'
                         and item.get('error_class') in {'authentication', 'rate-limit', 'transport', 'service'}]
            open_circuit = len(unhealthy) >= effective['failureThreshold']
            if open_circuit:
                retry_after = effective['cooldownSeconds'] - (current - unhealthy[0]['observed_at']).total_seconds()
                if retry_after > 0 or active:
                    raise ConnectorAdmissionError('connector-circuit-open', max(1, int(retry_after)))
            if active >= effective['maxConcurrent']:
                raise ConnectorAdmissionError('connector-concurrency-limited', 1)
            cursor.execute('''SELECT count(*) AS calls,min(created_at) AS earliest FROM connector_lease
                WHERE connector_ref=%s AND created_at>%s-interval '60 seconds'
                AND (release_reason IS NULL OR release_reason NOT IN ('not-dispatched','cache-reused')) ''', (ref, current))
            window = cursor.fetchone()
            if window['calls'] >= effective['callsPerMinute']:
                wait = 60 - (current - window['earliest']).total_seconds()
                raise ConnectorAdmissionError('connector-rate-limited', max(1, int(wait)))
            lease_id = 'connector-lease-' + secrets.token_hex(12)
            cursor.execute('''INSERT INTO connector_lease(lease_id,connector_ref,connector_version,run_id,
                operation_key,attempt_key,expires_at) VALUES(%s,%s,%s,%s,%s,%s,
                clock_timestamp()+(%s * interval '1 second'))''',
                (lease_id, ref, connection_version, run_id,
                 operation_key, attempt_key, lease_seconds))
        return {'leaseId': lease_id, 'connectorRef': ref,
            'connectorVersion': connection_version, 'bindingHash': binding_hash, 'runId': run_id,
            'stepRunId': step_run_id, 'operationKey': operation_key, 'attemptKey': attempt_key,
            'callKind': call_kind}

    def release(self, lease, *, status='unknown', error_code=None, call_id=None, latency_ms=0, dispatched=True,
                cached=False):
        status = {'succeeded': 'success', 'timeout': 'unknown', 'cancelled': 'unknown'}.get(status, status)
        if status not in {'success', 'failed', 'unknown', 'rejected'}:
            status = 'unknown'
        if not dispatched:
            status, error_code = 'rejected', 'connector-not-dispatched'
        if not isinstance(error_code, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,100}', error_code):
            error_code = None if status == 'success' else 'connector-operation-failed'
        error_class = None if status == 'success' else _failure(error_code)[0]
        with self.factory() as con, con.cursor() as cursor:
            self._lock(cursor, lease['connectorRef'])
            cursor.execute('''UPDATE connector_lease SET released_at=clock_timestamp(),release_reason=%s
                WHERE lease_id=%s AND released_at IS NULL RETURNING lease_id''',
                ('cache-reused' if cached else status if dispatched else 'not-dispatched', lease['leaseId']))
            if not cursor.fetchone():
                return
            if not cached:
                cursor.execute('''INSERT INTO connector_health_observation(observation_id,connector_ref,
                    connector_version,binding_hash,run_id,step_run_id,operation_key,attempt_key,status,error_class,error_code,latency_ms)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    ('connector-observation-' + secrets.token_hex(12), lease['connectorRef'], lease['connectorVersion'],
                     lease['bindingHash'], lease['runId'], lease.get('stepRunId'), lease['operationKey'], lease['attemptKey'], status,
                     error_class, error_code, max(0, int(latency_ms))))
            if call_id:
                cursor.execute('''INSERT INTO connector_call_link(link_id,connector_ref,connector_version,
                    run_id,step_run_id,operation_key,attempt_key,call_kind,call_id,cache_reused)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    ('connector-link-' + secrets.token_hex(12), lease['connectorRef'], lease['connectorVersion'],
                     lease['runId'], lease.get('stepRunId'), lease['operationKey'], lease['attemptKey'],
                     lease['callKind'], call_id, bool(cached)))

    def record_cache_link(self, run_id, connection, operation_key, attempt_key, call_kind, call_id,
                          step_run_id=None):
        if not call_id:
            return
        with self.factory() as con, con.cursor() as cursor:
            cursor.execute('''INSERT INTO connector_call_link(link_id,connector_ref,connector_version,
                run_id,step_run_id,operation_key,attempt_key,call_kind,call_id,cache_reused)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,true) ON CONFLICT DO NOTHING''',
                ('connector-link-' + secrets.token_hex(12), connection['ref'], str(connection.get('version', 'unknown')),
                 run_id, step_run_id, operation_key, attempt_key, call_kind, call_id))

    @asynccontextmanager
    async def attempt(self, ctx, *, kind, key, version=None, model=None, operation_key,
                attempt_key, step_run_id=None, timeout_seconds=60, cache_only=False):
        manifest = self.manifest(ctx.run_id, ctx.profile)
        if manifest is None:
            # Database versions before the connector migration keep their
            # documented execution path. Current-schema production uses leases.
            yield AttemptReceipt()
            return
        connection, operation = self._resolve(manifest, ctx.profile, kind, key, version, model)
        if cache_only:
            if kind != 'tool' or key != 'web.scrape' or connection.get('driver') != 'firecrawl':
                raise ConnectorAdmissionError('connector-cache-bypass-not-supported')
            receipt = AttemptReceipt()
            receipt.cached = True
            try:
                yield receipt
            finally:
                if receipt.result and receipt.result.get('call_id'):
                    try:
                        self.record_cache_link(ctx.run_id, connection, operation_key, attempt_key,
                            kind, receipt.result['call_id'], step_run_id)
                    except Exception:
                        _LOG.warning('connector-observation-persistence-failed')
            return
        deadline = time.monotonic() + max(1, min(60, timeout_seconds))
        while True:
            try:
                lease = self.acquire(ctx.run_id, connection, operation_key, attempt_key,
                    step_run_id=step_run_id, call_kind=kind, timeout_seconds=timeout_seconds)
                break
            except ConnectorAdmissionError as exc:
                if exc.code not in {'connector-concurrency-limited', 'connector-rate-limited', 'connector-circuit-open'}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (exc.retry_after or 0) > remaining:
                    raise
                await asyncio.sleep(min(remaining, 0.25 if exc.code == 'connector-concurrency-limited'
                                        else max(0.25, min(1, exc.retry_after or 1))))
        receipt = AttemptReceipt(self, lease)
        error = None
        try:
            yield receipt
        except BaseException as exc:
            error = exc
            raise
        finally:
            result = receipt.result or {'status': 'unknown' if error else 'success',
                'error_code': getattr(error, 'code', None) if error else None}
            if error and error.__class__.__name__ == 'CancelledError':
                result = {**result, 'status': 'unknown', 'error_code': 'connector-call-cancelled'}
            try:
                self.release(lease, status=result['status'], error_code=result.get('error_code'),
                    call_id=result.get('call_id'), latency_ms=result.get('latency_ms') or
                    max(0, int((time.monotonic() - receipt.started) * 1000)), dispatched=receipt.dispatched,
                    cached=receipt.cached)
            except Exception:
                # The original durable model/tool audit and reservations own
                # execution accounting. An observation failure must not replay
                # an already dispatched call; the bounded lease expires.
                _LOG.warning('connector-observation-persistence-failed')
