"""Human-requested successor runs. Historical outputs and approvals stay intact."""
from copy import deepcopy
import hashlib
import json
import re

from psycopg2.extras import Json

from .engine import TERMINAL
from .worker import control_context
from .evidence_repository import artifact_json_hash


class FollowupService:
    def __init__(self, engine, queue):
        self.engine, self.repo, self.queue = engine, engine.repo, queue

    def links(self, run_id):
        with self.repo.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_run_followup WHERE previous_run_id=%s OR run_id=%s "
                          "ORDER BY created_at,run_id", (run_id, run_id))
                return [dict(row) for row in c.fetchall()]

    def create(self, previous_id, actor, *, idempotency_key, reason, changes):
        if not actor.permits('operator'):
            raise PermissionError('operator role required')
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', idempotency_key):
            raise ValueError('a stable follow-up operation key is required')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 4000:
            raise ValueError('follow-up reason is required')
        if not isinstance(changes, dict):
            raise ValueError('invalid follow-up input fields')
        material = changes.get('supplementalMaterial', '')
        if not isinstance(material, str) or len(material) > 30000:
            raise ValueError('supplemental material must be bounded text')
        digest = hashlib.sha256(json.dumps({'changes': changes, 'reason': reason}, sort_keys=True,
                                ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        with control_context(previous_id), self.repo.connection_factory() as con:
            with con.cursor() as c:
                # Recheck the database identity rather than trusting a constructed Principal.
                c.execute("SELECT kind,roles_json,enabled FROM agent_admin_identity WHERE principal_id=%s", (actor.principal_id,))
                identity = c.fetchone()
                if not identity or not identity['enabled'] or identity['kind'] != 'human' or not \
                        {'operator', 'admin'}.intersection(identity['roles_json']):
                    raise PermissionError('active human operator identity required')
                c.execute('SELECT * FROM agent_run WHERE run_id=%s FOR UPDATE', (previous_id,))
                previous = c.fetchone()
                if not previous or previous['status'] not in TERMINAL:
                    raise ValueError('a follow-up requires a terminal previous run')
                future = previous['agent_key'] in {'future-battlefield-analyst', 'future-battlefield-codex'}
                allowed = ({'supplementalMaterial', 'followupTarget'} if future else
                           {'question', 'requirements', 'supplementalMaterial', 'budgetLimits', 'collaborate', 'includeDesign'})
                if set(changes) - allowed:
                    raise ValueError('invalid follow-up input fields')
                if future:
                    self._validate_future_target(previous['input_json'], changes.get('followupTarget'))
                c.execute('SELECT * FROM agent_run_followup WHERE previous_run_id=%s AND idempotency_key=%s',
                          (previous_id, idempotency_key))
                saved = c.fetchone()
                if saved:
                    if saved['request_hash'] != digest or saved['principal_id'] != actor.principal_id:
                        raise ValueError('operation key already identifies a different follow-up')
                    return {'runId': saved['run_id'], 'previousRunId': previous_id, 'created': False}
                if not future and previous['input_json'].get('executionMode') != 'research':
                    raise ValueError('follow-ups require a research run')
                if future and previous['input_json'].get('executionMode') != 'profile-run':
                    raise ValueError('future battlefield follow-ups require a profile Run')
                data = deepcopy(previous['input_json']) if future else {key: deepcopy(previous['input_json'][key]) for key in
                        ('entityId', 'uavId', 'question', 'requirements', 'budgetLimits', 'collaborate', 'includeDesign')
                        if key in previous['input_json']}
                data.update(deepcopy(changes))
                data.update(executionMode=previous['input_json'].get('executionMode', 'research'), previousRunId=previous_id,
                            followupMode='targeted-evidence' if future else 'research-followup')
                if future:
                    # Pin the effective original ceiling even when its input used
                    # an empty override and today's Profile defaults changed.
                    c.execute('SELECT b.limits_json FROM agent_run_budget_member m JOIN agent_run_budget b '
                              'USING(root_run_id) WHERE m.run_id=%s', (previous_id,))
                    budget = c.fetchone()
                    if not budget:
                        raise ValueError('previous Run budget is not initialized')
                    data['budgetLimits'] = deepcopy(budget['limits_json'])
                    if data.get('commonSettings'):
                        data['commonSettings']['budgetLimits'] = deepcopy(data['budgetLimits'])
                session_id = data.get('dshSessionId')
                turn_id = 'dsh-followup-' + hashlib.sha256((previous_id + ':' + idempotency_key).encode()).hexdigest()[:24] if session_id else None
                if session_id:
                    c.execute('SELECT session_id FROM dsh_session WHERE session_id=%s AND principal_id=%s',
                              (session_id, actor.principal_id))
                    if not c.fetchone():
                        raise PermissionError('follow-up DSH session ownership mismatch')
                    data['dshTurnId'] = turn_id
                c.execute("SELECT artifact_id,artifact_type,content_json,content_hash FROM agent_run_artifact "
                          "WHERE run_id=%s AND artifact_type='agent-output' ORDER BY artifact_id", (previous_id,))
                refs = []
                for artifact in c.fetchall():
                    if artifact_json_hash(artifact['content_json']) != artifact['content_hash']:
                        raise ValueError('previous output artifact hash mismatch')
                    refs.append({'artifactId': artifact['artifact_id'], 'contentHash': artifact['content_hash']})
                # Use current configured permissions. No upstream candidates or human
                # review flags are copied; collection and review start independently.
                profile = self.engine.loader.load(previous['agent_key'] if future else 'uav-capability-analyst')
                # The successor freezes today's Profile. Historical UI settings
                # must name that version while the previous Run stays immutable.
                if isinstance(data.get('commonSettings'),dict):
                    data['commonSettings']['agentKey']=profile.agent_key
                    data['commonSettings']['agentVersion']=profile.version
                from .task_contracts import validate_task_envelope
                validate_task_envelope(profile.agent_key, profile, data)
                from .task_intent import admission_actor
                from .run_origin import origin_context
                with admission_actor(actor), origin_context(actor, source_kind='followup', source_ref=previous_id):
                    run_id = self.repo.create_run(profile, data, _cursor=c)
                c.execute('INSERT INTO agent_run_followup(previous_run_id,run_id,idempotency_key,principal_id,'
                          'reason,previous_artifacts_json,request_hash) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                          (previous_id, run_id, idempotency_key, actor.principal_id, reason.strip(), Json(refs), digest))
                if session_id:
                    c.execute("INSERT INTO dsh_turn(turn_id,session_id,run_id,idempotency_key,question,input_json,status) "
                              "VALUES(%s,%s,%s,%s,%s,%s,'queued')",
                              (turn_id, session_id, run_id, turn_id, data.get('question', reason.strip()), Json(data)))
                # Queue admission and link creation commit with the new snapshot/budget.
                self.repo.event(c, run_id, 'research.followup-created', None,
                                {'previousRunId': previous_id, 'principalId': actor.principal_id,
                                 'reviewStatus': 'pending', 'budgetRoot': run_id})
                c.execute('INSERT INTO agent_run_queue(run_id,pool_key) VALUES(%s,%s)', (run_id, self.queue.pool_key))
                return {'runId': run_id, 'previousRunId': previous_id, 'turnId': turn_id, 'status': 'queued', 'created': True}

    @staticmethod
    def _validate_future_target(input_data, target):
        """Validate a successor's retrieval focus without allowing scope edits."""
        if not isinstance(target, dict):
            raise ValueError('future battlefield follow-upTarget is required')
        allowed = {'perspectiveKey', 'gapIds', 'queries'}
        if set(target) - allowed:
            raise ValueError('future battlefield follow-upTarget contains unknown fields')
        perspectives = {str(item.get('key')) for item in input_data.get('perspectives', []) if isinstance(item, dict)}
        key = target.get('perspectiveKey')
        if not isinstance(key, str) or key not in perspectives:
            raise ValueError('follow-up target must name one frozen perspective')
        gaps = target.get('gapIds', [])
        if not isinstance(gaps, list) or len(gaps) > 8 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in gaps):
            raise ValueError('follow-up gapIds must be a bounded list')
        queries = target.get('queries', [])
        if not isinstance(queries, list) or len(queries) > 4 or any(not isinstance(v, str) or not v.strip() or len(v) > 500 for v in queries):
            raise ValueError('follow-up queries must be a bounded list')
