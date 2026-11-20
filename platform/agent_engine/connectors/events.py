"""Deduplicated changes of persisted source content, never a truth assertion."""
from copy import deepcopy
import hashlib
import json
import re
import logging

from psycopg2.extras import Json
from ..evidence_repository import artifact_source
from ..repository import new_id


class SourceEventService:
    def __init__(self, factory):
        self.factory = factory

    def record_latest(self, artifact_id):
        with self.factory() as con, con.cursor() as c:
            c.execute("SELECT to_regclass(current_schema() || '.connector_source_event') AS name")
            if not c.fetchone()['name']:
                return None
            c.execute('SELECT artifact_type,content_json,created_at FROM agent_run_artifact WHERE artifact_id=%s', (artifact_id,))
            current = c.fetchone()
            if not current or current['artifact_type'] not in {'web-source-snapshot', 'document-source-snapshot'}:
                return None
            c.execute('''SELECT artifact_id,content_json FROM agent_run_artifact WHERE artifact_type=%s
                AND content_json->>'sourceUri'=%s AND created_at<%s
                ORDER BY created_at DESC,artifact_id DESC LIMIT 1''',
                (current['artifact_type'], current['content_json'].get('sourceUri'), current['created_at']))
            previous = c.fetchone()
            if not previous or previous['content_json'].get('contentHash') == current['content_json'].get('contentHash'):
                return None
        return self.record_change(previous['artifact_id'], artifact_id, 'system:stored-source-change')

    @staticmethod
    def _profile_web_connector_ref(cursor, run_id):
        """Read a historical provider identity without consulting the registry."""
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_run_profile_snapshot') AS name")
        if not (cursor.fetchone() or {}).get('name'):
            return None
        cursor.execute('SELECT profile_json,profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s', (run_id,))
        row = cursor.fetchone()
        if not row:
            return None
        profile = row.get('profile_json')
        if isinstance(profile, str):
            try:
                profile = json.loads(profile)
            except (TypeError, ValueError):
                raise ValueError('frozen source Profile is invalid') from None
        if not isinstance(profile, dict):
            raise ValueError('frozen source Profile is invalid')
        digest = hashlib.sha256(json.dumps(profile, sort_keys=True, default=str).encode()).hexdigest()
        if digest != row.get('profile_hash'):
            raise ValueError('frozen source Profile integrity mismatch')
        policy = profile.get('sourcePolicy') or {}
        if not isinstance(policy, dict):
            raise ValueError('frozen source policy is invalid')
        provider = policy.get('firecrawlProvider')
        if provider is None:
            return None
        key = provider.get('providerKey') if isinstance(provider, dict) else None
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', key):
            raise ValueError('frozen web provider identity is invalid')
        return 'provider:' + key

    @classmethod
    def _web_connector_ref(cls, cursor, run_id):
        """Resolve the provider from the immutable Run manifest and Profile.

        A web artifact only carries source content and its public URI. The
        connector identity is therefore taken from the Run's frozen manifest,
        never from a caller or the mutable provider registry. Runs created
        before connector snapshots existed use their frozen sourcePolicy.
        Legacy artifacts without either frozen identity retain the explicit
        Firecrawl compatibility identity; a current snapshot without one web
        connector is rejected rather than silently attributed to the wrong
        service.
        """
        profile_ref = cls._profile_web_connector_ref(cursor, run_id)
        cursor.execute("SELECT to_regclass(current_schema() || '.agent_run_connector_snapshot') AS name")
        if not (cursor.fetchone() or {}).get('name'):
            return profile_ref or 'provider:firecrawl'
        cursor.execute('SELECT manifest_json,manifest_hash FROM agent_run_connector_snapshot WHERE run_id=%s', (run_id,))
        row = cursor.fetchone()
        if not row:
            # Old persisted artifacts have no frozen connector identity.
            return profile_ref or 'provider:firecrawl'
        manifest = row.get('manifest_json') or {}
        if isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except (TypeError, ValueError):
                raise ValueError('frozen connector manifest is invalid') from None
        if not isinstance(manifest, dict):
            raise ValueError('frozen connector manifest is invalid')
        from .contracts import manifest_hash
        try:
            digest = manifest_hash(manifest)
        except (TypeError, ValueError):
            raise ValueError('frozen connector manifest is invalid') from None
        if digest != row.get('manifest_hash') or manifest.get('manifestHash') != digest:
            raise ValueError('frozen connector manifest integrity mismatch')
        connections, operations = manifest.get('connections'), manifest.get('operations')
        if not isinstance(connections, list) or not isinstance(operations, list):
            raise ValueError('frozen connector manifest is invalid')
        web = [item for item in connections if isinstance(item, dict) and item.get('driver') == 'firecrawl']
        if len(web) != 1:
            raise ValueError('web source connector is ambiguous in the frozen Run manifest')
        ref = web[0].get('ref')
        if not isinstance(ref, str) or not re.fullmatch(r'provider:[A-Za-z0-9_.:-]{1,200}', ref):
            raise ValueError('frozen web provider identity is invalid')
        web_operations = [item for item in operations if isinstance(item, dict)
                          and str(item.get('toolKey', '')).startswith('web.')]
        if not web_operations or any(item.get('connectorRef') != ref or item.get('driver') != 'firecrawl'
                                     for item in web_operations):
            raise ValueError('web source operation disagrees with frozen connector identity')
        if profile_ref and profile_ref != ref:
            raise ValueError('frozen web provider Profile and connector manifest disagree')
        return ref

    def record_change(self, before_id, after_id, actor_id):
        if not all(isinstance(value, str) and 1 <= len(value) <= 200 for value in (before_id, after_id, actor_id)):
            raise ValueError('invalid source event identifiers')
        with self.factory() as con, con.cursor() as c:
            sources, created = [], []
            for identity in (before_id, after_id):
                c.execute('SELECT * FROM agent_run_artifact WHERE artifact_id=%s', (identity,))
                row = c.fetchone()
                if not row:
                    raise ValueError('source artifact not found')
                sources.append(artifact_source(dict(row), row['run_id']))
                created.append(row['created_at'])
            before, after = sources
            if created[1] <= created[0]:
                raise ValueError('source change requires a strictly later stored artifact')
            if before['sourceUri'] != after['sourceUri'] or before['contentHash'] == after['contentHash']:
                raise ValueError('change requires distinct content versions of the same source')
            # Choose the logical connector from the actual, immutable source
            # artifact kind and (for web) the after Run's frozen manifest. No
            # external endpoint or caller-defined ref enters.
            c.execute('SELECT artifact_type,run_id FROM agent_run_artifact WHERE artifact_id=%s', (after_id,))
            artifact_row = c.fetchone()
            kind, after_run_id = artifact_row['artifact_type'], artifact_row['run_id']
            ref = (self._web_connector_ref(c, after_run_id) if kind == 'web-source-snapshot'
                   else 'source:' + after['metadata']['sourceKey'])
            key = hashlib.sha256(json.dumps([ref, before['sourceUri'], before['contentHash'], after['contentHash']],
                                           separators=(',', ':')).encode()).hexdigest()
            c.execute('''INSERT INTO connector_source_event(event_id,connector_ref,source_uri,
                before_artifact_id,after_artifact_id,before_hash,after_hash,dedup_key,observed_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(dedup_key) DO NOTHING''',
                (new_id('source-event'), ref, before['sourceUri'], before_id, after_id,
                 before['contentHash'], after['contentHash'], key, actor_id))
            c.execute('''SELECT event_id,connector_ref,source_uri,before_hash,after_hash,created_at
                         FROM connector_source_event WHERE dedup_key=%s''', (key,))
            return {**dict(c.fetchone()), 'semantics': 'stored-content-changed', 'truthVerified': False}


