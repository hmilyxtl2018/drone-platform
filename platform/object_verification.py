"""Link evidence checks to pending ontology targets without promoting trust.

All checks read the immutable assessment and graph snapshots. Exact quotations,
declared classes and predicate ranges are structural checks; their success is
neither independent verification nor confirmation of a proposed assertion.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

try:
    from evidence_graph import MAX_ATTRIBUTES, attribute_value_supported, event_date_supported
    from capability_analysis import normalize_capabilities, validate_capabilities
    from intelligence_store import (
        GRAPH_ENTITY_TYPES, GRAPH_EVENT_TYPES, GRAPH_RELATION_TYPES,
        ONTOLOGY_NS, IntelligenceStore, IntelligenceValidationError,
    )
except ModuleNotFoundError:  # pragma: no cover
    from .evidence_graph import MAX_ATTRIBUTES, attribute_value_supported, event_date_supported
    from .capability_analysis import normalize_capabilities, validate_capabilities
    from .intelligence_store import (
        GRAPH_ENTITY_TYPES, GRAPH_EVENT_TYPES, GRAPH_RELATION_TYPES,
        ONTOLOGY_NS, IntelligenceStore, IntelligenceValidationError,
    )


def _array(value: Any) -> list:
    return value if isinstance(value, list) else []


def _check(code: str, status: str, detail: str) -> dict[str, Any]:
    return {"code": code, "status": status, "detail": detail}


def _evidence(element: dict[str, Any], sources: list[dict[str, Any]]) -> tuple[list, dict]:
    try:
        refs = IntelligenceStore._graph_evidence(element, sources)
    except (IntelligenceValidationError, KeyError, TypeError):
        return [], _check("snapshot-quote-locator", "failed", "引用、原文定位或来源版本与报告快照不匹配。")
    return refs, _check("snapshot-quote-locator", "passed", "引用原文、字符/行范围、来源 URI、版本与哈希匹配不可变快照；尚未验证事实含义。")


def _property_field_context(name: Any, attribute: dict | None, value: Any, refs: list) -> tuple[str | None, bool]:
    """Check a declared field against the value's own literal statement.

    This only flags missing conservative cues. It neither reclassifies old
    drafts nor claims to resolve attribution, negation or factual meaning.
    """
    try:
        from object_attribute_extraction import ATTRIBUTE_TEMPLATES, _field_context_supported
        from ontology_attribute_admission import ATTRIBUTE_PREDICATES
    except ModuleNotFoundError:  # pragma: no cover
        from .object_attribute_extraction import ATTRIBUTE_TEMPLATES, _field_context_supported
        from .ontology_attribute_admission import ATTRIBUTE_PREDICATES
    known_keys = {key for fields in ATTRIBUTE_TEMPLATES.values() for key in fields}
    key = (attribute or {}).get('attributeKey')
    if not isinstance(key, str) or key not in known_keys:
        aliases = {'reportedStandard': 'interface-standard', 'reportedInterface': 'interface-standard',
                   'reportedHumanOversight': 'human-oversight', 'reportedTestStatus': 'public-test-status'}
        folded = str(name or '').casefold().strip()
        key = next((mapped for predicate, mapped in aliases.items()
                    if folded in {alias.casefold() for alias in ATTRIBUTE_PREDICATES[predicate]['aliases']}), None)
        key = key or {'humanoversight': 'human-oversight', 'teststatus': 'public-test-status',
                      'interface-standard': 'interface-standard', 'human-oversight': 'human-oversight',
                      'public-test-status': 'public-test-status'}.get(folded)
    if key not in {'human-oversight', 'interface-standard', 'public-test-status'}:
        return key, True
    if not refs or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return key, False
    return key, all(_field_context_supported(key, ref['quote'], value) for ref in refs)


def _property_evidence(node: dict, refs: list, property_refs: Any, sources: list,
                       value: Any, path: str, *, kind: str, name: Any,
                       qualifiers: dict | None = None) -> tuple[list, list]:
    """Check each property against its own snapshot locator, never node co-occurrence."""
    property_refs, locator = _evidence({"evidenceIds": node.get("evidenceIds", []),
                                       "payload": {"evidenceRefs": property_refs}}, sources)
    checks = [{**locator, "code": "attribute-quote-locator", "propertyPath": path}]
    supported = bool(property_refs) and all(attribute_value_supported(value, r["quote"]) for r in property_refs)
    if path == "payload.date":
        supported = bool(property_refs) and all(event_date_supported(value, r["quote"]) for r in property_refs)
    valid_name = isinstance(name, str) and 1 <= len(name.strip()) <= 120
    checks.append({**_check("attribute-value-quote", "passed" if supported and valid_name else "failed",
                            "属性值逐字出现在本项引用中；属性命名及含义仍需人工判断。" if supported and valid_name else "属性名称或值没有有效的逐字原文支持。"),
                   "propertyPath": path})
    field_key, field_supported = _property_field_context(name, qualifiers, value, property_refs)
    if field_key in {'human-oversight', 'interface-standard', 'public-test-status'}:
        checks.append({**_check('attribute-field-context', 'passed' if field_supported else 'failed',
            '属性原值所在陈述包含该字段的明确原文字面线索；类别含义仍待人工核验。' if field_supported
            else '属性原值所在陈述缺少所声明类别的明确字面线索；保留历史草稿，请修正分类或补证，不自动改值或重分类。'),
            'propertyPath': path, 'attributeKey': field_key})
    label = str(node.get("label") or "")
    def contextual(ref: dict) -> bool:
        if kind in GRAPH_ENTITY_TYPES:
            return bool(label) and label.casefold() in ref["quote"].casefold()
        return any(ref["itemId"] == base["itemId"] and type(ref.get("startChar")) is int
                   and type(base.get("startChar")) is int
                   and max(ref["startChar"], base["startChar"]) < min(ref["endChar"], base["endChar"])
                   for base in refs)
    context_ok = bool(property_refs) and all(contextual(r) for r in property_refs)
    checks.append({**_check("attribute-object-context", "passed" if context_ok else "failed",
                            "属性引用包含对象名称，或与同源事件引用重叠；归属语义仍待人工核验。" if context_ok else "属性引用缺少可核对的对象/事件上下文。"),
                   "propertyPath": path})
    for key, limit in (("unit", 60), ("conditions", 300), ("validTime", 200)):
        qualifier = (qualifiers or {}).get(key)
        if qualifier is None or (key != "validTime" and qualifier == ""):
            continue
        valid = isinstance(qualifier, str) and bool(qualifier.strip()) and len(qualifier) <= limit and bool(property_refs) and all(qualifier in ref["quote"] for ref in property_refs)
        detail = ("有效时间逐字出现在本项引文中；时间含义仍需人审，不用资料发布日期替代。" if valid else "有效时间没有合法的逐字属性引文支持，不能用资料发布日期补造。") if key == "validTime" else (
            "单位或限定条件逐字出现在本项引文中；不据此认定测量有效性。" if valid else "单位或限定条件不受本项引文支持。")
        checks.append({**_check("attribute-valid-time-quote" if key == "validTime" else "attribute-qualifier-quote", "passed" if valid else "failed", detail),
                       "propertyPath": path.rpartition(".")[0] + "." + key})
    return property_refs, checks


def _candidate_links(admissions: list[dict[str, Any]], revision_id: str, element_id: str,
                     metric_id: str | None = None, *, property_path: str | None = None) -> dict[str, Any]:
    matching = []
    for candidate in admissions:
        if not isinstance(candidate, dict):
            continue
        mapping = candidate.get("mapping") or {}
        if property_path is not None:
            if (mapping.get("kind") != "graph-property" or mapping.get("propertyPath") != property_path
                    or mapping.get("graphRevisionId") != revision_id or mapping.get("graphElementId") != element_id
                    or candidate.get("staleGraph") or candidate.get("supersededByAdmissionId")):
                continue
            matching.append(candidate)
            continue
        is_graph = mapping.get("kind") in {"graph-node", "graph-relation"}
        if is_graph:
            if (mapping.get("graphRevisionId") != revision_id
                    or mapping.get("graphElementId") != element_id
                    or candidate.get("staleGraph")):
                continue
        elif metric_id is None or candidate.get("metricId") != metric_id:
            continue
        matching.append(candidate)
    human_confirmed = [c for c in matching if c.get("status") in {"accepted", "published"}
                       and (c.get("review") or {}).get("verifiedConfidence") == "high"]
    return {"admissionIds": [c.get("admissionId") or c.get("id") for c in matching],
            "candidateIris": [c["candidateIri"] for c in matching if c.get("candidateIri")],
            "admissionStatuses": [c.get("status") for c in matching],
            "humanReviewRecorded": bool(human_confirmed),
            "humanVerifiedConfidence": "high" if human_confirmed else None}


def _target_check(code: str, kind: str, assessment: dict, revision_id: str,
                  element_id: str, label: str, refs: list, checks: list,
                  admissions: list, *, metric_id: str | None = None,
                  mapping_allowed: bool = True, **target_fields: Any) -> dict[str, Any]:
    machine_status = ("failed" if any(c["status"] == "failed" for c in checks)
                      else "needs-evidence" if any(c["status"] == "needs-evidence" for c in checks)
                      else "passed")
    linked = _candidate_links(admissions, revision_id, element_id, metric_id)
    checks = checks + [_check("semantic-human-review", "needs-evidence",
                             "机器只核对出处与结构。对象身份、属性/关系含义、限定条件及来源独立性仍需逐条人工核验。")]
    return {"code": code, "status": "failed" if machine_status == "failed" else "needs-evidence",
            "machineStatus": machine_status, "targetKind": kind,
            "assessmentId": assessment.get("assessmentId") or assessment.get("id"),
            "graphRevisionId": revision_id or None, "graphElementId": element_id,
            "label": label, "evidenceIds": list(dict.fromkeys(ref["itemId"] for ref in refs)),
            "evidenceRefs": refs, "checks": checks, "truthVerified": False,
            "independenceVerified": False,
            "eligibleForHumanReview": machine_status == "passed" and mapping_allowed,
            "detail": "出处和结构检查通过；断言含义待人工核验。" if machine_status == "passed"
                      else "存在出处、映射或结构缺口；请查看目标记录的逐项检查。",
            **linked, **target_fields}


def build_object_verification_checks(assessment: dict[str, Any], graph: dict[str, Any] | None,
                                     admissions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return per-node, per-edge and per-capability checks with stable target IDs."""
    sources = [source for source in _array(assessment.get("sourceSnapshot")) if isinstance(source, dict)]
    admissions = _array(admissions)
    graph = graph if isinstance(graph, dict) else {}
    aid = str(assessment.get("assessmentId") or assessment.get("id") or "")
    graph_aid = graph.get("assessmentId")
    if graph_aid and str(graph_aid) != aid:
        return [{"code": "ontology-coverage", "status": "failed", "targetKind": "coverage",
                 "assessmentId": aid, "truthVerified": False,
                 "detail": "图谱版本不属于此报告，未进行对象核验。"}]
    revision_id = str(graph.get("graphRevisionId") or graph.get("id") or "")
    nodes = {str(node.get("id")): node for node in _array(graph.get("nodes")) if isinstance(node, dict)}
    result = []
    for node in nodes.values():
        payload = node.get("payload") or {}
        if payload.get("extractionMethod") != "llm" and not payload.get("evidenceRefs"):
            continue
        refs, locator_check = _evidence(node, sources)
        identity_refs = list(refs)
        checks = [locator_check]
        ontology_class = None
        try:
            kind = IntelligenceStore._graph_node_type(node)
            ontology_class = ONTOLOGY_NS + kind
            checks.append(_check("ontology-class", "passed", "类别映射至已声明的本体实体或公开事件子类；事件性质仍需语义核验。"))
        except IntelligenceValidationError:
            kind = str(node.get("type") or "unknown")
            checks.append(_check("ontology-class", "needs-evidence", "当前图谱类别没有事实准入映射。"))
        label = str(node.get("label") or "").strip()
        if kind in GRAPH_ENTITY_TYPES:
            checks.append(_check("identity-quote", "passed" if label and any(label.casefold() in ref["quote"].casefold() for ref in refs) else "failed",
                                 "对象名称应出现在所引用原文中；同名消歧及身份合并需人工判断。"))
        is_event = kind in GRAPH_EVENT_TYPES or node.get("type") == "EventRecord"
        property_paths = ["label", "type"] + (["payload.eventKind"] if is_event else [])
        unverified_paths = ["payload." + key for key in ("date", "conditions") if payload.get(key)]
        property_evidence = []
        attributes = payload.get("attributes", [])
        if not isinstance(attributes, list):
            checks.append(_check("attribute-schema", "failed", "属性列表结构无效。"))
            attributes = []
        if len(attributes) > MAX_ATTRIBUTES:
            checks.append(_check("attribute-schema", "failed", "属性数量超出单对象可核验的范围。"))
        for index, attribute in enumerate(attributes[:MAX_ATTRIBUTES]):
            path = f"payload.attributes[{index}].value"
            property_paths.append(path)
            if not isinstance(attribute, dict):
                checks.append({**_check("attribute-schema", "failed", "属性条目结构无效。"), "propertyPath": path})
                continue
            property_refs, property_checks = _property_evidence(node, identity_refs, attribute.get("evidenceRefs"), sources,
                                                                attribute.get("value"), path, kind=kind,
                                                                name=attribute.get("name"), qualifiers=attribute)
            checks.extend(property_checks)
            for key in ("unit", "conditions", "validTime"):
                if attribute.get(key) is not None and (key == "validTime" or attribute.get(key) != ""):
                    property_paths.append(f"payload.attributes[{index}].{key}")
            property_evidence.append({"propertyPath": path, "name": attribute.get("name"), "evidenceRefs": property_refs,
                                      "machineStatus": "failed" if any(c["status"] == "failed" for c in property_checks) else "passed",
                                      "truthVerified": False,
                                      **_candidate_links(admissions, revision_id, str(node.get("id")), property_path=path)})
            refs.extend(ref for ref in property_refs if ref not in refs)
        if is_event and payload.get("dateEvidenceRefs"):
            date_refs, date_checks = _property_evidence(node, identity_refs, payload.get("dateEvidenceRefs"), sources,
                                                       payload.get("date"), "payload.date", kind=kind, name="eventDate")
            checks.extend(date_checks)
            if "payload.date" in unverified_paths:
                unverified_paths.remove("payload.date")
            property_paths.append("payload.date")
            property_evidence.append({"propertyPath": "payload.date", "name": "eventDate", "evidenceRefs": date_refs,
                                      "machineStatus": "failed" if any(c["status"] == "failed" for c in date_checks) else "passed",
                                      "truthVerified": False,
                                      **_candidate_links(admissions, revision_id, str(node.get("id")), property_path="payload.date")})
            refs.extend(ref for ref in date_refs if ref not in refs)
        result.append(_target_check("ontology-object", "event" if is_event else "object", assessment,
                                    revision_id, str(node.get("id")), label, refs, checks, admissions,
                                    mapping_allowed=ontology_class is not None, ontologyClass=ontology_class,
                                    propertyPaths=property_paths, propertyEvidence=property_evidence,
                                    unverifiedPropertyPaths=unverified_paths))
    for edge in _array(graph.get("edges")):
        if not isinstance(edge, dict):
            continue
        payload = edge.get("payload") or {}
        if payload.get("extractionMethod") != "llm" and not payload.get("evidenceRefs"):
            continue
        refs, locator_check = _evidence(edge, sources)
        checks = [locator_check]
        source, target = nodes.get(str(edge.get("from"))), nodes.get(str(edge.get("to")))
        mapping = GRAPH_RELATION_TYPES.get(str(edge.get("relation")))
        ontology_predicate = None
        if source is None or target is None:
            checks.append(_check("relation-endpoints", "failed", "关系起点或终点不在此图谱版本中。"))
        elif mapping is None:
            checks.append(_check("ontology-predicate", "needs-evidence", "关联仅作为图谱上下文，没有可入库的本体谓词映射；共同出现不等于因果或已证实关系。"))
        else:
            domains, ranges, predicate, inverse = mapping
            try:
                source_kind, target_kind = IntelligenceStore._graph_node_type(source), IntelligenceStore._graph_node_type(target)
                applicable = source_kind in domains and target_kind in ranges
            except IntelligenceValidationError:
                applicable = False
            ontology_predicate = ONTOLOGY_NS + predicate
            checks.append(_check("ontology-domain-range", "passed" if applicable else "failed",
                                 "起终点类别符合本体谓词定义域和值域；关系含义仍需单独核验。" if applicable else "起终点类别不符合已声明本体谓词的定义域和值域。"))
            for endpoint in (source, target):
                _, endpoint_check = _evidence(endpoint, sources)
                checks.append({**endpoint_check, "code": "endpoint-quote", "graphNodeId": endpoint.get("id")})
        label = " → ".join([str((source or {}).get("label") or edge.get("from")),
                              str(edge.get("relation") or ""), str((target or {}).get("label") or edge.get("to"))])
        result.append(_target_check("ontology-relation", "relation", assessment, revision_id,
                                    str(edge.get("id")), label, refs, checks, admissions,
                                    mapping_allowed=mapping is not None, ontologyPredicate=ontology_predicate,
                                    endpointNodeIds=[str(edge.get("from")), str(edge.get("to"))],
                                    propertyPaths=["from", "relation", "to"]))
    capability = (assessment.get("summary") or {}).get("capabilityAssessment") or {}
    for metric in _array(capability.get("metrics")):
        if not isinstance(metric, dict):
            continue
        metric_id = str(metric.get("id") or "")
        evidence = metric.get("evidence") or {}
        refs, locator_check = _evidence({"evidenceIds": [evidence.get("itemId")], "payload": {"evidenceRefs": [evidence]}}, sources)
        checks = [locator_check]
        try:
            single = normalize_capabilities({"metrics": [{**metric, "itemId": evidence.get("itemId"), "quote": evidence.get("quote")}]}, sources)
            if len(single["metrics"]) != 1:
                raise ValueError("metric is not supported by the quote")
            # The normalizer derives the locator from the exact source body;
            # validate the persisted record too so dates/version fields cannot drift.
            single["metrics"] = [metric]
            validate_capabilities(single, sources)
            checks.append(_check("capability-number-unit", "passed", "数字、单位、数据性质与限定条件字段符合公开指标账本；不能据此认定实测能力。"))
        except (ValueError, KeyError, TypeError):
            checks.append(_check("capability-number-unit", "failed", "能力数值、单位、出处版本或必需字段校验不通过。"))
        result.append(_target_check("ontology-capability", "capability", assessment, revision_id,
                                    "capability:" + metric_id, str(metric.get("equipment") or "") + " · " + str(metric.get("metric") or metric.get("dimension") or metric_id),
                                    refs, checks, admissions, metric_id=metric_id, metricId=metric_id,
                                    ontologyClass=ONTOLOGY_NS + "CapabilityObservation", ontologyPredicate=ONTOLOGY_NS + "hasCapabilityObservation",
                                    propertyPaths=["value", "unit", "qualifier", "basis", "conditions"],
                                    dataBasis=metric.get("basis"), conditions=metric.get("conditions")))
    if not result:
        result.append({"code": "ontology-coverage", "status": "needs-evidence", "targetKind": "coverage",
                       "assessmentId": aid, "graphRevisionId": revision_id or None, "truthVerified": False,
                       "detail": "尚无具备引用的提取对象或公开能力指标可核验；先提取对象并保存图谱版本。"})
    return result


