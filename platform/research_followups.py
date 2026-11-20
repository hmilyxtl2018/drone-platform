"""Versioned, object-directed, finite public evidence collection plans."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone

try:
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
    from osint_store import OSINTValidationError
    from osint_sources import SEARCH_SCOPE_BY_ID
    from ontology_attribute_admission import ATTRIBUTE_PREDICATES
    from object_attribute_extraction import ATTRIBUTE_TEMPLATES
except ModuleNotFoundError:
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound, IntelligenceValidationError
    from .osint_store import OSINTValidationError
    from .osint_sources import SEARCH_SCOPE_BY_ID
    from .ontology_attribute_admission import ATTRIBUTE_PREDICATES
    from .object_attribute_extraction import ATTRIBUTE_TEMPLATES

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_followup (
 followup_id TEXT PRIMARY KEY,assessment_id TEXT NOT NULL,graph_revision_id TEXT NOT NULL,
 graph_element_id TEXT NOT NULL,purpose TEXT NOT NULL,payload TEXT NOT NULL,
 request_hash TEXT NOT NULL UNIQUE,task_id TEXT REFERENCES osint_task(task_id),
 created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS research_followup_input_immutable BEFORE UPDATE ON research_followup
 WHEN OLD.followup_id IS NOT NEW.followup_id OR OLD.assessment_id IS NOT NEW.assessment_id
 OR OLD.graph_revision_id IS NOT NEW.graph_revision_id OR OLD.graph_element_id IS NOT NEW.graph_element_id
 OR OLD.purpose IS NOT NEW.purpose OR OLD.payload IS NOT NEW.payload OR OLD.request_hash IS NOT NEW.request_hash
 OR OLD.created_at IS NOT NEW.created_at OR (OLD.task_id IS NOT NULL AND OLD.task_id IS NOT NEW.task_id)
 BEGIN SELECT RAISE(ABORT,'followup targets and task association are immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_followup_retained BEFORE DELETE ON research_followup
 BEGIN SELECT RAISE(ABORT,'followup plans are retained'); END;
"""
PURPOSES = {'attribute-gap', 'independent-check', 'public-event-imagery'}
TARGET_PURPOSE = {'property': 'attribute-gap', 'identity': 'independent-check', 'public-event-imagery': 'public-event-imagery'}
STOP = '一次有界检索后停止；无相关结果保留缺口，不自动扩大范围、重试或提高置信度。'
FIELD_SEARCH = {
    'category': ('公开型号身份与类别', '(model OR designation OR product) public description'),
    'architecture': ('架构描述', '("open architecture" OR modularity OR architecture) public description'),
    'interface-standard': ('接口与标准', '(interface OR standard OR protocol OR SDK) public documentation'),
    'interoperability': ('互操作描述', '(interoperability OR compatibility OR integration) public evaluation'),
    'human-oversight': ('人工监督方式', '("human oversight" OR "human supervision" OR "operator control") public documentation'),
    'public-test-status': ('公开试验状态', '(demonstration OR evaluation OR trial) public test status'),
    'industrial-maturity': ('公开成熟度描述', '(prototype OR maturity OR certification) public status'),
    'organization-role': ('组织角色', '(manufacturer OR developer OR research) public organization profile'),
    'public-program-role': ('公开项目角色', '(participant OR developer OR supplier) public program role'),
    'program-goal': ('公开项目目标', '("program objective" OR "project goal") public announcement'),
    'program-stage': ('公开项目阶段', '(prototype OR contract OR milestone) public program status'),
    'public-event-status': ('公开事件状态', '(demonstration OR contract OR production) public announcement status'),
    'test-context': ('受控试验条件', '("controlled test" OR "laboratory test" OR evaluation) public test conditions'),
    'endurance-duration': ('续航时间', '(endurance OR "flight time" OR "time aloft") public platform specification'),
    'flight-range': ('飞行航程', '("flight range" OR "ferry range" OR "operational range") public platform specification'),
    'payload-capacity': ('载荷容量', '("payload capacity" OR "payload weight" OR payload) public platform specification'),
    'communications-link': ('通信链路', '(datalink OR "data link" OR communications) public platform specification'),
    'autonomy-mode': ('自主模式', '(autonomous OR autonomy OR "autonomous mode") public platform demonstration'),
    'participants': ('公开参与对象', '(participant OR involved OR conducted) public event announcement'),
    'procurement-stage': ('公开采购阶段', '(award OR contract OR delivery) public procurement stage'),
    'production-stage': ('公开生产阶段', '(announcement OR certification OR delivery) public production status'),
    'event-date': ('公开事件日期', '(date OR announced OR demonstration) public event record'),
}
PREDICATE_FIELDS = {'reportedStandard': 'interface-standard', 'reportedInterface': 'interface-standard',
                    'reportedArchitecture': 'architecture', 'reportedHumanOversight': 'human-oversight',
                    'reportedTestStatus': 'public-test-status', 'reportedMaturity': 'industrial-maturity',
                    'reportedContractStage': 'procurement-stage', 'reportedEventDate': 'event-date'}