def observe_source_artifact(repository, artifact_id):
    factory = getattr(repository, 'connection_factory', None)
    if not callable(factory):
        return
    try:
        SourceEventService(factory).record_latest(artifact_id)
    except Exception:
        # Source persistence is already durable. An observation failure must
        # never make the gateway repeat a successful paid acquisition.
        logging.getLogger(__name__).warning('connector-source-change-observation-failed')


def propose_saved_changes(cursor, controller, mission):
    """Wake an existing active grant only for a source it already researched.

    The normal controller still checks goal, contract, expiry and remaining
    budget before dispatch. Event content never changes the human goal.
    """
    cursor.execute("SELECT to_regclass(current_schema() || '.connector_source_event') AS name")
    if not cursor.fetchone()['name']:
        return False
    cursor.execute('''SELECT r.run_id,r.output_json FROM agent_autopilot_run_binding b JOIN agent_run r USING(run_id)
        WHERE b.mission_id=%s AND b.grant_id=%s AND r.status IN ('completed','completed-with-gaps')
        ORDER BY r.created_at DESC LIMIT 1''', (mission['mission_id'], mission['grant_id']))
    previous = cursor.fetchone()
    if not previous:
        return False
    known = {e.get('sourceUri'): e.get('contentHash') for e in (previous['output_json'] or {}).get('evidence', [])
             if isinstance(e, dict) and e.get('sourceUri') and e.get('contentHash')}
    if not known:
        return False
    cursor.execute('''SELECT * FROM connector_source_event WHERE source_uri=ANY(%s)
        AND created_at>=%s ORDER BY created_at DESC LIMIT 100''', (list(known), mission['granted_at']))
    for event in cursor.fetchall():
        if known.get(event['source_uri']) != event['before_hash']:
            continue
        request = deepcopy(mission['document_json']['request'])
        request['previousRunId'] = previous['run_id']
        reference = {'eventId': event['event_id'], 'sourceUri': event['source_uri'],
                     'beforeHash': event['before_hash'], 'afterHash': event['after_hash'],
                     'semantics': 'stored-content-changed', 'realityChangeVerified': False}
        if controller.propose(cursor, mission, 'source-change', reference, request,
                              dedup='connector-event:' + event['event_id']):
            return True
    return False
