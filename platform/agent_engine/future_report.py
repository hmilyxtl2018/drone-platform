"""Versioned, evidence-bound ontology for a research report (not execution)."""
from copy import deepcopy

ENTITY_TYPES = ("system", "capability", "technology", "constraint", "context", "outcome")
PREDICATES = {
    "depends-on": "依赖", "constrains": "约束", "enables": "支持形成",
    "changes": "影响", "part-of": "组成", "associated-with": "相关于",
}
REASONS = {
    "web-search-no-results": "检索未获得合规来源",
    "web-search-excluded-results": "检索结果未通过来源范围校验",
    "web-scrape-no-content": "来源未取得可读正文",
    "web-acquisition-failed": "来源采集失败",
    "acquisition-budget-exhausted": "采集预算已用尽",
    "perspective-acquisition-not-funded": "本视角未获得采集预算",
    "query-variants-exhausted": "已完成本视角的不同检索策略",
    "evidence-support-rejected": "引文不足以支撑该判断",
    "evidence-support-review-unavailable": "引文相关性复核未完成",
    "no-relevant-evidence": "尚无直接相关的可定位引文",
    "perspective-model-failed": "分析模型调用或输出结构校验失败",
}


def evidence_gap(perspective, searches, reason="no-relevant-evidence", review=None):
    attempts = [deepcopy(row) for row in searches if row.get("perspectiveKey") == perspective["key"]]
    latest = attempts[-1] if attempts else {}
    cause = reason if review or reason != 'no-relevant-evidence' else latest.get("reason") or reason
    return {
        "perspectiveKey": perspective["key"],
        "perspectiveName": perspective.get("name", perspective["key"]),
        "question": perspective.get("question", ""), "reason": cause,
        "summary": REASONS.get(cause, REASONS["no-relevant-evidence"]),
        "attemptedQueries": list(dict.fromkeys(row["query"] for row in attempts if row.get("query"))),
        "attempts": attempts, "supportReview": deepcopy(review),
        "attemptedUrls": list(dict.fromkeys(url for row in attempts for url in row.get("attemptedUrls", []))),
        "neededEvidence": "补充直接回答该视角问题、含时间与适用条件的公开原文，并复核引文与判断的对应关系。",
        "nextAction": "在来源范围和剩余预算内改写检索或替换来源；预算结束后保留缺口。",
    }


def build_ontology(task, perspectives, findings, evidence, gaps, verification_tasks=()):
    """Reify judgments/relationships so provenance and modality belong to assertions.

    Administrative edges describe report structure. Domain edges are admitted
    only after a separate model support review; neither edge implies truth.
    """
    nodes, edges, assertions = [], [], []
    seen = set()

    def node(identity, kind, label, **data):
        if identity not in seen:
            nodes.append({"id": identity, "type": kind, "label": label, **data})
            seen.add(identity)
        return identity

    def edge(subject, predicate, obj):
        edges.append({"subject": subject, "predicate": predicate, "object": obj})

    node("research", "research-question", task.get("question", "研判问题"))
    spec = task.get('decisionSpec')
    if spec:
        node('decision', 'decision-purpose', spec['decision']['decisionToSupport'],
             decisionOwner=spec['decision']['decisionOwner'], criteria=deepcopy(spec['decision']['criteria']),
             alternatives=deepcopy(spec['decision']['alternatives']), fieldOrigins=deepcopy(spec['fieldOrigins']))
        edge('research', 'supports-decision', 'decision')
        for q in spec['questions']:
            node(q['id'], 'decision-question', q['text'], perspectiveKey=q['perspectiveKey'])
            edge('research', 'asks', q['id'])
    by_evidence = {}
    for item in evidence:
        identity = str(item.get("evidenceId") or item.get("claimId"))
        by_evidence[identity] = item
        eid = node("evidence:" + identity, "evidence", item.get("quote", ""), evidenceId=identity,
                   provenance={field: deepcopy(item.get(field)) for field in
                               ("sourceArtifactId", "sourceArtifactHash", "contentHash", "locator", "publishedAt", "retrievedAt")})
        uri = item.get("sourceUri", "")
        source = node("source:" + uri, "source", uri, independenceStatus="unknown")
        edge(eid, "quoted-from", source)
    for perspective in perspectives:
        key = perspective["key"]
        pid = node("perspective:" + key, "perspective", perspective.get("name", key))
        edge("research", "examined-through", pid)
        candidates = [row for row in findings if row.get('perspectiveKey') == key and row.get('status') == 'candidate']
        if not candidates:
            gap = next((row for row in gaps if row.get("perspectiveKey") == key), {})
            gid = node("gap:" + key, "evidence-gap", gap.get("summary", "证据尚未覆盖"))
            edge(pid, "has-gap", gid)
            continue
        for finding in candidates:
            _add_finding(task, finding, key, pid, by_evidence, node, edge, assertions)
        gap = next((row for row in gaps if row.get('perspectiveKey') == key), None)
        if gap:
            gid = node('gap:' + key, 'evidence-gap', gap.get('summary', '研究问题尚未获得直接回答'), perspectiveKey=key)
            edge(pid, 'has-gap', gid)
    for work in verification_tasks:
        target = node(work['id'], 'verification-task', work['target'], task=deepcopy(work),
                      perspectiveKey=work['perspectiveKey'], evidenceIds=list(work['evidenceIds']))
        edge(work['questionId'], 'needs-verification', target)
        for finding_id in work['findingIds']:
            edge('judgment:' + finding_id, 'requires-verification', target)
        for identity in work['evidenceIds']:
            edge('evidence:' + identity, 'informs-verification', target)
    return {"schemaVersion": "1.0", "nodes": nodes, "edges": edges, "assertions": assertions,
            "entityTypes": list(ENTITY_TYPES), "predicates": deepcopy(PREDICATES),
            **({'decisionSemanticsVersion': spec['schemaVersion'], 'decisionSpecHash': spec['contentHash']} if spec else {}),
            "truthStatus": "unverified", "method": "model-proposed-and-separately-support-reviewed"}


