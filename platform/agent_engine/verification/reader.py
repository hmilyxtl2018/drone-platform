"""One bounded read-only transaction, fixed SELECTs, no full provider configs."""
from contextlib import contextmanager
import json
import time

from .contracts import DSH_AGENT_KEYS, EvidenceBundle, VerificationError

TABLES = ('agent_definition', 'agent_dsh_exposure_policy', 'agent_goal_contract_revision',
          'agent_model_policy', 'agent_reasoning_policy', 'agent_harness_contract')


class ReadContext:
    def __init__(self, cursor):
        self.cursor = cursor
        self.deadline = time.monotonic() + 8
        self.queries = 0
        self.actor = None
        self.channel = None
        self._run_cache = {}
        self._contract_cache = {}
        self._query_cache = {}
        self._payload_bytes = 0
        self._payload_seen = set()

    def account_payload(self, identity, value):
        if identity in self._payload_seen or value is None: return
        self._payload_seen.add(identity)
        self._payload_bytes += len(json.dumps(value, ensure_ascii=False, default=str).encode('utf-8'))
        if self._payload_bytes > 24_000_000:
            raise VerificationError('verification-read-limit')

    def query(self, sql, args=(), *, many=False):
        if time.monotonic() > self.deadline:
            raise VerificationError('verification-read-limit')
        cache_key = (sql, args, many)
        try:
            hash(cache_key)
        except TypeError:
            cache_key = None
        if cache_key is not None and cache_key in self._query_cache:
            return self._query_cache[cache_key]
        if self.queries >= 400:
            raise VerificationError('verification-read-limit')
        self.queries += 1
        self.cursor.execute(sql, args)
        result = self.cursor.fetchall() if many else self.cursor.fetchone()
        if cache_key is not None: self._query_cache[cache_key] = result
        return result

    def available(self):
        rows = self.query('''SELECT table_name FROM information_schema.tables
            WHERE table_schema=current_schema() AND table_name=ANY(%s)''', (list(TABLES),), many=True)
        return {r['table_name'] for r in rows}

    def registration(self, key, tables):
        if key not in DSH_AGENT_KEYS:
            raise VerificationError('verification-subject-not-found', 404)
        # Exposure is an access prerequisite, never inferred from a fallback.
        if not {'agent_definition', 'agent_dsh_exposure_policy'}.issubset(tables):
            raise VerificationError('verification-schema-unavailable')
        definition = self.query('''SELECT agent_key,display_name,current_version,description,status
            FROM agent_definition WHERE agent_key=%s''', (key,))
        exposure = self.query('''SELECT enabled,allowed_modes_json FROM agent_dsh_exposure_policy
            WHERE agent_key=%s''', (key,))
        if (not definition or definition['status'] in {'disabled', 'inactive', 'retired'}
                or not exposure or not exposure['enabled']):
            raise VerificationError('verification-subject-not-found', 404)
        facts = {'definition': dict(definition), 'modes': exposure['allowed_modes_json']}
        missing = [t for t in TABLES if t not in tables]
        if 'agent_goal_contract_revision' in tables:
            row = self.query('''SELECT contract_id,revision,document_json,content_hash
                FROM agent_goal_contract_revision WHERE agent_key=%s ORDER BY revision DESC LIMIT 1''', (key,))
            facts['goal'] = dict(row) if row else None
        if 'agent_model_policy' in tables:
            row = self.query('''SELECT config_json::jsonb->'budgetLimits' AS budget
                FROM agent_model_policy WHERE agent_key=%s''', (key,))
            facts['model'] = dict(row) if row else None
        if 'agent_reasoning_policy' in tables:
            row = self.query('''SELECT profile_version,version,
                policy_json->>'agentKey' AS document_agent_key,
                policy_json->>'profileVersion' AS document_profile_version,
                policy_json->'version' AS document_version,
                policy_json->'budgetLimits' AS budget,
                policy_json->'stages' AS stages
                FROM agent_reasoning_policy WHERE agent_key=%s AND profile_version=%s
                ORDER BY version DESC LIMIT 1''', (key, definition['current_version']))
            facts['reasoning'] = dict(row) if row else None
        if 'agent_harness_contract' in tables:
            row = self.query('''SELECT profile_version FROM agent_harness_contract
                WHERE agent_key=%s AND profile_version=%s AND status IN ('testing','published')
                ORDER BY CASE WHEN status='published' THEN 0 ELSE 1 END,created_at DESC LIMIT 1''',
                (key, definition['current_version']))
            facts['harness'] = dict(row) if row else None
        return EvidenceBundle.make(key, facts, missing)

    def run(self, run_id):
        if run_id in self._run_cache:
            return self._run_cache[run_id]
        row = self.query('''SELECT r.run_id,r.agent_key,r.agent_version,r.status,
            r.created_at,r.completed_at,b.root_run_id,b.parent_run_id
            FROM agent_run r LEFT JOIN agent_run_budget_member b USING(run_id) WHERE r.run_id=%s''', (run_id,))
        if not row:
            raise VerificationError('verification-subject-not-found', 404)
        root_id = row['root_run_id'] or run_id
        root = self.query('SELECT agent_key FROM agent_run WHERE run_id=%s', (root_id,))
        if not root or root['agent_key'] not in DSH_AGENT_KEYS:
            raise VerificationError('verification-subject-not-found', 404)
        # Same owner/root/candidate access rule as the DSH Run reads. An admin
        # may also inspect the same fixed application family from the workbench.
        if not (self.channel == 'admin' and self.actor.permits('admin')):
            access = self.query('''SELECT 1 AS allowed FROM dsh_turn t
                JOIN dsh_session s USING(session_id)
                WHERE s.principal_id=%s AND (t.run_id=%s OR t.run_id=%s OR t.run_id IN (
                    SELECT p.source_run_id FROM agent_improvement_candidate_run cr
                    JOIN agent_improvement_proposal p USING(proposal_id)
                    WHERE cr.run_id=%s)) LIMIT 1''',
                (self.actor.principal_id, run_id, root_id, root_id))
            if not access:
                raise VerificationError('verification-subject-not-found', 404)
        self._run_cache[run_id] = dict(row)
        return self._run_cache[run_id]

    def artifact(self, run_id, artifact_id):
        self.run(run_id)
        row = self.query('''SELECT artifact_id,run_id,artifact_type,content_hash,
            CASE WHEN octet_length(content_json::text)<=4000000 THEN content_json END AS content_json
            FROM agent_run_artifact WHERE run_id=%s AND artifact_id=%s''', (run_id, artifact_id))
        if row: self.account_payload(('artifact', artifact_id), row['content_json'])
        return dict(row) if row else None

    def profile(self, run_id):
        self.run(run_id)
        row = self.query('''SELECT profile_hash,
            CASE WHEN octet_length(profile_json::text)<=4000000 THEN profile_json END AS profile_json
            FROM agent_run_profile_snapshot WHERE run_id=%s''', (run_id,))
        if row: self.account_payload(('profile', run_id), row['profile_json'])
        return dict(row) if row else None


@contextmanager
def read_snapshot(factory):
    con = factory()
    try:
        con.set_session(readonly=True, isolation_level='REPEATABLE READ', autocommit=False)
        with con.cursor() as cursor:
            # Transaction-local limits, not business data mutations.
            cursor.execute("SET LOCAL statement_timeout = '2500ms'")
            cursor.execute("SET LOCAL lock_timeout = '500ms'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '10000ms'")
            yield ReadContext(cursor)
    finally:
        try:
            con.rollback()
        finally:
            con.close()