def _stable_id(prefix, value):
    return prefix + '-' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def _attribute_key(attribute):
    key = attribute.get('attributeKey')
    if key in FIELD_SEARCH:
        return key
    name = str(attribute.get('name') or '').casefold().strip()
    for predicate, entry in ATTRIBUTE_PREDICATES.items():
        if name in {alias.casefold() for alias in entry['aliases']}:
            return PREDICATE_FIELDS[predicate]
    return None


def _source_context(refs):
    return [{'itemId': ref.get('itemId'), 'sourceUri': ref.get('sourceUri'),
             'sourceVersion': ref.get('sourceVersion'), 'contentHash': ref.get('contentHash'),
             'sourceRole': ref.get('sourceRole') or 'unclassified',
             'contentNature': ref.get('contentNature') or 'unclassified',
             'publishedAt': ref.get('publishedAt'), 'publicationDateMissing': not bool(ref.get('publishedAt'))}
            for ref in refs[:4] if isinstance(ref, dict)]


class ResearchFollowups:
    MAX_TASKS, MAX_ITEMS, MAX_ITEMS_PER_TASK = 4, 32, 8

    def __init__(self, osint_store, intelligence_store):
        self.osint, self.store = osint_store, intelligence_store
        with self.osint.connect() as con:
            con.executescript(SCHEMA)

    def _context(self, assessment_id):
        assessment = self.store.get_assessment(assessment_id)
        if not assessment:
            raise IntelligenceNotFound('assessment not found')
        graph = self.store.get_graph(assessment_id)
        if not graph:
            raise IntelligenceValidationError('extract objects before planning followups')
        if assessment.get('status') == 'withdrawn':
            raise IntelligenceConflict('withdrawn report cannot start followups')
        return assessment, graph

    @staticmethod
    def _anchor(node, graph):
        if node.get('type') != 'EventRecord':
            return node['label']
        refs = (node.get('payload') or {}).get('evidenceRefs') or []
        text = ' '.join(str(ref.get('quote') or '') for ref in refs)
        adjacent = {edge['to'] if edge['from'] == node['id'] else edge['from']
                    for edge in graph.get('edges', []) if node['id'] in {edge['from'], edge['to']}
                    and edge.get('relation') in {'testsEquipment', 'involvesEquipment', 'involvesOrganization', 'announcedBy', 'participatesIn'}
                    and (edge.get('payload') or {}).get('extractionMethod') == 'llm'}
        related = [n['label'] for n in graph['nodes'] if n['id'] in adjacent
                   and n.get('type') not in {'EventRecord', 'EvidenceSource', 'TrendHypothesis'}
                   and n['label'].casefold() in text.casefold()]
        # A public event can be linked through its own saved entity relation.
        # Without it, a translated event label is not a safe retrieval anchor.
        return related[0] if related else None

    def plan(self, assessment_id):
        assessment, graph = self._context(assessment_id)
        targets = []
        for node in graph['nodes']:
            payload = node.get('payload') or {}
            if payload.get('extractionMethod') != 'llm':
                continue
            anchor = self._anchor(node, graph)
            if not anchor:
                continue
            attrs = payload.get('attributes') or []
            kind = payload.get('eventKind') if node.get('type') == 'EventRecord' else node.get('type')
            source_map = {source['itemId']: source for source in assessment.get('sourceSnapshot') or []}
            def context_for(refs):
                # Publisher/date context comes from the immutable report, not
                # model-provided graph source classifications.
                refs = [{**{key: source_map[ref['itemId']].get(key)
                                  or (source_map[ref['itemId']].get('provenance') or {}).get(key)
                                  for key in ('itemId', 'sourceUri', 'sourceVersion', 'contentHash', 'sourceRole', 'contentNature', 'publishedAt')}}
                        for ref in refs if isinstance(ref, dict) and ref.get('itemId') in source_map]
                return _source_context(refs)
            identity_context = context_for(payload.get('evidenceRefs') or [])
            retained_keys = set()
            for index, attribute in enumerate(attrs[:12]):
                if not isinstance(attribute, dict):
                    continue
                key = _attribute_key(attribute)
                if key is None:
                    continue
                retained_keys.add(key)
                path = f'payload.attributes[{index}].value'
                value = attribute.get('value')
                context = context_for(attribute.get('evidenceRefs') or [])
                claim = str(value)[:240]
                reason = f'核验该对象“{attribute.get("name")} = {claim}”的明确归属、限定条件和独立支持/反证；厂商声明或项目目标不等于实测，转载不计独立来源。'
                target = self._target(node, 'identity', 'independent-check',
                                      f'"{anchor}" {FIELD_SEARCH[key][1]} independent evaluation', reason,
                                      attributeKey=key, attributeLabel=attribute.get('name') or FIELD_SEARCH[key][0],
                                      propertyPath=path, assertionId=_stable_id('assertion', [node['id'], path, value, attribute.get('evidenceRefs')]),
                                      rawValue=value, sourceContext=context, claimKind=attribute.get('claimKind') or 'source-report')
                targets.append(target)
            if node.get('type') == 'EventRecord' and payload.get('date'):
                path, value = 'payload.date', payload['date']
                targets.append(self._target(node, 'identity', 'independent-check',
                    f'"{anchor}" {FIELD_SEARCH["event-date"][1]} independent evaluation',
                    f'核验该公开事件的“事件日期 = {str(value)[:120]}”；查明原发布记录与直接对象关联，不用资料发布日期替代事件日期。',
                    attributeKey='event-date', attributeLabel='公开事件日期', propertyPath=path,
                    assertionId=_stable_id('assertion', [node['id'], path, value, payload.get('dateEvidenceRefs')]),
                    rawValue=value, sourceContext=context_for(payload.get('dateEvidenceRefs') or []), claimKind='source-report'))
                retained_keys.add('event-date')
            coverage = (payload.get('attributeExtraction') or {}).get('coverage') or {}
            states = coverage.get('fieldStates') or []
            missing = [field.get('attributeKey') for field in states if isinstance(field, dict)
                       and field.get('state') != 'retained-draft' and field.get('attributeKey') in FIELD_SEARCH]
            # This fallback is an explicitly declared schema template, not a
            # model judgment that the object actually possesses those fields.
            missing = missing or [key for key in ATTRIBUTE_TEMPLATES.get(kind, ()) if key not in retained_keys]
            if node.get('type') == 'EventRecord' and not payload.get('date'):
                missing.append('event-date')
            for key in dict.fromkeys(missing):
                if key in retained_keys:
                    continue
                # Legacy object templates can contain fields that predate the
                # directed follow-up vocabulary. Keep their gap visible in
                # the extraction contract, but never issue an unscoped query.
                if key not in FIELD_SEARCH:
                    continue
                label, terms = FIELD_SEARCH[key]
                reason = f'当前已读取材料未保留“{label}”属性。只补采该对象对此字段的明确公开描述、归属和适用条件；缺失不代表不存在，不推算属性值。'
                targets.append(self._target(node, 'property', 'attribute-gap', f'"{anchor}" {terms}', reason,
                                            attributeKey=key, attributeLabel=label, sourceContext=identity_context,
                                            fieldState=next((field.get('state') for field in states if isinstance(field, dict) and field.get('attributeKey') == key), 'not-extracted')))
            if node.get('type') in {'EventRecord', 'EquipmentModel', 'Technology', 'Program'}:
                event = node if node.get('type') == 'EventRecord' else next((candidate for candidate in graph['nodes']
                    if candidate.get('type') == 'EventRecord' and self._anchor(candidate, graph) == anchor), None)
                date = (event.get('payload') or {}).get('date') if event else None
                query = f'"{anchor}" satellite imagery public historical publication'
                if date:
                    query += ' ' + str(date)[:120]
                targets.append(self._target(node, 'public-event-imagery', 'public-event-imagery', query,
                    '只寻找该对象或有直接出处关联的公开历史事件之影像发布页、提供方、采集日期与许可；不把普通照片当卫星影像，零结果保留缺口。',
                    eventId=event['id'] if event else None, eventLabel=event['label'] if event else None,
                    eventDate=date, sourceContext=context_for((event.get('payload') or {}).get('evidenceRefs') or []) if event else identity_context))
        followups = self.list(assessment_id)
        current = [record for record in followups if not record['staleGraph']]
        remaining_tasks = max(0, self.MAX_TASKS - len(current))
        remaining_items = max(0, self.MAX_ITEMS - sum(record['maxItems'] for record in current))
        budget = {'maxTasks': self.MAX_TASKS, 'maxItems': self.MAX_ITEMS, 'maxItemsPerTask': self.MAX_ITEMS_PER_TASK,
                  'usedTasks': len(current), 'reservedItems': self.MAX_ITEMS - remaining_items,
                  'remainingTasks': remaining_tasks, 'remainingItems': remaining_items,
                  'exhausted': not remaining_tasks or not remaining_items, 'scope': 'current-graph-revision'}
        for target in targets:
            target['targetId'] = _stable_id('target', [graph['graphRevisionId'], target['objectId'], target['targetType'],
                                                       target.get('propertyPath'), target.get('attributeKey'), target.get('eventId')])
            previous = next((record for record in current if record.get('targetId') == target['targetId']), None)
            target.update(executionStatus=previous['taskStatus'] if previous else 'not-started',
                          followupTaskId=previous['followupTaskId'] if previous else None,
                          collectedItems=previous['collectedItems'] if previous else 0,
                          executable=not previous and remaining_tasks > 0 and remaining_items >= target['maxItems'])
        return {'graphRevisionId': graph['graphRevisionId'], 'targets': targets,
                'budget': budget, 'followups': followups, 'stoppingCondition': STOP,
                'planningMethod': 'deterministic-object-assertion-templates', 'modelJudgment': False, 'truthVerified': False}

    @staticmethod
    def _target(node, target_type, purpose, query, reason, **details):
        context = details.get('sourceContext') or []
        dates = ', '.join(str(row['publishedAt']) for row in context if row.get('publishedAt')) or '未记录'
        natures = ', '.join(dict.fromkeys(row.get('contentNature') or row.get('sourceRole') or 'unclassified' for row in context)) or 'unclassified'
        question = f'"{query.split(chr(34))[1]}"：{reason} 当前来源性质：{natures}；资料发布日期：{dates}。发布日期缺失时需核对原发布页，不能据此推断事件日期或当前状态。'
        return {'objectId': node['id'], 'objectLabel': node['label'], 'targetType': target_type,
                'purpose': purpose, 'query': query, 'question': question, 'keywords': ['"' + query.split('"')[1] + '"'],
                'reason': reason, 'rationale': reason, 'maxItems': 8, 'sinceDays': 365,
                'sourceIds': ['public-imagery'] if purpose == 'public-event-imagery' else ['defense-official', 'test-evaluation'],
                'stoppingCondition': STOP, **details}

    def list(self, assessment_id):
        graph = self.store.get_graph(assessment_id)
        with self.osint.connect() as con:
            rows = con.execute('SELECT * FROM research_followup WHERE assessment_id=? ORDER BY created_at', (assessment_id,)).fetchall()
        result = []
        for row in rows:
            record = json.loads(row['payload'])
            task = self.osint.get_task(row['task_id']) if row['task_id'] else None
            record.update(followupId=row['followup_id'], followupTaskId=row['task_id'],
                          taskStatus=(task or {}).get('status', 'not-started'),
                          collectedItems=len((task or {}).get('items') or []),
                          staleGraph=not graph or graph['graphRevisionId'] != row['graph_revision_id'],
                          truthVerified=False, confidencePromotion=False)
            result.append(record)
        return result

    def execute_target(self, assessment_id, payload):
        if not isinstance(payload, dict) or set(payload) - {'graphRevisionId', 'objectId', 'targetType', 'targetId'}:
            raise IntelligenceValidationError('select a server-generated followup target')
        plan = self.plan(assessment_id)
        if payload.get('graphRevisionId') != plan['graphRevisionId']:
            raise IntelligenceConflict('graph revision changed; reload followup plan')
        target = next((t for t in plan['targets'] if t['objectId'] == payload.get('objectId') and t['targetType'] == payload.get('targetType')
                       and ('targetId' not in payload or t['targetId'] == payload['targetId'])), None)
        if not target:
            raise IntelligenceValidationError('followup target is unavailable')
        return self.create(assessment_id, {'graphRevisionId': plan['graphRevisionId'], 'graphElementId': target['objectId'],
                                         'targetId': target['targetId'],
                                         **{k: target[k] for k in ('purpose', 'query', 'sourceIds', 'maxItems', 'sinceDays', 'rationale')}})

    def create(self, assessment_id, payload):
        allowed = {'graphRevisionId', 'graphElementId', 'purpose', 'query', 'sourceIds', 'maxItems', 'sinceDays', 'rationale', 'targetId'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise IntelligenceValidationError('unsupported followup fields')
        assessment, graph = self._context(assessment_id)
        if payload.get('graphRevisionId') != graph['graphRevisionId']:
            raise IntelligenceConflict('graph revision changed; reload followup plan')
        node = next((n for n in graph['nodes'] if n['id'] == payload.get('graphElementId') and (n.get('payload') or {}).get('extractionMethod') == 'llm'), None)
        if not node:
            raise IntelligenceValidationError('followup must target a saved extracted object or public event')
        purpose = payload.get('purpose')
        if purpose not in PURPOSES:
            raise IntelligenceValidationError('unsupported followup purpose')
        anchor = self._anchor(node, graph)
        if not anchor:
            raise IntelligenceValidationError('public event lacks a source-backed retrieval anchor')
        query = payload.get('query') or f'"{anchor}" public test evaluation report'
        if not isinstance(query, str) or not 1 <= len(query) <= 1000 or not query.startswith(f'"{anchor}"'):
            raise IntelligenceValidationError('query must begin with the exact saved object retrieval anchor')
        source_ids = payload.get('sourceIds') or (['public-imagery'] if purpose == 'public-event-imagery' else ['defense-official', 'test-evaluation'])
        if not isinstance(source_ids, list) or not 1 <= len(source_ids) <= 2 or any(s not in SEARCH_SCOPE_BY_ID for s in source_ids) or len(set(source_ids)) != len(source_ids):
            raise OSINTValidationError('select at most two distinct curated publisher scopes')
        if (purpose == 'public-event-imagery') != (source_ids == ['public-imagery']):
            raise OSINTValidationError('image discovery and body evidence require separate followup tasks')
        limit, days = payload.get('maxItems', 8), payload.get('sinceDays', 365)
        if type(limit) is not int or not 1 <= limit <= self.MAX_ITEMS_PER_TASK or type(days) is not int or not 1 <= days <= 365:
            raise OSINTValidationError('followup budget must be 1–8 items and 1–365 publication days')
        reason = payload.get('rationale')
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1600:
            raise IntelligenceValidationError('a specific gap or assertion to verify is required')
        frozen_target = None
        if 'targetId' in payload:
            frozen_target = next((target for target in self.plan(assessment_id)['targets'] if target['targetId'] == payload['targetId']
                                  and target['objectId'] == node['id'] and target['purpose'] == purpose), None)
            if frozen_target is None or any(payload.get(key) != frozen_target.get(key) for key in ('query', 'sourceIds', 'maxItems', 'sinceDays', 'rationale')):
                raise IntelligenceValidationError('followup target fields must match the exact server-generated assertion plan')
        record = {**payload, 'assessmentId': assessment_id, 'parentTaskId': assessment['taskId'], 'objectLabel': node['label'],
                  'query': query, 'sourceIds': source_ids, 'maxItems': limit, 'sinceDays': days,
                  'sourceSnapshotHash': self.store._snapshot_hash(assessment['sourceSnapshot']),
                  'graphContentHash': graph['contentHash'], 'stoppingCondition': STOP,
                  'evidenceIntegration': 'new-task-snapshot-pending-review'}
        if frozen_target:
            record.update({key: frozen_target[key] for key in ('question', 'targetType', 'propertyPath', 'assertionId', 'attributeKey',
                           'attributeLabel', 'sourceContext', 'rawValue', 'claimKind', 'fieldState', 'eventId', 'eventLabel', 'eventDate') if key in frozen_target})
        text = json.dumps(record, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(text.encode()).hexdigest()
        # Serialize budget reservation and task creation. Partial creation is
        # retained as not-started; it does not silently launch another task.
        with self.osint.lock:
            with self.osint.connect() as con:
                rows = con.execute('SELECT * FROM research_followup WHERE assessment_id=? AND graph_revision_id=?', (assessment_id, graph['graphRevisionId'])).fetchall()
                old = next((r for r in rows if r['request_hash'] == digest), None)
                if old:
                    return {'task': self.osint.get_task(old['task_id']) if old['task_id'] else None,
                            'followup': next(r for r in self.list(assessment_id) if r['followupId'] == old['followup_id']), 'duplicate': True}
                if len(rows) >= self.MAX_TASKS or sum(json.loads(r['payload'])['maxItems'] for r in rows) + limit > self.MAX_ITEMS:
                    raise IntelligenceConflict('followup plan budget exhausted for this graph revision')
                followup_id = 'followup-' + secrets.token_hex(10)
                con.execute('INSERT INTO research_followup(followup_id,assessment_id,graph_revision_id,graph_element_id,purpose,payload,request_hash,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (followup_id, assessment_id, graph['graphRevisionId'], node['id'], purpose, text, digest,
                             datetime.now(timezone.utc).isoformat()))
            keywords = [f'"{anchor}"']
            if purpose != 'public-event-imagery':
                keywords.append(query[len(keywords[0]):].strip())
            task = self.osint.create_task({'title': f'{node["label"]} · 定向补证', 'question': record.get('question') or query,
                                          'keywords': keywords, 'sourceIds': source_ids, 'maxItems': limit,
                                          'sinceDays': days, 'requestedBy': 'human-selected-followup'})
            with self.osint.connect() as con:
                con.execute('UPDATE research_followup SET task_id=? WHERE followup_id=?', (task['id'], followup_id))
        return {'task': task, 'followup': next(r for r in self.list(assessment_id) if r['followupId'] == followup_id), 'duplicate': False}

    def permits_imagery_link(self, task_id, assessment_id, graph_revision_id, element_id):
        with self.osint.connect() as con:
            row = con.execute('SELECT 1 FROM research_followup WHERE task_id=? AND assessment_id=? AND graph_revision_id=? AND graph_element_id=? AND purpose=?',
                              (task_id, assessment_id, graph_revision_id, element_id, 'public-event-imagery')).fetchone()
        return bool(row)

    def parent(self, task_id):
        with self.osint.connect() as con:
            row = con.execute('SELECT assessment_id,followup_id FROM research_followup WHERE task_id=?', (task_id,)).fetchone()
        if not row:
            return None
        return next(r for r in self.list(row['assessment_id']) if r['followupId'] == row['followup_id'])