def attach_object_verification(result: dict[str, Any], assessment: dict[str, Any],
                               graph: dict[str, Any] | None, admissions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Enrich a new validation round; never rewrite prior immutable rounds."""
    enriched = deepcopy(result)
    graph = graph if isinstance(graph, dict) else {}
    graph_nodes = {str(n.get("id")): n for n in _array(graph.get("nodes")) if isinstance(n, dict)}
    findings = {str(f.get("id")): f for f in _array((assessment.get("summary") or {}).get("findings")) if isinstance(f, dict)}
    checks = enriched.setdefault("checks", [])
    for check in checks:
        if not isinstance(check, dict) or not str(check.get("code") or "").startswith("model-critique:"):
            continue
        finding_id = str(check["code"]).partition(":")[2]
        finding = findings.get(finding_id)
        if finding is None:
            continue
        # New critique records retain the actual checked quotations. Never
        # expand this subset to every citation belonging to the whole finding.
        actual_refs = [ref for ref in _array(check.get("evidenceRefs")) if isinstance(ref, dict)]
        evidence_ids = ([str(ref.get("itemId")) for ref in actual_refs]
                        if actual_refs else [str(value) for value in _array(check.get("evidenceIds"))])
        context_ids = [node_id for node_id, node in graph_nodes.items()
                       if (node.get("payload") or {}).get("extractionMethod") == "llm"
                       and set(map(str, _array(node.get("evidenceIds")))) & set(evidence_ids)]
        check.update({"targetKind": "finding", "findingId": finding_id,
                      "assessmentId": assessment.get("assessmentId") or assessment.get("id"),
                      "graphRevisionId": graph.get("graphRevisionId") or graph.get("id"),
                      "graphElementId": "finding:" + finding_id if "finding:" + finding_id in graph_nodes else None,
                      "evidenceIds": evidence_ids, "contextGraphNodeIds": context_ids,
                      "linkBasis": "shared-source-context-only", "truthVerified": False})
    object_checks = build_object_verification_checks(assessment, graph, admissions)
    checks.extend(object_checks)
    targets = [check for check in object_checks if check["targetKind"] != "coverage"]
    enriched.setdefault("confidenceFactors", {})["objectVerificationCoverage"] = {
        "graphRevisionId": graph.get("graphRevisionId") or graph.get("id"),
        "targetCount": len(targets), "objectCount": sum(c["targetKind"] == "object" for c in targets),
        "eventCount": sum(c["targetKind"] == "event" for c in targets),
        "relationCount": sum(c["targetKind"] == "relation" for c in targets),
        "capabilityCount": sum(c["targetKind"] == "capability" for c in targets),
        "machinePassedCount": sum(c["machineStatus"] == "passed" for c in targets),
        "machineFailedCount": sum(c["machineStatus"] == "failed" for c in targets),
        "pendingMappingCount": sum(c["machineStatus"] == "needs-evidence" for c in targets),
        "humanConfirmedCount": sum(c["humanReviewRecorded"] for c in targets),
        "independenceVerified": False, "truthVerified": False,
    }
    if any(check["status"] == "failed" for check in object_checks):
        enriched["status"] = "failed"
    elif enriched.get("status") == "passed":
        enriched["status"] = "needs-evidence"
    return enriched
