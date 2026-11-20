"""Append-only human opinions bound to an exact saved report and object.

This service never publishes, verifies a fact, calls a model, or creates a Run.
Credential-free loopback is authorized by the existing local operator boundary;
explicit service/model credentials never acquire a human identity here.
"""
from copy import deepcopy
from datetime import date, datetime
import json
import re

from psycopg2.extras import Json
from psycopg2 import IntegrityError, errors

from .context_contract import content_hash
from .repository import as_json, new_id
from .ontology_explorer import evidence_index, explorer_graph, source_excerpt, PREDICATES

ACTIONS = {'comment': '评论', 'objection': '保留异议', 'request-evidence': '要求补证',
           'scope-confirmation': '确认表述范围', 'correction': '追加更正'}
KINDS = {'report', 'node', 'edge', 'evidence'}
DELIVERED = {'completed', 'completed-with-gaps'}
FIELDS = {'reportHash', 'sourceGraphHash', 'objectKind', 'objectId', 'action', 'reason',
          'evidenceIds', 'expectedRevision', 'idempotencyKey', 'supersedesEventId'}


class ReviewValidationError(ValueError): pass
class ReviewConflictError(ValueError): pass
class ReviewUnavailableError(ValueError): pass


def installed(cursor):
    cursor.execute("SELECT to_regclass(current_schema() || '.agent_report_review_snapshot') AS snapshot, to_regclass(current_schema() || '.agent_report_review_event') AS event")
    row = cursor.fetchone()
    return bool(row and row['snapshot'] and row['event'])


def report_for(run):
    output = as_json(run.get('output_json')) or {}
    if not isinstance(output, dict): output = {}
    return {**deepcopy(output), 'runId': run['run_id'],
        'ontologyGraph': deepcopy(output.get('ontology') or (output.get('judgmentReport') or {}).get('ontology') or {})}


def identity_for(run, report):
    from .review_summary import build_review_summary
    return build_review_summary(run, {}, report)['identity']


def manifest_for(run, report, identity):
    graph = explorer_graph(report)
    evidence = evidence_index(report)
    return {'schemaVersion': 'report-review-manifest.v1', 'identity': deepcopy(identity),
        'objects': {'report': ['report'], 'node': sorted(n['id'] for n in graph.get('nodes', [])),
            'edge': sorted(e['id'] for e in graph.get('edges', [])), 'evidence': sorted(evidence)},
        'evidenceHashes': {key: content_hash(value) for key, value in sorted(evidence.items())},
        'sourceGraphHash': identity['sourceGraphHash'], 'reportHash': identity['reportHash']}


def normalized(body):
    if not isinstance(body, dict) or set(body) - FIELDS:
        raise ReviewValidationError('unsupported review fields; reviewer identity is assigned by the server')
    if not re.fullmatch(r'[a-f0-9]{64}', str(body.get('reportHash') or '')):
        raise ReviewValidationError('reportHash must identify the exact saved report')
    source_hash = body.get('sourceGraphHash')
    if source_hash is not None and not re.fullmatch(r'[a-f0-9]{64}', str(source_hash)):
        raise ReviewValidationError('sourceGraphHash is invalid')
    if body.get('objectKind') not in KINDS or not isinstance(body.get('objectId'), str) or not 1 <= len(body['objectId']) <= 4096:
        raise ReviewValidationError('invalid review object')
    if body.get('action') not in ACTIONS:
        raise ReviewValidationError('unsupported review action')
    if not isinstance(body.get('reason'), str) or not 1 <= len(body['reason'].strip()) <= 8000:
        raise ReviewValidationError('review reason must contain 1 to 8000 characters')
    ids = body.get('evidenceIds', [])
    if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(v, str) or not 1 <= len(v) <= 1024 for v in ids) or len(set(ids)) != len(ids):
        raise ReviewValidationError('evidenceIds must be distinct saved evidence identifiers')
    if type(body.get('expectedRevision')) is not int or body['expectedRevision'] < 0:
        raise ReviewValidationError('expectedRevision must identify the current object revision')
    if not isinstance(body.get('idempotencyKey'), str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,200}', body['idempotencyKey']):
        raise ReviewValidationError('invalid idempotency key')
    supersedes = body.get('supersedesEventId')
    if body['action'] == 'correction':
        if not isinstance(supersedes, str) or not 1 <= len(supersedes) <= 200:
            raise ReviewValidationError('correction requires supersedesEventId')
    elif supersedes is not None:
        raise ReviewValidationError('only a correction can supersede an earlier event')
    return {**body, 'sourceGraphHash': source_hash, 'reason': body['reason'].strip(),
        'evidenceIds': list(ids), 'supersedesEventId': supersedes}


