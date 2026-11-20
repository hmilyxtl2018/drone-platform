"""Database-backed context and one-use admissions for ontology document research."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets

from psycopg2.extras import Json

from .cognitive_identity import AGENT_KEYS, APPLICATION_KEY


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PURPOSE = '审查公开本体方法文献的概念、版本、约束与证据关系，比较研究解释及其局限。'
DEFAULT_QUESTION = '根据公开规范，比较 RDF/OWL 本体表达与 SHACL 约束检查的职责，解释证据追溯、版本变化和未知信息应如何处理，并用本体关系图呈现依据与缺口。'
MAX_QUESTION_LENGTH = 2000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def runtime_payload(data):
    return {key: deepcopy(data[key]) for key in ('question', 'context', 'budgetRef',
            'sourceArtifactRefs', 'capabilityArtifactRefs') if key in data}


def validate_context_record(row):
    """Resolve frozen references to their stored snapshot bytes."""
    from .cognitive_contracts import validate_context
    context = row['context_json']
    if digest(context) != row['content_hash']:
        raise ValueError('context integrity mismatch')
    validate_context(context)
    snapshots = row.get('snapshots_json')
    if not isinstance(snapshots, dict):
        raise ValueError('context snapshots unavailable')
    for field, name in (('ontologySnapshotRef', 'report-ontology'), ('ontologySchemaRef', 'report-shapes'),
                        ('scenarioRef', 'scenario'), ('sourcePolicyRef', 'sourcePolicy')):
        snapshot = snapshots.get(name) or {}
        if snapshot.get('ref') != context[field] or digest(snapshot.get('body')) != context[field]['contentHash']:
            raise ValueError('context snapshot fingerprint mismatch')
    if context['scenarioRef']['kind'] != 'public-research' or context['ruleSetRef']['selection'] != 'not-applicable':
        raise PermissionError('only the installed document research context is admitted')
    policy = snapshots['sourcePolicy']['body']
    if policy != {'allowedDomains': ['w3.org', 'nist.gov'], 'includeSubdomains': True,
                  'scope': 'ontology-method-document-review'}:
        raise PermissionError('context source scope is not installed')
    return deepcopy(context)


def seed_context(cursor):
    """Install real local model snapshots, never fabricated research findings."""
    snapshots = {}
    for name, relative in (('report-ontology', 'ontology/research-report/report-ontology.ttl'),
                           ('report-shapes', 'ontology/research-report/report-shapes.ttl')):
        body = {'name': name, 'format': 'text/turtle', 'content': (ROOT / relative).read_text(encoding='utf-8')}
        snapshots[name] = {'body': body, 'ref': {'snapshotId': name + ':' + digest(body)[:24],
                           'version': digest(body)[:16], 'contentHash': digest(body)}}
    scenario = {'kind': 'public-research', 'purpose': 'ontology-method-document-review',
                'description': '公开规范与本地研究报告本体模型的语义研究；不包含实战任务或仿真交战行动。'}
    policy = {'allowedDomains': ['w3.org', 'nist.gov'], 'includeSubdomains': True,
              'scope': 'ontology-method-document-review'}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    context = {'researchPurpose': DEFAULT_PURPOSE,
       'scenarioRef': {'scenarioId': 'ontology-method-research', 'kind': 'public-research',
                       'version': '1', 'contentHash': digest(scenario)},
       'ontologySnapshotRef': snapshots['report-ontology']['ref'],
       'ontologySchemaRef': snapshots['report-shapes']['ref'],
       'ruleSetRef': {'selection': 'not-applicable', 'reason': '本任务研究本体表达和证据治理方法，不适用交战或实际行动规则。'},
       'timeScope': {'asOf': now.isoformat(), 'validFrom': (now - timedelta(days=3650)).isoformat(),
                     'validTo': (now + timedelta(days=3650)).isoformat()},
       'sourcePolicyRef': {'snapshotId': 'ontology-method-sources', 'version': '1', 'contentHash': digest(policy)},
       'inputArtifactRefs': []}
    snapshots.update(scenario={'body': scenario, 'ref': context['scenarioRef']},
                     sourcePolicy={'body': policy, 'ref': context['sourcePolicyRef']})
    # Re-running deployment keeps the exact existing frozen version and date.
    identity = 'ontology-method-document-review.v1'
    cursor.execute('INSERT INTO agent_cognitive_context(context_id,label,context_json,snapshots_json,content_hash) '
                   'VALUES(%s,%s,%s,%s,%s) ON CONFLICT(context_id) DO NOTHING',
                   (identity, '公开本体规范与证据方法研究', Json(context), Json(snapshots), digest(context)))


def readiness(engine, agent_key=APPLICATION_KEY):
    issues = []
    if agent_key not in AGENT_KEYS:
        raise LookupError('unknown cognitive role')
    try:
        profile = engine.loader.load(agent_key)
        from .cognitive_implementation import assert_profile_implementation
        try:
            assert_profile_implementation(profile)
        except ValueError:
            issues.append({'code': 'cognitive-implementation-mismatch',
                           'message': '认知执行器与冻结版本不一致，须发布已审查版本。'})
        from agent_tools.research import PROPERTIES, SUPPORTED_TREND_VERSIONS, implementation_hash
        actual_research_hash = implementation_hash()
        def research_binding_issues(candidate, parent_step=None):
            for tool_key, binding in candidate.tools.items():
                is_research = tool_key in PROPERTIES or (tool_key == 'analysis.change_point'
                    and binding.version in SUPPORTED_TREND_VERSIONS)
                if binding.enabled and is_research and binding.config.get('implementationHash') != actual_research_hash:
                    issues.append({'code': 'research-implementation-mismatch',
                        'message': '研究工具实现与已冻结绑定不一致，需发布并校验兼容版本',
                        'toolKey': tool_key, 'agentKey': candidate.agent_key,
                        **({'stepKey': parent_step} if parent_step else {})})
        research_binding_issues(profile)
        from .runtime_contracts import fixed_runtime_errors
        issues.extend({'code': e['code'], 'message': e.get('message') or e['code'],
                       'stepKey': e['stepKey']} for e in fixed_runtime_errors(profile))
        for step in profile.steps:
            if not engine.executor.has_executor(agent_key, step.key):
                issues.append({'code': 'executor-not-installed', 'message': '节点执行器未安装', 'stepKey': step.key})
            for key in step.tools:
                binding = profile.tools.get(key)
                if not binding or not binding.enabled or key not in engine.gateway.handlers:
                    issues.append({'code': 'tool-not-ready', 'message': '节点工具未安装或未启用', 'stepKey': step.key})
        if not profile.execution_baseline.get('baselineId') or not profile.execution_baseline.get('validation', {}).get('accepted'):
            issues.append({'code': 'baseline-not-registered', 'message': '执行基线尚未登记或校验未通过'})
        if not profile.harness_contract:
            issues.append({'code': 'harness-missing', 'message': 'Harness 契约未登记'})
        if not getattr(profile, 'goal_contract', {}):
            issues.append({'code': 'goal-contract-missing', 'message': '目标契约未登记'})
        if agent_key == APPLICATION_KEY:
            from .orchestration import constrained_child_profile
            for node in profile.execution_baseline.get('graph', {}).get('nodes', []):
                child_key = node.get('childAgentKey')
                if not child_key:
                    continue
                try:
                    child = engine.loader.load(child_key)
                    projected = constrained_child_profile(profile, child, profile.resource_policy['budgetLimits'], node['id'])
                    research_binding_issues(projected, node['id'])
                    for step in projected.steps:
                        for tool in step.tools:
                            if tool not in projected.tools or not projected.tools[tool].enabled:
                                issues.append({'code': 'delegated-tool-not-granted', 'message': '子 Agent 工具与父授权不一致',
                                               'stepKey': node['id'], 'childAgentKey': child_key})
                except (ValueError, KeyError, LookupError):
                    issues.append({'code': 'delegation-not-ready', 'message': '子 Agent 委派契约未就绪',
                                   'stepKey': node['id'], 'childAgentKey': child_key})
    except (ValueError, KeyError, LookupError):
        issues.append({'code': 'profile-unavailable', 'message': '角色或依赖尚未登记完整'})
    return {'agentKey': agent_key, 'ready': not issues, 'status': 'ready' if not issues else 'blocked',
            'issues': issues, 'runtimeInstalled': not any(e['code'] == 'executor-not-installed' for e in issues),
            'scope': 'document-and-ontology-method-research'}


class CognitiveAdmissionService:
    def __init__(self, engine):
        self.engine = engine
        self.factory = getattr(engine, 'base_connection_factory', engine.repo.connection_factory)

    def contexts(self, agent_key=APPLICATION_KEY):
        with self.factory() as con:
            con.set_session(readonly=True)
            with con.cursor() as c:
                c.execute('SELECT context_id,label,context_json,snapshots_json,content_hash FROM agent_cognitive_context ORDER BY context_id')
                rows = c.fetchall()
        result = []
        for row in rows:
            context = validate_context_record(row)
            result.append({'contextId': row['context_id'], 'label': row['label'], 'context': context,
                           'sourceArtifactRefs': [], 'capabilityArtifactRefs': []})
        return {'contexts': result, 'defaultQuestion': DEFAULT_QUESTION, 'readiness': readiness(self.engine, agent_key)}

    def admit(self, body, principal_id):
        from .cognitive_contracts import validate_schema
        from .phase3_repository import resolve_profile_budget
        if set(body) - {'agentKey', 'question', 'contextId', 'researchPurpose', 'budgetLimits'}:
            raise ValueError('unsupported admission field')
        agent_key = body.get('agentKey', APPLICATION_KEY)
        # The four professional roles receive only explicit, typed delegations.
        if agent_key != APPLICATION_KEY:
            return {'admissionStatus': 'rejected', 'code': 'out-of-scope', 'reason': '专业角色从应用级任务受托执行。',
                    'dispatchAllowed': False, 'runCreated': False}
        missing = [key for key in ('question', 'contextId') if not isinstance(body.get(key), str) or not body[key].strip()]
        if missing:
            return {'admissionStatus': 'needs-context', 'missingFields': [{'path': '/' + k, 'reason': '必填研究上下文'} for k in missing],
                    'resolutionHints': ['填写研究问题并选择已登记的研究上下文。'], 'dispatchAllowed': False, 'runCreated': False}
        if len(body['question']) > MAX_QUESTION_LENGTH:
            return {'admissionStatus': 'rejected', 'code': 'question-too-long',
                    'reason': '研究问题最多 2000 字符；请精简问题后重试，服务不会截断原文。',
                    'dispatchAllowed': False, 'runCreated': False}
        if not readiness(self.engine, agent_key)['ready']:
            raise ValueError('cognitive runtime is not ready')
        text = body['question'] + ' ' + str(body.get('researchPurpose', ''))
        if re.search(r'(?i)(目标分配|交战授权|打击方案|作战指令|武器控制|突防路径|火力分配|target assignment|weapon control|engagement authorization)', text):
            return {'admissionStatus': 'rejected', 'code': 'out-of-scope',
                    'reason': '此入口用于文献与本体语义研究，不能生成或审查行动方案。', 'dispatchAllowed': False, 'runCreated': False}
        profile = self.engine.loader.load(agent_key)
        limits = resolve_profile_budget(profile, body.get('budgetLimits') or {})
        with self.factory() as con:
            with con.cursor() as c:
                c.execute('SELECT * FROM agent_cognitive_context WHERE context_id=%s', (body['contextId'],))
                row = c.fetchone()
                if not row:
                    raise ValueError('selected context is unavailable or changed')
                context = validate_context_record(row)
                if body.get('researchPurpose'):
                    context['researchPurpose'] = body['researchPurpose']
                identity = 'cognitive-admission-' + secrets.token_hex(16)
                budget = {'budgetId': identity, 'ownerId': principal_id, 'goalLineageId': identity,
                          'contentHash': digest(limits)}
                data = {'question': body['question'].strip(), 'context': context, 'budgetRef': budget,
                        'sourceArtifactRefs': [], 'capabilityArtifactRefs': []}
                validate_schema('TaskInput', data)
                data.update(executionMode='profile-run', automationMode='unattended-draft', budgetLimits=limits,
                            cognitiveAdmissionId=identity, cognitiveContextId=body['contextId'],
                            scope={'contextId': body['contextId'], **deepcopy(context)})
                validate_schema('FrozenTaskInput', data)
                c.execute('INSERT INTO agent_cognitive_admission(admission_id,principal_id,agent_key,input_json,content_hash,expires_at) '
                          'VALUES(%s,%s,%s,%s,%s,%s)', (identity, principal_id, agent_key, Json(data), digest(data),
                          datetime.now(timezone.utc) + timedelta(hours=1)))
        result = {'admissionStatus': 'accepted', 'dispatchAllowed': True, 'inputHash': digest(data), 'input': data}
        validate_schema('AdmissionResult', result)
        return result


def validate_root(cursor, profile, data, *, consume_run_id=None):
    """Called within root creation's transaction; one admission creates one Run."""
    from .task_intent import _issuer
    identity = data.get('cognitiveAdmissionId')
    cursor.execute('SELECT * FROM agent_cognitive_admission WHERE admission_id=%s FOR UPDATE', (identity,))
    row = cursor.fetchone()
    if (not row or row['agent_key'] != profile.agent_key or digest(row['input_json']) != row['content_hash']
            or digest(data) != row['content_hash']):
        raise PermissionError('cognitive admission does not match the frozen input')
    issuer_id, _ = _issuer.get()
    if row['principal_id'] != issuer_id:
        raise PermissionError('cognitive admission belongs to another principal')
    if row['expires_at'] <= datetime.now(timezone.utc):
        raise PermissionError('cognitive admission expired')
    cursor.execute('SELECT run_id FROM agent_cognitive_admission_use WHERE admission_id=%s', (identity,))
    used = cursor.fetchone()
    if used:
        raise PermissionError('cognitive admission already created a Run')
    if consume_run_id:
        cursor.execute('INSERT INTO agent_cognitive_admission_use(admission_id,run_id) VALUES(%s,%s)',
                       (identity, consume_run_id))
