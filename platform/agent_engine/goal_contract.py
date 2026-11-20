"""Versioned goals; authority remains exclusively in TaskIntent and Harness.

The v1 evaluator proves delivery/traceability, not semantic truth or complete
coverage of an arbitrary user question. Unknown checks never count as passes.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import secrets

from psycopg2.extras import Json

EVALUATOR = 'goal-delivery.v1'
EVALUATOR_VERSION = 'goal-delivery.v1.1'
TERMINAL = {'completed', 'completed-with-gaps', 'requires-human-review', 'blocked', 'failed', 'cancelled'}
LABELS = {'met': '目标已达成', 'partially-met': '目标部分达成', 'unmet': '目标未达成',
          'not-evaluable': '目标暂不可评估', 'pending': '目标待验收', 'unrecorded': '目标未记录'}
ROLES = {
    'ontology-cognitive-analyst': ('综合公开本体与文献研究的条件性结论', '协调专业分支，交付可追溯研究图谱，不生成行动指令'),
    'situation-assessment-action': ('分析文献观察的状态、时间与变化', '区分观察、推断与未知，保持来源和对象配置'),
    'rule-applicability-action': ('审查文献规则的版本与语义适用条件', '缺条件保持未知，不签发实际行动授权'),
    'tactics-research-action': ('比较文献研究的方法与解释假设', '保留适用条件、反证和不可比较项，不优化军事行动'),
    'ontology-consistency-action': ('检查候选关系与研究视图的一致性', '确定性检查与模型解释分开，不能以一致性证明事实真实'),
    'future-battlefield-analyst': ('形成可追溯的多视角公开研究', '区分证据、条件性判断、反证与未知；交付可审阅报告与本体关系'),
    'future-battlefield-codex': ('组织研究、分解问题并协调收敛', '给受托 Agent 分配明确职责，汇总返回依据、冲突与未解决问题'),
    'evidence-collection-action': ('获取相关公开来源并保存快照', '保留来源、检索过程与正文快照；无法获取时说明缺口'),
    'evidence-extraction-action': ('从来源提取可定位的主张与原文', '主张绑定原文和位置，不把摘录提升为已核实事实'),
    'trend-analysis-action': ('比较变化、冲突与可比性', '保留时间、口径和对象差异，不将相关性升级为因果'),
    'scenario-analysis-action': ('形成有条件、有依据的情景判断', '记录关系的前提、证据、适用范围及未知'),
    'red-team-action': ('寻找具体反证及替代解释', '质疑具体断言，保留无法消除的冲突，不为反对而编造证据'),
    'uav-capability-analyst': ('评估固定对象与配置的能力证据', '固定对象、配置、要求和量纲，逐项列出支持与缺口'),
    'uav-design-engineer': ('审查工程资料、需求与验证的一致性', '交付资料审查与验证缺口，不把未验证方案表述为可发布设计'),
    'uav-evidence-reviewer': ('独立复核证据、计算与追溯', '标明复核对象、方法和证据；来源或方法相关性保持可见'),
    'research-reflection-agent': ('从已记录产物识别研究质量问题', '问题必须指向实际输出或调用记录，不能改写原始结果'),
    'research-candidate-designer-agent': ('提出范围受限的研究改进候选', '建议限于方法、指令或分工，保持目标、权限和评测标准不变'),
    'research-report-quality-agent': ('按固定标准比较基线与候选', '使用冻结的同一评测用例，保留差异和失败项，不自改标准'),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _id(prefix):
    return prefix + '-' + secrets.token_hex(12)


def installed(c):
    c.execute("SELECT to_regclass(current_schema() || '.agent_run_goal') AS name")
    row = c.fetchone()
    return bool(row and row['name'])


def default_contract(agent_key):
    objective, scope = ROLES[agent_key]
    def rule(key, category, trigger, check, failure, proof):
        return {'id': key, 'category': category, 'appliesTo': 'agent-actions', 'trigger': trigger,
                'check': check, 'failureAction': failure, 'proofRequired': proof}
    return {'schemaVersion': 'agent-goal.v1', 'objective': objective, 'scope': scope,
        'exclusions': ['越权访问、扩大预算或工具权限', '把未核验信息标记为事实或自行发布', '自行修改目标与评估标准'],
        'deliverables': ['已保存的结构化产物', '与判断绑定的来源依据，或明确的证据缺口'],
        'acceptanceCriteria': ['delivery-artifact', 'traceable-evidence-or-gap', 'delegation-return', 'substantive-coverage'],
        'dependencies': ['输入问题与范围', '获准的数据源及上游产物'],
        'constraintRefs': ['TaskIntent', 'Harness', 'shared-root-budget', 'publication-authorization'],
        'stopConditions': ['任务撤销或到期', '权限或共享预算不足', '连续研究无新增依据', '达到冻结执行基线的轮次或时间上限'],
        'evaluation': {'evaluator': EVALUATOR, 'scope': 'delivery-and-traceability', 'semanticCoverage': 'independent-review-required',
                       'proofRequired': ['goal-hash', 'output-artifact-hash', 'recorded-checks']},
        'behaviorRules': [
            rule('authority-boundary', 'hard', '工具、模型、委派或写入前', 'TaskIntent / Harness / 共享预算检查', 'deny-dispatch', '授权及调用记录'),
            rule('immutable-provenance', 'hard', '保存或引用产物时', '保留产物所有者及内容哈希', 'reject-write', '不可变产物记录'),
            rule('publication-boundary', 'hard', '发布或修改权限时', '独立的人类授权', 'deny-publication', '授权发布记录'),
            rule('exact-citation', 'quality', '形成可核查主张时', '引文精确定位到已保存正文', 'retain-gap-or-reject-claim', '来源产物及定位'),
            rule('conditions-and-conflicts', 'quality', '归纳判断时', '保留前提、反证、冲突和未知', 'retain-gap-or-reject-claim', '断言与反证记录'),
            rule('targeted-research', 'strategy', '补充研究时', '针对缺口检索并避免重复调用', 'replan-within-budget', '检索及无进展记录'),
        ]}


def validate_contract(agent_key, value):
    if agent_key not in ROLES or not isinstance(value, dict):
        raise ValueError('unsupported Agent goal contract')
    base = default_contract(agent_key)
    if set(value) != set(base):
        raise ValueError('invalid goal contract fields')
    # Evaluation/authority/rule semantics are code-versioned, never candidate-editable.
    for key in ('schemaVersion', 'evaluation', 'acceptanceCriteria', 'constraintRefs', 'behaviorRules', 'stopConditions'):
        if value[key] != base[key]:
            raise ValueError(key + ' is fixed by the reviewed evaluator version')
    for key in ('objective', 'scope'):
        if not isinstance(value[key], str) or not 1 <= len(value[key].strip()) <= 2000:
            raise ValueError('invalid ' + key)
    for key in ('exclusions', 'deliverables', 'dependencies'):
        if (not isinstance(value[key], list) or not 1 <= len(value[key]) <= 30
                or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 500 for v in value[key])):
            raise ValueError('invalid ' + key)
    if not set(base['exclusions']).issubset(value['exclusions']):
        raise ValueError('required exclusions cannot be removed')
    if not set(base['deliverables']).issubset(value['deliverables']):
        raise ValueError('required deliverables cannot be removed')
    if len(json.dumps(value, allow_nan=False).encode()) > 64000:
        raise ValueError('goal contract exceeds size limit')
    from agent_admin.config_publication import SECRET
    from agent_admin.repository import reject_secrets
    reject_secrets(value)
    if SECRET.search(json.dumps(value, ensure_ascii=False)):
        raise ValueError('raw credentials are not accepted in goal contracts')
    return deepcopy(value)


def current(c, agent_key):
    if not installed(c):
        return {}
    c.execute('SELECT * FROM agent_goal_contract_revision WHERE agent_key=%s ORDER BY revision DESC LIMIT 1', (agent_key,))
    row = c.fetchone()
    if not row:
        return {}
    if digest(row['document_json']) != row['content_hash']:
        raise ValueError('goal contract hash mismatch')
    return {'contractId': row['contract_id'], 'agentKey': agent_key, 'revision': row['revision'],
            'contentHash': row['content_hash'], 'document': row['document_json'], 'createdAt': row['created_at'].isoformat()}


def seed(c):
    """Reviewed deployment seed; never changes an existing contract revision."""
    for key in ROLES:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('goal-contract:' + key,))
        if current(c, key):
            continue
        c.execute('SELECT 1 FROM agent_definition WHERE agent_key=%s', (key,))
        if not c.fetchone():
            continue
        document = default_contract(key)
        c.execute('''INSERT INTO agent_goal_contract_revision(contract_id,agent_key,revision,document_json,
            content_hash,created_by,reason,idempotency_key,request_hash) VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s)''',
            (_id('goal-contract'), key, Json(document), digest(document), 'deployment:083',
             'Reviewed initial role goals and behavior rules', 'deployment-083', digest(document)))


def bind(c, run_id, profile, data, contract):
    if not installed(c):
        return
    if not contract:
        raise ValueError('published goal contract required for new Runs')
    from .task_intent import read
    intent = read(c, run_id)
    task = intent.get('document') or {}
    parent_id = intent.get('parentRunId')
    parent = None
    if parent_id:
        c.execute('SELECT * FROM agent_run_goal WHERE run_id=%s', (parent_id,))
        parent = c.fetchone()
        # Legacy parent cannot acquire retroactive goals through a new child.
    role = contract['document']
    step_key = str(data.get('parentStepKey') or data.get('nodeKey') or 'delegated-task')[:200] if parent_id else None
    objective = task.get('objective') or data.get('question') or data.get('taskGoal') or profile.display_name
    document = {'schemaVersion': 'run-goal.v1', 'goalId': _id('goal'), 'runId': run_id,
        'agentKey': profile.agent_key, 'roleObjective': role['objective'], 'objective': objective,
        'scope': deepcopy(task.get('scope') or {}), 'roleScope': role['scope'],
        'localGoal': (role['objective'] + '；负责父任务步骤：' + step_key) if parent_id else None,
        'parentGoalId': parent['goal_id'] if parent else None, 'parentRunId': parent_id,
        'contributionStepKey': step_key, 'contractId': contract['contractId'], 'contractHash': contract['contentHash'],
        'contractRevision': contract['revision'], 'deliverables': role['deliverables'],
        'acceptanceCriteria': role['acceptanceCriteria'], 'dependencies': role['dependencies'],
        'constraintRefs': {'taskIntentId': intent.get('intentId'), 'taskIntentHash': intent.get('contentHash'),
            'harnessContractId': profile.harness_contract.get('harnessContractId'),
            'harnessContractHash': profile.harness_contract.get('contractHash')},
        'evaluation': role['evaluation'], 'behaviorRules': role['behaviorRules'],
        'exclusions': role['exclusions'], 'stopConditions': role['stopConditions']}
    c.execute('''INSERT INTO agent_run_goal(run_id,goal_id,parent_goal_id,contract_id,document_json,content_hash)
        VALUES(%s,%s,%s,%s,%s,%s)''', (run_id, document['goalId'], document['parentGoalId'], contract['contractId'], Json(document), digest(document)))


class _CursorEvidence:
    """Exact locator verification on the same transaction as terminal output."""
    def __init__(self, c):
        self.c = c

    def get_artifact(self, run_id, identity):
        self.c.execute('SELECT * FROM agent_run_artifact WHERE run_id=%s AND artifact_id=%s', (run_id, identity))
        row = self.c.fetchone()
        return dict(row) if row else None

    def get_source_snapshot(self, run_id, identity):
        from .evidence_repository import EvidenceRepository
        return EvidenceRepository._source(self, self.c, run_id, identity)

    def _artifact(self, cursor, run_id, identity):
        row = self.get_artifact(run_id, identity)
        if not row:
            raise ValueError('artifact absent from current Run')
        return row


def evaluate(goal, output, status, *, artifact=None, evidence_checks=None, children=None):
    """Pure deterministic assessment; supplied proofs have already been resolved."""
    output = output if isinstance(output, dict) else {}
    evidence_checks = evidence_checks or []
    children = children or []
    gaps = output.get('evidenceGaps') or output.get('gaps') or output.get('unresolvedQuestions') or []
    if not gaps:
        gaps = output.get('capabilityGaps') or []
    child_states = [(child.get('assessment') or {}).get('deliveryStatus') for child in children]
    returned = 'fail' if any(s == 'unmet' for s in child_states) else ('pass' if all(s == 'met' for s in child_states) else 'unknown')
    exact = bool(evidence_checks) and all(p.get('status') == 'pass' for p in evidence_checks)
    trace = 'fail' if any(p.get('status') == 'fail' for p in evidence_checks) else 'pass' if exact or (gaps and not evidence_checks) else 'unknown'
    has_output = bool(output) and bool(artifact)
    checks = [
        {'id': 'delivery-artifact', 'label': '已保存交付产物', 'status': 'pass' if has_output else 'fail', 'proof': artifact},
        {'id': 'traceable-evidence-or-gap', 'label': '引文可定位或明确保留缺口',
         'status': trace,
         'proof': evidence_checks, 'gapCount': len(gaps) if isinstance(gaps, list) else 1 if gaps else 0},
        {'id': 'delegation-return', 'label': '受托交付已验收', 'status': returned,
         'proof': [{'runId': r['runId'], 'goalId': r['goalId'], 'status': r.get('status'),
                    'assessmentId': (r.get('assessment') or {}).get('assessmentId')} for r in children]},
        {'id': 'substantive-coverage', 'label': '问题覆盖与专业正确性', 'status': 'unknown',
         'reason': '结构化验收不能证明问题覆盖与专业正确性，独立评估尚未记录。'},
    ]
    delivery = 'unmet' if any(check['status'] == 'fail' for check in checks[:3]) else 'met' if all(check['status'] == 'pass' for check in checks[:3]) else 'not-evaluable'
    result = 'partially-met' if has_output else 'unmet'
    if goal.get('evaluation', {}).get('evaluator') != EVALUATOR:
        result, delivery = 'not-evaluable', 'not-evaluable'
    return {'schemaVersion': 'goal-assessment.v1', 'goalId': goal['goalId'],
        'status': result, 'label': LABELS[result], 'deliveryStatus': delivery, 'executionStatus': status,
        'evaluatorVersion': EVALUATOR_VERSION, 'checks': checks, 'truthVerified': False,
        'limitations': ['仅验收已记录的基础交付与追溯；新增交付要求、问题覆盖、来源独立性和专业正确性尚需独立评估。']}


def evaluator_build_hash():
    import inspect
    from .ontology_explorer import source_excerpt
    return digest([inspect.getsource(item) for item in (evaluate, capture_terminal, resolve_evidence, _CursorEvidence, source_excerpt)])


def resolve_evidence(c, report, identity, evidence):
    """Allow immediate delegated input only after exact ownership/hash binding.

    This does not import an arbitrary ancestor artifact or turn an upstream
    quote into a child-owned observation. The proof retains the source owner.
    """
    from .ontology_explorer import source_excerpt
    repo = _CursorEvidence(c)
    try:
        source = source_excerpt(report, identity, repo)
        return {**source, 'sourceOwnerRunId':report['runId'], 'proofScope':'run-owned-source'}
    except (KeyError, ValueError):
        pass
    c.execute("SELECT to_regclass(current_schema() || '.agent_action_invocation') AS name")
    if not c.fetchone()['name']:
        raise ValueError('no recorded delegation for source')
    c.execute('''SELECT a.run_id,a.input_artifact_id,a.input_artifact_hash,b.content_json,b.content_hash,b.run_id AS owner
        FROM agent_action_invocation a JOIN agent_run_artifact b ON b.artifact_id=a.input_artifact_id
        WHERE a.child_run_id=%s''', (report['runId'],))
    delegation = c.fetchone()
    if (not delegation or delegation['owner'] != delegation['run_id']
            or delegation['content_hash'] != delegation['input_artifact_hash']
            or digest(delegation['content_json']) != delegation['content_hash']):
        raise ValueError('delegation input hash/ownership mismatch')
    parent_id = delegation['run_id']
    def supplied(value):
        if isinstance(value, dict):
            if (str(value.get('claimId') or value.get('evidenceId') or '') == str(evidence.get('claimId') or identity)
                    and value.get('sourceArtifactId') == evidence.get('sourceArtifactId')
                    and value.get('sourceArtifactHash') == evidence.get('sourceArtifactHash')
                    and value.get('contentHash') == evidence.get('contentHash')
                    and value.get('quote') == evidence.get('quote') and value.get('quote')):
                return True
            return any(supplied(v) for v in value.values())
        return any(supplied(v) for v in value) if isinstance(value, list) else False
    if not supplied(delegation['content_json']):
        raise ValueError('evidence was not supplied in the frozen delegation input')
    source = source_excerpt({'runId':parent_id, 'evidence':[{**evidence,'evidenceId':identity}]}, identity, repo)
    return {**source, 'sourceOwnerRunId':parent_id, 'proofScope':'frozen-delegated-source',
            'delegationInputArtifactId':delegation['input_artifact_id'],
            'delegationInputArtifactHash':delegation['input_artifact_hash']}


def capture_terminal(c, run_id, status, output, *, requested_by=None, reason=None):
    if status not in TERMINAL or not installed(c):
        return
    c.execute('SELECT * FROM agent_run_goal WHERE run_id=%s', (run_id,))
    row = c.fetchone()
    if not row:
        return
    if digest(row['document_json']) != row['content_hash']:
        raise ValueError('run goal hash mismatch')
    if output is None:
        c.execute('SELECT output_json FROM agent_run WHERE run_id=%s', (run_id,))
        output = c.fetchone()['output_json'] or {}
    output_hash = digest(output)
    c.execute('''SELECT * FROM agent_run_artifact WHERE run_id=%s AND artifact_type='agent-output' AND content_hash=%s''', (run_id, output_hash))
    saved = c.fetchone()
    proof = {'artifactId': saved['artifact_id'], 'runId': run_id, 'contentHash': output_hash} if saved and digest(saved['content_json']) == output_hash else None
    from .ontology_explorer import evidence_index
    report = {**(output if isinstance(output, dict) else {}), 'runId': run_id}
    evidence_checks = []
    for identity, evidence in evidence_index(report).items():
        if not evidence.get('quote') and not evidence.get('sourceArtifactId'):
            evidence_checks.append({'evidenceId': identity, 'status': 'unknown',
                'reason': '此项是工具核查引用，未携带可单独验证的原文定位。'})
            continue
        try:
            source = resolve_evidence(c, report, identity, evidence)
            evidence_checks.append({'evidenceId': identity, 'status': 'pass', 'artifactId': source['sourceArtifactId'],
                'contentHash': source['sourceArtifactHash'], 'locatorStatus': source['locatorStatus'],
                **{key:source[key] for key in ('sourceOwnerRunId','proofScope','delegationInputArtifactId','delegationInputArtifactHash') if key in source}})
        except (KeyError, ValueError, TypeError):
            evidence_checks.append({'evidenceId': identity, 'status': 'fail', 'reason': '保存的引文、所属运行、哈希或正文位置不匹配。'})
    if report.get('schemaVersion') == 'ontology-cognitive.v1':
        # Native research products use typed, authorized ArtifactRef records.
        # This only verifies source fidelity; it never scores semantic truth.
        from .cognitive_contracts import verify_evidence
        c.execute('SELECT state_json FROM agent_run_state WHERE run_id=%s', (run_id,))
        stored = c.fetchone()
        authority = ((stored or {}).get('state_json') or {}).get('_cognitiveAuthority', {}).get('artifactRefs', [])
        checked = set()
        for claim in output.get('claims', []):
            for evidence in claim.get('evidence', []):
                if evidence['evidenceId'] in checked:
                    continue
                checked.add(evidence['evidenceId'])
                try:
                    verify_evidence(evidence, authority, _CursorEvidence(c))
                    evidence_checks.append({'evidenceId': evidence['evidenceId'], 'status': 'pass',
                        'artifactId': evidence['sourceArtifactRef']['artifactId'],
                        'contentHash': evidence['sourceArtifactRef']['contentHash'], 'locatorStatus': 'exact-quote',
                        'sourceOwnerRunId': evidence['sourceArtifactRef']['runId'], 'proofScope': 'authorized-source-fidelity'})
                except (ValueError, KeyError, TypeError, PermissionError):
                    evidence_checks.append({'evidenceId': evidence['evidenceId'], 'status': 'fail',
                        'reason': '来源授权、指纹或精确引文不匹配。'})
    children = _children(c, row['goal_id'])
    document = evaluate(row['document_json'], output, status, artifact=proof, evidence_checks=evidence_checks, children=children)
    document.update(assessmentId=_id('goal-assessment'), goalHash=row['content_hash'], outputHash=output_hash)
    document.update(evaluatorBuildHash=evaluator_build_hash(), requestedBy=requested_by or 'terminal-worker', reason=reason)
    c.execute('''INSERT INTO agent_goal_assessment(assessment_id,run_id,goal_id,goal_hash,output_hash,execution_status,
        evaluator_version,status,document_json,content_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (document['assessmentId'], run_id, row['goal_id'], row['content_hash'], output_hash, status,
         EVALUATOR_VERSION, document['status'], Json(document), digest(document)))


def _assessment(c, run_id, status, output):
    c.execute('''SELECT document_json,content_hash FROM agent_goal_assessment WHERE run_id=%s
        AND execution_status=%s AND output_hash=%s ORDER BY created_at DESC,assessment_id DESC LIMIT 1''', (run_id, status, digest(output or {})))
    row = c.fetchone()
    if row and digest(row['document_json']) != row['content_hash']:
        raise ValueError('goal assessment hash mismatch')
    return row['document_json'] if row else None


def _children(c, goal_id):
    c.execute('''SELECT g.*,r.status,r.output_json FROM agent_run_goal g JOIN agent_run r USING(run_id)
        WHERE g.parent_goal_id=%s ORDER BY g.created_at,g.run_id''', (goal_id,))
    rows = list(c.fetchall())
    return [{'runId': r['run_id'], 'goalId': r['goal_id'], 'localGoal': r['document_json'].get('localGoal'),
             'agentKey': r['document_json']['agentKey'], 'status': r['status'],
             'assessment': _assessment(c, r['run_id'], r['status'], r['output_json'])} for r in rows]


def run_view(c, run_id):
    empty = {'runId': run_id, 'status': 'unrecorded', 'label': LABELS['unrecorded'], 'goal': None, 'assessment': None, 'children': []}
    if not installed(c):
        return empty
    c.execute('''SELECT g.*,r.status,r.output_json FROM agent_run_goal g JOIN agent_run r USING(run_id) WHERE run_id=%s''', (run_id,))
    row = c.fetchone()
    if not row:
        return empty
    goal = row['document_json']
    if digest(goal) != row['content_hash']:
        raise ValueError('run goal hash mismatch')
    assessment = _assessment(c, run_id, row['status'], row['output_json'])
    status = assessment['status'] if assessment else ('not-evaluable' if row['status'] in TERMINAL else 'pending')
    children = _children(c, row['goal_id'])
    return {'runId': run_id, 'status': status, 'label': LABELS[status], 'goal': goal, 'goalHash': row['content_hash'],
        'assessment': assessment, 'children': children,
        'relations': [{'subject': goal['agentKey'], 'predicate': 'pursues', 'object': row['goal_id'], 'runId':run_id}] +
            ([{'subject': row['goal_id'], 'predicate': 'contributes-to', 'object': row['parent_goal_id']}] if row['parent_goal_id'] else []) +
            [{'subject': r['goalId'], 'predicate': 'contributes-to', 'object':row['goal_id']} for r in children] +
            ([{'subject': assessment['assessmentId'], 'predicate': 'evaluates', 'object': row['goal_id']}] if assessment else []) +
            ([{'subject': assessment['checks'][0]['proof']['artifactId'], 'predicate':'responds-to', 'object':row['goal_id']}]
             if assessment and (assessment['checks'][0].get('proof') or {}).get('artifactId') else [])}


class GoalService:
    def __init__(self, factory):
        self.factory = factory

    def contract(self, agent_key):
        with self.factory() as con:
            with con.cursor() as c:
                return current(c, agent_key)

    def run(self, run_id):
        with self.factory() as con:
            with con.cursor() as c:
                return run_view(c, run_id)

    def context(self, run_id):
        """Only frozen instructions, without reading descendants or report bodies."""
        with self.factory() as con:
            with con.cursor() as c:
                if not installed(c):
                    return {}
                c.execute('SELECT document_json,content_hash FROM agent_run_goal WHERE run_id=%s', (run_id,))
                row = c.fetchone()
                if not row:
                    return {}
                if digest(row['document_json']) != row['content_hash']:
                    raise ValueError('run goal hash mismatch')
                return row['document_json']

    def reassess(self, run_id, body, actor):
        """Append local checks of an exact saved output; never rerun research."""
        from .reasoning_repository import _identity
        from .worker import control_context, fenced_connection_factory
        if (not isinstance(body, dict) or set(body) != {'expectedGoalHash','expectedOutputHash','reason'}
                or not isinstance(body['reason'], str) or not 1 <= len(body['reason'].strip()) <= 2000):
            raise ValueError('exact goal/output hashes and reassessment reason required')
        with control_context(run_id):
            with fenced_connection_factory(self.factory)() as con:
                with con.cursor() as c:
                    _identity(c, actor, 'operator')
                    c.execute('SELECT status,output_json FROM agent_run WHERE run_id=%s FOR UPDATE', (run_id,))
                    run = c.fetchone()
                    if not run or run['status'] not in TERMINAL:
                        raise ValueError('only saved terminal Runs can be reassessed')
                    view = run_view(c, run_id)
                    if not view.get('goal'):
                        raise ValueError('historical Run has no frozen goal; no backfill allowed')
                    if body['expectedGoalHash'] != view['goalHash'] or body['expectedOutputHash'] != digest(run['output_json'] or {}):
                        raise ValueError('goal or output changed; reload before reassessing')
                    assessment = view.get('assessment') or {}
                    recorded_children = next((check.get('proof', []) for check in assessment.get('checks', [])
                                              if check.get('id') == 'delegation-return'), [])
                    current_children = [{'runId': child['runId'], 'goalId': child['goalId'], 'status': child['status'],
                                         'assessmentId': (child.get('assessment') or {}).get('assessmentId')}
                                        for child in view['children']]
                    if (assessment.get('evaluatorBuildHash') == evaluator_build_hash()
                            and recorded_children == current_children):
                        return view
                    capture_terminal(c, run_id, run['status'], run['output_json'],
                                     requested_by=actor.principal_id, reason=body['reason'].strip())
                    return run_view(c, run_id)

    def publish(self, agent_key, body, actor):
        from .reasoning_repository import _identity
        if not isinstance(body, dict) or set(body) != {'document', 'expectedHash', 'reason', 'idempotencyKey'}:
            raise ValueError('invalid goal publication fields')
        document = validate_contract(agent_key, body['document'])
        reason, key = body['reason'], body['idempotencyKey']
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
            raise ValueError('publication reason required')
        if not isinstance(key, str) or not 8 <= len(key) <= 128:
            raise ValueError('invalid idempotency key')
        request_hash = digest(body)
        with self.factory() as con:
            with con.cursor() as c:
                _identity(c, actor, 'admin')
                c.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent-admin-profile-config',0))")
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('goal-contract:' + agent_key,))
                c.execute('SELECT request_hash,contract_id FROM agent_goal_contract_revision WHERE agent_key=%s AND idempotency_key=%s', (agent_key, key))
                prior = c.fetchone()
                if prior:
                    if prior['request_hash'] != request_hash:
                        raise ValueError('idempotency key already used for a different publication')
                    return {'contractId': prior['contract_id'], 'replayed': True}
                live = current(c, agent_key)
                if body['expectedHash'] != live.get('contentHash'):
                    raise ValueError('goal contract changed; reload before publishing')
                identity = _id('goal-contract')
                c.execute('''INSERT INTO agent_goal_contract_revision(contract_id,agent_key,revision,document_json,content_hash,
                    created_by,reason,idempotency_key,request_hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (identity, agent_key, live.get('revision', 0) + 1, Json(document), digest(document), actor.principal_id, reason.strip(), key, request_hash))
                return current(c, agent_key)
