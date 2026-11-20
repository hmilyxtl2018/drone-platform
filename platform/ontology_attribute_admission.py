"""Source-qualified, descriptive property assertions for independent review.

This whitelist maps descriptions, never inferred performance or manufacturing
parameters. A display translation remains distinct from the literal fact value.
The store supplies its immutable snapshot locator validator to avoid a circular
dependency and retains review/publication as separate human actions.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Callable

try:
    from evidence_graph import attribute_value_supported, event_date_supported
except ModuleNotFoundError:  # pragma: no cover
    from .evidence_graph import attribute_value_supported, event_date_supported

NS = "https://local.ontology/coast-raid-k#"
TECHNICAL = {"EquipmentModel", "Technology", "Program"}
EVENTS = {"TestEvent", "ProcurementEvent", "ProductionEvent"}
ATTRIBUTE_PREDICATES = {
    "reportedStandard": {"label": "公开标准", "domains": TECHNICAL, "aliases": ["标准", "公开标准", "通信标准", "标准名称", "standard"]},
    "reportedInterface": {"label": "公开接口", "domains": TECHNICAL, "aliases": ["接口", "接口方式", "接口标准", "接口框架", "公开接口", "interface"]},
    "reportedArchitecture": {"label": "公开架构", "domains": TECHNICAL, "aliases": ["架构", "架构类型", "系统架构", "开放架构", "公开架构", "architecture"]},
    "reportedHumanOversight": {"label": "人工监督", "domains": TECHNICAL | {"TestEvent"}, "aliases": ["人工监督", "人机监督", "监督", "监督方式", "人工监督方式", "human oversight"]},
    "reportedTestStatus": {"label": "公开试验状态", "domains": TECHNICAL | {"TestEvent"}, "aliases": ["试验状态", "测试状态", "公开试验状态", "test status"]},
    "reportedMaturity": {"label": "公开成熟度描述", "domains": TECHNICAL, "aliases": ["成熟度", "技术成熟度", "产业成熟度", "公开成熟度描述", "maturity"]},
    "reportedContractStage": {"label": "公开合同阶段", "domains": TECHNICAL | {"ProcurementEvent", "ProductionEvent"}, "aliases": ["合同阶段", "采购阶段", "采购状态", "公开合同阶段", "contract stage"]},
    "reportedEventDate": {"label": "公开事件日期", "domains": EVENTS, "aliases": ["事件日期", "eventDate"]},
}
PROPERTY_PATH = re.compile(r"^payload\.attributes\[(0|[1-9]\d?)\]\.value$")


class AttributeAdmissionError(ValueError):
    pass


def get_attribute_admission_catalog() -> list[dict[str, Any]]:
    return [{"ontologyPredicate": NS + name, "id": name, "label": entry["label"],
             "domains": sorted(entry["domains"]), "aliases": list(entry["aliases"])}
            for name, entry in ATTRIBUTE_PREDICATES.items()]


def predicate_for(name: str, requested: Any, kind: str, path: str,
                  suggested: Any = None) -> str:
    aliases = [key for key, entry in ATTRIBUTE_PREDICATES.items()
               if isinstance(name, str) and name.casefold().strip() in {alias.casefold() for alias in entry["aliases"]}]
    if requested is not None:
        if not isinstance(requested, str):
            raise AttributeAdmissionError("ontologyPredicate must be a declared descriptive predicate")
        predicate = requested[len(NS):] if requested.startswith(NS) else requested
    else:
        # The model may propose a declared predicate while extracting the
        # attribute.  Treat it as a recommendation and run it through the
        # same domain/range and vocabulary checks as a human correction.  If
        # the model did not provide one, retain the legacy alias fallback.
        if isinstance(suggested, dict):
            suggested = suggested.get("id") or suggested.get("predicate") or suggested.get("ontologyPredicate")
        if isinstance(suggested, str) and "#" in suggested:
            suggested = suggested.rsplit("#", 1)[-1]
        if isinstance(suggested, str) and suggested in ATTRIBUTE_PREDICATES:
            predicate = suggested
        elif len(aliases) == 1:
            predicate = aliases[0]
        else:
            raise AttributeAdmissionError("model did not provide an unambiguous predicate recommendation; review ontologyPredicate")
    entry = ATTRIBUTE_PREDICATES.get(predicate)
    if entry is None or kind not in entry["domains"]:
        raise AttributeAdmissionError("property predicate is not allowed for this object class")
    if aliases and predicate not in aliases:
        raise AttributeAdmissionError("the selected predicate contradicts the recognized property category")
    if (path == "payload.date") != (predicate == "reportedEventDate"):
        raise AttributeAdmissionError("event date predicate must map the source-qualified event date field")
    return predicate


def property_from_node(node: dict, path: Any, kind: str) -> dict:
    if not isinstance(path, str):
        raise AttributeAdmissionError("propertyPath is required")
    payload = node.get("payload") or {}
    if path == "payload.date" and kind in EVENTS:
        return {"name": "eventDate", "value": payload.get("date"), "evidenceRefs": payload.get("dateEvidenceRefs")}
    match = PROPERTY_PATH.fullmatch(path)
    attributes = payload.get("attributes")
    if match is None or not isinstance(attributes, list) or int(match[1]) >= len(attributes):
        raise AttributeAdmissionError("propertyPath is not an extracted attribute or public event date")
    attribute = attributes[int(match[1])]
    if not isinstance(attribute, dict):
        raise AttributeAdmissionError("property record must be an object")
    return deepcopy(attribute)


def validate_property(node: dict, attribute: dict, sources: list, kind: str, path: str,
                      evidence_validator: Callable) -> tuple[list, list]:
    identities = evidence_validator(node, sources)
    refs = evidence_validator({"evidenceIds": node.get("evidenceIds", []),
                               "payload": {"evidenceRefs": attribute.get("evidenceRefs")}}, sources)
    name, value = attribute.get("name"), attribute.get("value")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise AttributeAdmissionError("property name is invalid")
    if not refs or not all(attribute_value_supported(value, ref["quote"]) for ref in refs):
        raise AttributeAdmissionError("raw property value is not literally supported by its source quotes")
    if path == "payload.date" and not all(event_date_supported(value, ref["quote"]) for ref in refs):
        raise AttributeAdmissionError("public event date needs literal calendar evidence")
    label = str(node.get("label") or "")
    bodies = {source["itemId"]: str(source.get("body") or "") for source in sources}
    def span(ref: dict) -> tuple[int, int]:
        if type(ref.get("startChar")) is int:
            return ref["startChar"], ref["endChar"]
        lines = bodies[ref["itemId"]].splitlines(keepends=True)
        return sum(map(len, lines[:ref["lineStart"] - 1])), sum(map(len, lines[:ref["lineEnd"]]))
    if kind not in EVENTS:
        context = label and all(label.casefold() in ref["quote"].casefold() for ref in refs)
    else:
        context = all(any(ref["itemId"] == identity["itemId"]
                          and max(span(ref)[0], span(identity)[0]) < min(span(ref)[1], span(identity)[1])
                          for identity in identities) for ref in refs)
    if not context:
        raise AttributeAdmissionError("property quote does not establish this object's source context")
    for key, limit in (("unit", 60), ("conditions", 300), ("validTime", 200)):
        value = attribute.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or len(value) > limit or not all(value in ref["quote"] for ref in refs):
            raise AttributeAdmissionError("property qualifier or time lacks literal source support")
    return refs, identities


def build_property_assertion(node: dict, selection: dict, sources: list, kind: str,
                             subject: str, evidence_validator: Callable,
                             *, edited: dict | None = None) -> tuple[str, dict]:
    path = selection.get("propertyPath")
    attribute = property_from_node(node, path, kind)
    if edited is not None:
        allowed = {"rawValue", "normalizedValue", "unit", "conditions", "validTime", "ontologyPredicate", "evidenceRefs"}
        if not isinstance(edited, dict) or not edited or set(edited) - allowed:
            raise AttributeAdmissionError("editedProperty contains unsupported fields")
        for key, value in edited.items():
            if key == "rawValue": attribute["value"] = value
            elif key == "normalizedValue": attribute["displayValue"] = value
            elif key != "ontologyPredicate": attribute[key] = value
        if "ontologyPredicate" in edited:
            selection = {**selection, "ontologyPredicate": edited["ontologyPredicate"]}
        # An old model recommendation does not cover a human-corrected row.
        attribute.pop("reviewRecommendation", None)
    predicate_suggestion = attribute.get("ontologyPredicateSuggestion")
    predicate = predicate_for(attribute.get("name", ""), selection.get("ontologyPredicate"), kind, path,
                              predicate_suggestion)
    refs, identities = validate_property(node, attribute, sources, kind, path, evidence_validator)
    assertion = {"subject": subject, "subjectLabel": node["label"], "propertyName": attribute["name"].strip(),
                 "predicate": NS + predicate, "rawValue": attribute["value"],
                 "evidenceRefs": refs, "identityEvidenceRefs": identities}
    if path == "payload.date":
        assertion["validTime"] = attribute["value"]
    for key in ("unit", "conditions", "validTime"):
        if attribute.get(key) not in (None, ""):
            assertion[key] = attribute[key]
    display = attribute.get("displayValue")
    if display not in (None, ""):
        if not isinstance(display, str) or not display.strip() or len(display) > 600:
            raise AttributeAdmissionError("normalizedValue must be bounded text")
        assertion["normalizedValue"] = display.strip()
        assertion["normalizationBasis"] = "human-proposal" if edited and "normalizedValue" in edited else "model-translation"
    recommendation = attribute.get("reviewRecommendation")
    if isinstance(recommendation, dict) and recommendation.get("action") in {"consider", "more-evidence", "reject"}:
        assertion["reviewRecommendation"] = {"action": recommendation["action"],
                                             "reason": str(recommendation.get("reason") or "")[:1000],
                                             "source": "model", "truthVerified": False}
    if isinstance(predicate_suggestion, dict):
        # Keep the recommendation on the immutable candidate so the reviewer
        # can see why the automatic mapping was chosen.  This is metadata only;
        # publication still requires an explicit human review action.
        suggestion = deepcopy(predicate_suggestion)
        suggestion["humanReviewRequired"] = True
        suggestion["truthVerified"] = False
        assertion["predicateRecommendation"] = suggestion
    return predicate, assertion