def _add_finding(task, finding, key, pid, by_evidence, node, edge, assertions):
        namespace = finding.get('findingId') or key
        jid = node("judgment:" + namespace, "conditional-judgment", finding["statement"],
                   **({'findingId': finding['findingId'], 'perspectiveKey': key,
                       'epistemicKind': finding.get('epistemicKind'), 'modality': finding.get('modality')} if finding.get('findingId') else {}),
                   truthStatus="unverified", supportReview=deepcopy(finding.get("supportReview")))
        edge(pid, "has-judgment", jid)
        for q in finding.get('answeredQuestionIds', []):
            edge(q, 'answered-by', jid)
        for identity in finding.get("supportingClaims", []):
            if identity in by_evidence:
                edge("evidence:" + identity, "supports-judgment", jid)
        for field, relation, kind in (("conditions", "applies-when", "condition"),
                                      ("counterArguments", "challenged-by", "counter-hypothesis"),
                                      ("unknowns", "has-unknown", "unknown")):
            for index, text in enumerate(finding.get(field, [])):
                target = node(f"{kind}:{namespace}:{index}", kind, text, truthStatus="unverified")
                edge(jid, relation, target)
        entities = {row["id"]: row for row in finding.get("entities", [])}
        for row in finding.get("relations", []):
            if row.get("supportReview", {}).get("verdict") != "supported":
                continue
            if row.get("subject") not in entities or row.get("object") not in entities:
                continue
            ids = row.get("evidenceIds", [])
            if not ids or any(identity not in by_evidence for identity in ids):
                continue
            subject, obj = entities[row["subject"]], entities[row["object"]]
            qualifiers = ('aliases', 'variant', 'configuration', 'observedAt', 'validFrom', 'validTo', 'canonicalBinding')
            source_id = node(f"entity:{namespace}:{subject['id']}", subject["type"], subject["label"],
                             **{k: deepcopy(subject[k]) for k in qualifiers if k in subject})
            target_id = node(f"entity:{namespace}:{obj['id']}", obj["type"], obj["label"],
                             **{k: deepcopy(obj[k]) for k in qualifiers if k in obj})
            assertion_id = "assertion:" + namespace + ":" + row["id"]
            assertion = {**deepcopy(row), "id": assertion_id, "subject": source_id,
                         "object": target_id, "perspectiveKey": key, "judgmentId": jid,
                         **({'findingId': finding['findingId']} if finding.get('findingId') else {}),
                         "scope": deepcopy(task.get("scope", {})), "truthStatus": "unverified"}
            assertions.append(assertion)
            node(assertion_id, "relation-assertion", PREDICATES[row["predicate"]], truthStatus="unverified")
            edge(jid, "expressed-as", assertion_id)
            edge(assertion_id, "has-subject", source_id)
            edge(assertion_id, "has-object", target_id)
            for identity in ids:
                edge("evidence:" + identity, "supports-relation", assertion_id)


def add_method_hypotheses(graph, spec, material):
    """Project reviewed compatibility assessments, never promote hypotheses to facts."""
    if material.get('status') != 'reviewed-candidates':
        return
    nodes, edges = graph['nodes'], graph['edges']
    known = {n['id'] for n in nodes}
    questions = {q['id'] for q in spec['questions']}
    for hypothesis in material.get('hypotheses', []):
        refs = hypothesis.get('questionIds', [])
        if not refs or not set(refs) <= questions:
            continue
        identity = 'hypothesis:' + hypothesis['id']
        if identity in known:
            continue
        nodes.append({'id': identity, 'type': 'research-hypothesis', 'label': hypothesis['statement'],
                      'questionIds': refs, 'truthStatus': 'unverified', 'status': 'candidate-explanation'})
        known.add(identity)
        for q in refs:
            edges.append({'subject': q, 'predicate': 'considers-explanation', 'object': identity})
        for assessment in material.get('assessments', []):
            eid = 'evidence:' + assessment['evidenceId']
            if assessment.get('hypothesisId') != hypothesis['id'] or eid not in known or assessment.get('supportReview', {}).get('verdict') != 'supported':
                continue
            aid = 'hypothesis-assessment:' + assessment['id']
            nodes.append({'id': aid, 'type': 'hypothesis-assessment', 'label': assessment['rationale'],
                          'assessment': deepcopy(assessment), 'truthStatus': 'unverified'})
            edges.extend([{'subject': identity, 'predicate': 'compared-with-evidence', 'object': aid},
                          {'subject': eid, 'predicate': 'informs-comparison', 'object': aid}])