def event_payload(row):
    return {'eventId': row['event_id'], 'objectKind': row['object_kind'], 'objectId': row['object_id'],
        'action': row['action'], 'reason': row['reason'], 'evidenceIds': row['evidence_ids_json'],
        'reviewerId': row['reviewer_id'], 'displayName': row['reviewer_display_name'],
        'createdAt': row['created_at'], 'revision': row['revision'],
        'previousEventId': row.get('previous_event_id'), 'supersedesEventId': row.get('supersedes_event_id'),
        'reportHash': row['report_hash'], 'sourceGraphHash': row['source_graph_hash'],
        'contentHash': row['content_hash']}


def _authorize(actor):
    if not actor or actor.kind != 'human' or not (actor.permits('reviewer') or actor.permits('admin')):
        raise PermissionError('human reviewer or administrator required')


class ReportReviewService:
    def __init__(self, factory, repository):
        self.factory, self.repo = factory, repository

    @staticmethod
    def _capability(run, actor, is_installed):
        if not is_installed: return False, '人工审阅记录功能尚未部署'
        if run.get('status') not in DELIVERED: return False, '仅已交付且固定的报告可留审阅意见'
        if not actor or actor.kind != 'human' or not (actor.permits('reviewer') or actor.permits('admin')):
            return False, '需要人工审阅者或管理员权限'
        return True, None

    @staticmethod
    def _snapshot(c, run_id, identity):
        c.execute('''SELECT * FROM agent_report_review_snapshot WHERE run_id=%s AND report_hash=%s
            AND source_graph_hash IS NOT DISTINCT FROM %s''', (run_id, identity['reportHash'], identity['sourceGraphHash']))
        return c.fetchone()

    @staticmethod
    def _events(c, snapshot_id):
        c.execute('''SELECT e.*,s.report_hash,s.source_graph_hash FROM agent_report_review_event e
            JOIN agent_report_review_snapshot s USING(snapshot_id) WHERE snapshot_id=%s ORDER BY e.created_at,e.event_id''', (snapshot_id,))
        return [event_payload(dict(row)) for row in c.fetchall()]

    def _result(self, run, report, identity, actor, is_installed, events, object_kind=None, object_id=None):
        write, reason = self._capability(run, actor, is_installed)
        if not identity.get('reportHash'): write, reason = False, '尚无已保存报告'
        selected = [event for event in events if event['objectKind'] == object_kind and event['objectId'] == object_id] if object_kind else events
        evidence = evidence_index(report)
        graph = explorer_graph(report)
        labels = {n['id']: n.get('label') for n in graph.get('nodes', [])}
        if object_kind == 'report': label = identity.get('taskTitle') or '整份报告'
        elif object_kind == 'evidence': label = (evidence.get(object_id) or {}).get('quote') or object_id
        elif object_kind == 'edge':
            edge = next((e for e in graph.get('edges', []) if e.get('id') == object_id), {})
            source = edge.get('source', edge.get('subject'))
            target = edge.get('target', edge.get('object'))
            predicate = edge.get('relation', edge.get('predicate'))
            label = ' · '.join(str(value) for value in (
                labels.get(source) or source, PREDICATES.get(predicate, predicate), labels.get(target) or target
            ) if value) or object_id
        else: label = labels.get(object_id) or object_id
        return {'identity': deepcopy(identity), 'installed': is_installed, 'writeAvailable': write,
            'unavailableReason': reason, 'reviewRevision': len(events),
            'objectRevision': max((e['revision'] for e in selected), default=0) if object_kind else None,
            'events': selected, 'currentActor': {'reviewerId': actor.principal_id, 'displayName': actor.display_name} if actor and actor.kind == 'human' else None,
            'actions': [{'code': key, 'label': value} for key, value in ACTIONS.items()],
            'availableEvidence': [{'evidenceId': key, 'label': str(value.get('quote') or value.get('sourceUri') or key)[:100]} for key, value in sorted(evidence.items())],
            'object': {'kind': object_kind, 'id': object_id, 'label': label} if object_kind else None,
            'notice': '人工意见不等于事实核验、正式人工通过或发布；要求补证不会自动启动研究。'}

    def read(self, run, report, identity, actor=None, *, object_kind=None, object_id=None):
        if (object_kind is None) != (object_id is None) or (object_kind is not None and object_kind not in KINDS):
            raise ReviewValidationError('objectKind and objectId must identify one exact report object')
        exact = identity_for(run, report)
        if any(identity.get(k) != exact.get(k) for k in ('runId', 'reportHash', 'sourceGraphHash')):
            raise ReviewConflictError('报告读取版本已变化，请重新读取当前报告')
        if object_kind:
            manifest = manifest_for(run, report, identity)
            if object_id not in manifest['objects'][object_kind]:
                raise ReviewValidationError('object is outside the current report version')
        with self.factory() as con:
            with con.cursor() as c:
                ready = installed(c)
                snapshot = self._snapshot(c, run['run_id'], identity) if ready else None
                events = self._events(c, snapshot['snapshot_id']) if snapshot else []
        return self._result(run, report, identity, actor, ready, events, object_kind, object_id)

    def overlay(self, run, identity, actor=None):
        with self.factory() as con:
            with con.cursor() as c:
                ready = installed(c)
                snapshot = self._snapshot(c, run['run_id'], identity) if ready else None
                events = self._events(c, snapshot['snapshot_id']) if snapshot else []
        superseded = {e['supersedesEventId'] for e in events if e['supersedesEventId']}
        unresolved = sum(e['action'] == 'objection' and e['eventId'] not in superseded for e in events)
        requests = sum(e['action'] == 'request-evidence' and e['eventId'] not in superseded for e in events)
        status = 'open-objections' if unresolved else 'has-comments' if events else 'not-requested'
        write, reason = self._capability(run, actor, ready)
        if not identity.get('reportHash'): write, reason = False, '尚无已保存报告'
        return {'status': status, 'label': {'open-objections': '有待回应异议', 'has-comments': '已有人工意见', 'not-requested': '未发起人工审阅'}[status],
            'writeAvailable': write, 'unavailableReason': reason, 'installed': ready,
            'reviewRevision': len(events), 'reviewedObjects': len({(e['objectKind'], e['objectId']) for e in events}),
            'unresolvedObjections': unresolved, 'evidenceRequests': requests,
            'notice': '这里记录人工意见；正式人工审核维度仍未建立，不会推导事实核验或发布。'}

    def append(self, run_id, body, actor, *, identity=None):
        _authorize(actor)
        body = normalized(body)
        request_hash = content_hash({'runId': run_id, 'reviewerId': actor.principal_id, 'request': body})
        try:
            with self.factory() as con:
                with con.cursor() as c:
                    if not installed(c): raise ReviewUnavailableError('人工审阅记录迁移尚未部署')
                    c.execute('SELECT enabled,kind,roles_json,display_name FROM agent_admin_identity WHERE principal_id=%s FOR SHARE', (actor.principal_id,))
                    actual_actor = c.fetchone()
                    if not actual_actor or not actual_actor['enabled'] or actual_actor['kind'] != 'human' or not set(actual_actor['roles_json']) & {'reviewer', 'admin'}:
                        raise PermissionError('human reviewer or administrator required')
                    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('report-review-idempotency:' + actor.principal_id + ':' + body['idempotencyKey'],))
                    c.execute('''SELECT e.*,s.run_id,s.report_hash,s.source_graph_hash,s.manifest_json
                        FROM agent_report_review_event e JOIN agent_report_review_snapshot s USING(snapshot_id)
                        WHERE e.reviewer_id=%s AND e.idempotency_key=%s''', (actor.principal_id, body['idempotencyKey']))
                    existing = c.fetchone()
                    if existing:
                        if existing['request_hash'] != request_hash:
                            raise ReviewConflictError('幂等键已用于不同内容、对象或报告版本')
                        c.execute('SELECT * FROM agent_run WHERE run_id=%s', (run_id,))
                        run = dict(c.fetchone())
                        saved_identity = existing['manifest_json']['identity']
                        events = self._events(c, existing['snapshot_id'])
                        current_report = report_for(run)
                        current_identity = identity_for(run, current_report)
                        version_current = all(current_identity[k] == saved_identity[k] for k in ('runId', 'reportHash', 'sourceGraphHash'))
                        result = self._result(run, current_report if version_current else {'runId': run_id, 'ontologyGraph': {}},
                            saved_identity, actor, True, events, body['objectKind'], body['objectId'])
                        if not version_current:
                            result.update(writeAvailable=False, unavailableReason='此意见属于旧报告版本，仅返回原始提交记录；请打开当前报告后继续审阅')
                        result['versionCurrent'] = version_current
                        return {**result, 'event': event_payload(dict(existing)), 'idempotent': True}
                    c.execute('SELECT * FROM agent_run WHERE run_id=%s FOR SHARE', (run_id,))
                    row = c.fetchone()
                    if not row: raise LookupError('Run not found')
                    run = dict(row)
                    if run['status'] not in DELIVERED:
                        raise ReviewConflictError('仅已交付且固定的报告可留审阅意见')
                    report = report_for(run)
                    exact = identity_for(run, report)
                    if body['reportHash'] != exact['reportHash'] or body['sourceGraphHash'] != exact['sourceGraphHash']:
                        raise ReviewConflictError('报告版本已变化，请重新读取当前版本，核对意见后再提交')
                    # The caller may supply the same server-read identity with a
                    # richer Profile display name; hashes always derive here.
                    identity = deepcopy(identity) if identity and all(identity.get(k) == exact[k] for k in ('runId', 'reportHash', 'sourceGraphHash')) else exact
                    manifest = json.loads(json.dumps(manifest_for(run, report, identity),
                        default=lambda value: value.isoformat() if isinstance(value, (datetime, date)) else str(value)))
                    if body['objectId'] not in manifest['objects'][body['objectKind']]:
                        raise ReviewValidationError('对象不属于该报告版本')
                    if not set(body['evidenceIds']) <= set(manifest['objects']['evidence']):
                        raise ReviewValidationError('引用证据不属于该报告版本')
                    evidence_to_validate = set(body['evidenceIds'])
                    if body['objectKind'] == 'evidence': evidence_to_validate.add(body['objectId'])
                    for evidence_id in sorted(evidence_to_validate):
                        try: source_excerpt(report, evidence_id, self.repo)
                        except (KeyError, ValueError, TypeError):
                            raise ReviewValidationError('引用证据缺少可校验的本次运行原文定位') from None
                    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('report-review-snapshot:' + run_id + ':' + body['reportHash'] + ':' + str(body['sourceGraphHash']),))
                    snapshot = self._snapshot(c, run_id, identity)
                    if not snapshot:
                        snapshot_id = new_id('review-snapshot')
                        c.execute('''INSERT INTO agent_report_review_snapshot(snapshot_id,run_id,report_hash,source_graph_hash,manifest_json,content_hash)
                            VALUES(%s,%s,%s,%s,%s,%s)''', (snapshot_id, run_id, body['reportHash'], body['sourceGraphHash'], Json(manifest), content_hash(manifest)))
                    else:
                        snapshot_id = snapshot['snapshot_id']
                        # Display-name changes do not alter an already frozen
                        # object/evidence manifest or invalidate existing opinions.
                        for key in ('objects', 'evidenceHashes', 'reportHash', 'sourceGraphHash'):
                            if snapshot['manifest_json'].get(key) != manifest.get(key):
                                raise ReviewConflictError('审阅对象清单已变化，请重新打开报告')
                        identity = snapshot['manifest_json']['identity']
                    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('report-review-object:' + snapshot_id + ':' + body['objectKind'] + ':' + body['objectId'],))
                    c.execute('''SELECT revision,event_id FROM agent_report_review_event WHERE snapshot_id=%s AND object_kind=%s AND object_id=%s ORDER BY revision DESC LIMIT 1''', (snapshot_id, body['objectKind'], body['objectId']))
                    previous = c.fetchone()
                    revision = previous['revision'] if previous else 0
                    if revision != body['expectedRevision']:
                        raise ReviewConflictError('该对象的审阅意见已更新，请读取最新意见并核对后再提交')
                    if body['supersedesEventId']:
                        c.execute('SELECT * FROM agent_report_review_event WHERE event_id=%s', (body['supersedesEventId'],))
                        old = c.fetchone()
                        if not old or old['snapshot_id'] != snapshot_id or old['object_kind'] != body['objectKind'] or old['object_id'] != body['objectId'] or old['reviewer_id'] != actor.principal_id:
                            raise ReviewConflictError('只能更正本人在同一报告版本、同一对象上的意见')
                        c.execute('SELECT 1 FROM agent_report_review_event WHERE supersedes_event_id=%s', (old['event_id'],))
                        if c.fetchone(): raise ReviewConflictError('该意见已有后继更正，请选择最新意见')
                    event_id = new_id('review-event')
                    record_hash = content_hash({'snapshotId': snapshot_id, 'reviewerId': actor.principal_id,
                        'requestHash': request_hash, 'revision': revision + 1, 'previousEventId': previous['event_id'] if previous else None})
                    c.execute('''INSERT INTO agent_report_review_event(event_id,snapshot_id,object_kind,object_id,action,
                        reviewer_id,reviewer_display_name,reason,evidence_ids_json,revision,expected_revision,
                        previous_event_id,supersedes_event_id,idempotency_key,request_hash,content_hash)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (event_id,snapshot_id,body['objectKind'],body['objectId'],body['action'],actor.principal_id,
                         actual_actor['display_name'],body['reason'],Json(body['evidenceIds']),revision+1,revision,
                         previous['event_id'] if previous else None,body['supersedesEventId'],body['idempotencyKey'],request_hash,record_hash))
                    events = self._events(c, snapshot_id)
                    event = next(e for e in events if e['eventId'] == event_id)
                    result = self._result(run, report, identity, actor, True, events, body['objectKind'], body['objectId'])
                    return {**result, 'event': event, 'idempotent': False}
        except (IntegrityError, errors.SerializationFailure):
            raise ReviewConflictError('审阅记录并发或约束冲突，请读取当前意见后重试') from None
