"""Run-owned UAV report and evidence graph projections; never publish facts."""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import math

from .context_contract import content_hash


# Source claims retain their reported unit. The graph additionally exposes a
# deterministic display normalization so a metric key can never be mistaken for
# the unit shown to an analyst (for example maxSpeedMps reported as km/h).
METRIC_CONTRACT = {
    "enduranceHours": {"label": "续航时间", "unit": "h", "dimension": "time",
                       "units": {"h": Fraction(1), "min": Fraction(1, 60), "s": Fraction(1, 3600)}},
    "payloadKg": {"label": "任务载荷", "unit": "kg", "dimension": "mass",
                   "units": {"kg": Fraction(1), "g": Fraction(1, 1000), "lb": Fraction("0.45359237")}},
    "mtowKg": {"label": "最大起飞重量", "unit": "kg", "dimension": "mass",
                "units": {"kg": Fraction(1), "g": Fraction(1, 1000), "lb": Fraction("0.45359237")}},
    "maxSpeedMps": {"label": "最大速度", "unit": "m/s", "dimension": "speed",
                    "units": {"m/s": Fraction(1), "km/h": Fraction(5, 18), "kn": Fraction(463, 900)}},
}

UAV_GRAPH_SCHEMA_VERSION = "uav-evidence-graph.v1"
GRAPH_HASH_ALGORITHM = "sha256-canonical-json-numbers-v1"
UAV_GRAPH_TYPES = {
    "EquipmentModel", "CapabilityClaim", "EvidenceExcerpt", "SourceSnapshot",
    "Requirement", "RequirementComparison", "Finding", "EvidenceGap",
}


def _finite_number(value):
    return type(value) in (int, float) and (not isinstance(value, float) or math.isfinite(value))


def _display_number(value):
    number = float(value)
    return int(number) if number.is_integer() else round(number, 4)


def _normalise_claim(candidate):
    """Return source-preserving and display-normalized values for one claim."""
    metric = candidate.get("metric")
    contract = METRIC_CONTRACT.get(metric)
    reported_unit = candidate.get("unit")
    if not contract or reported_unit not in contract["units"]:
        return {"reportedValue": deepcopy(candidate.get("value", candidate.get("range"))),
                "reportedUnit": reported_unit, "normalizedValue": None,
                "normalizedUnit": contract["unit"] if contract else None,
                "unitStatus": "unsupported-or-missing"}
    factor = contract["units"][reported_unit]
    raw = candidate.get("value")
    value = _display_number(Fraction(str(raw)) * factor) if _finite_number(raw) else None
    raw_range = candidate.get("range")
    normalized_range = None
    if isinstance(raw_range, dict) and _finite_number(raw_range.get("min")) and _finite_number(raw_range.get("max")):
        normalized_range = {"min": _display_number(Fraction(str(raw_range["min"])) * factor),
                            "max": _display_number(Fraction(str(raw_range["max"])) * factor),
                            "unit": contract["unit"]}
    return {"reportedValue": deepcopy(raw if raw is not None else raw_range), "reportedUnit": reported_unit,
            "normalizedValue": value, "normalizedRange": normalized_range,
            "normalizedUnit": contract["unit"], "dimension": contract["dimension"],
            "unitStatus": "already-canonical" if reported_unit == contract["unit"] else "converted",
            "conversionFactorToCanonical": float(factor)}


def _requirement_label(metric, requirement):
    contract = METRIC_CONTRACT.get(metric, {"label": metric, "unit": None})
    quantity = requirement.get("quantity") if isinstance(requirement, dict) else None
    if not isinstance(quantity, dict):
        return contract["label"]
    value = quantity.get("value", quantity.get("min"))
    unit = quantity.get("unit") or contract.get("unit") or ""
    return f'{contract["label"]} {requirement.get("operator", "")} {value} {unit}'.strip()


def graph_content_hash(graph):
    """Return the digest for a graph without its self-referential digest field."""
    payload = deepcopy(graph)
    payload.pop("contentHash", None)
    if payload.get("contentHashAlgorithm") == GRAPH_HASH_ALGORITHM:
        # JavaScript and JSONB may serialize 1.0 as 1. Treat these equal JSON
        # numbers identically, so exporting/reloading does not break provenance.
        def numbers(value):
            if isinstance(value, float) and math.isfinite(value) and value.is_integer():
                return int(value)
            if isinstance(value, dict):
                return {key: numbers(child) for key, child in value.items()}
            if isinstance(value, list):
                return [numbers(child) for child in value]
            return value
        payload = numbers(payload)
    return content_hash(payload)


def validate_uav_graph(graph, run_id=None):
    """Validate the immutable, run-scoped graph projection.

    This is deliberately a structural check. It does not promote a claim to a
    fact or certify an engineering requirement; those states remain explicit in
    the graph. Legacy snapshots may omit the newer projection scope/hash
    fields, but a current graph must pass the integrity checks before DSH
    presents it as a complete projection.
    """
    errors = []
    if not isinstance(graph, dict):
        return ["graph must be an object"]
    if graph.get("schemaVersion") != UAV_GRAPH_SCHEMA_VERSION:
        errors.append("unsupported schemaVersion")
    if graph.get("contentHashAlgorithm") not in (None, GRAPH_HASH_ALGORITHM):
        errors.append("unsupported contentHashAlgorithm")
    if run_id is not None and graph.get("runId") != run_id:
        errors.append("runId does not match the requested Run")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return errors + ["nodes and edges must be arrays"]
    node_ids = []
    for index, item in enumerate(nodes):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            errors.append(f"nodes[{index}] has no stable id")
            continue
        node_ids.append(item["id"])
        if item.get("type") not in UAV_GRAPH_TYPES:
            errors.append(f"nodes[{index}] has unsupported type")
        if item.get("truthVerified") is not False:
            errors.append(f"nodes[{index}] must remain unverified")
    if len(node_ids) != len(set(node_ids)):
        errors.append("node ids are not unique")
    node_set = set(node_ids)
    edge_ids = []
    for index, item in enumerate(edges):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            errors.append(f"edges[{index}] has no stable id")
            continue
        edge_ids.append(item["id"])
        if (not isinstance(item.get("source"), str) or not isinstance(item.get("target"), str)
                or item["source"] not in node_set or item["target"] not in node_set):
            errors.append(f"edges[{index}] references an unknown node")
        if not isinstance(item.get("relation"), str) or not item["relation"]:
            errors.append(f"edges[{index}] has no relation")
        if item.get("assertionStatus") != "report-projection":
            errors.append(f"edges[{index}] is not a report projection")
        if item.get("truthVerified") is not False:
            errors.append(f"edges[{index}] must remain unverified")
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("edge ids are not unique")
    if graph.get("projectionOnly") is not True:
        errors.append("projectionOnly must be true")
    if "projectionScope" in graph and graph.get("projectionScope") != "run-report-ontology-projection":
        errors.append("projectionScope is invalid")
    # Legacy projections without a digest remain readable with a warning.
    # Any supplied digest, including on an old projection, must be authentic.
    if "projectionScope" in graph or "contentHash" in graph:
        digest = graph.get("contentHash")
        try:
            expected = graph_content_hash(graph)
        except (TypeError, ValueError):
            expected = None
        if not isinstance(digest, str) or len(digest) != 64 or digest != expected:
            errors.append("contentHash does not match the graph payload")
    return errors


def enrich_uav_report(output, source, bundle, requirements, tool_results, run_id):
    """Attach deterministic comparisons and a graph from frozen Run material.

    Edges describe report provenance and requirements, not new trusted domain
    assertions. Unknown operating conditions remain unknown even when a raw
    numeric comparison passes.
    """
    candidates = {c["candidateId"]: c for c in bundle["candidates"]}
    calculations = {}
    for result in tool_results.values():
        if result.get("toolKey") != "engineering.evaluate_requirement":
            continue
        metric = result.get("inputSnapshot", {}).get("observation", {}).get("metric")
        if metric in requirements:
            calculations.setdefault(metric, []).append(deepcopy(result))
    baseline, comparisons, gaps = {}, {}, []
    for metric, requirement in requirements.items():
        claims = [deepcopy(c) for c in candidates.values() if c.get("metric") == metric]
        baseline[metric] = {"state": "reported" if claims else "missing", "claims": claims}
        results = calculations.get(metric, [])
        comparisons[metric] = {"requirement": deepcopy(requirement), "calculations": results,
                               "reviewedCompliance": "unknown", "engineeringCertification": False}
        reasons = []
        if not claims:
            reasons.append("missing-cited-value")
        if requirement is not None and not results:
            reasons.append("requirement-comparison-not-executed")
        for claim in claims:
            for field, reason in (("conditions", "operating-conditions-unknown"),
                                  ("variant", "aircraft-variant-unknown"),
                                  ("observedAt", "observation-time-unknown")):
                if not claim.get(field):
                    reasons.append(reason)
            if claim.get("contentKind") == "ontology-quote":
                reasons.append("original-full-source-unavailable")
        reasons.extend(reason for result in results for reason in result.get("reasons", []))
        reasons.append("evidence-not-independently-verified")
        for reason in dict.fromkeys(reasons):
            gaps.append({"gapId": "gap-" + content_hash([run_id, metric, reason])[:24], "metric": metric,
                         "reason": reason, "status": "open", "candidateIds": [c["candidateId"] for c in claims]})
    output.update({
        "truthStatus": "unverified", "publicationStatus": "not-published", "publicationReviewRequired": True,
        "assessment": {"entityId": output["entityId"], "summary": output["summary"],
                       "requirements": deepcopy(requirements), "baseline": baseline,
                       "compliance": {"items": comparisons, "unknownRequirements": list(requirements)},
                       "truthVerified": False, "reviewStatus": "pending", "engineeringCertification": False},
        "gaps": gaps,
        "gapSummary": [{"reason": reason, "metrics": sorted({item["metric"] for item in gaps if item["reason"] == reason}),
                         "count": sum(item["reason"] == reason for item in gaps), "status": "open"}
                        for reason in dict.fromkeys(item["reason"] for item in gaps)],
    })
    # Include actual deterministic results in the model's immutable report input
    # at the call site; this projection also keeps them accessible to DSH.
    output["ontology"] = build_uav_graph(output, source, bundle, run_id)


def build_uav_graph(output, source, bundle, run_id):
    common = {"truthStatus": "unverified", "truthVerified": False, "reviewStatus": "pending"}
    entity_id = output["entityId"]
    entity = source.get("entity", {})
    nodes = [{"id": entity_id, "type": "EquipmentModel", "label": entity.get("preferred_name") or entity_id,
              "entityId": entity_id, "sourceSnapshot": deepcopy(output["sourceSnapshot"]), **common}]
    edges, by_id = [], {entity_id}

    def node(identity, kind, label, **properties):
        if identity not in by_id:
            nodes.append({"id": identity, "type": kind, "label": str(label), **properties, **common})
            by_id.add(identity)
        return identity

    def edge(start, end, relation, **properties):
        edges.append({"id": "edge-" + content_hash([start, end, relation])[:24],
                      "source": start, "target": end, "relation": relation,
                      "assertionStatus": "report-projection", **properties, **common})

    sources = {s["sourceId"]: s for s in bundle["sources"]}
    candidates = {c["candidateId"]: c for c in bundle["candidates"]}
    for candidate_id, candidate in candidates.items():
        display = _normalise_claim(candidate)
        metric_contract = METRIC_CONTRACT.get(candidate.get("metric"), {"label": candidate.get("metric")})
        display_value = display.get("normalizedValue")
        if display_value is None and display.get("normalizedRange"):
            display_value = f'{display["normalizedRange"]["min"]}–{display["normalizedRange"]["max"]}'
        claim_label = f'{metric_contract["label"]}: {display_value} {display.get("normalizedUnit") or candidate.get("unit") or ""}'.strip()
        claim_id = node(candidate_id, "CapabilityClaim",
                        claim_label,
                        candidateId=candidate_id, metric=candidate["metric"], value=deepcopy(candidate.get("value")),
                        unit=candidate.get("unit"), qualifier=candidate.get("qualifier"),
                        conditions=deepcopy(candidate.get("conditions")), variant=candidate.get("variant"),
                        reportedValue=display["reportedValue"], reportedUnit=display["reportedUnit"],
                        normalizedValue=display["normalizedValue"], normalizedRange=display.get("normalizedRange"),
                        normalizedUnit=display["normalizedUnit"], unitStatus=display["unitStatus"],
                        dimension=display.get("dimension"),
                        conversionFactorToCanonical=display.get("conversionFactorToCanonical"))
        edge(entity_id, claim_id, "hasReportedCapability", candidateIds=[candidate_id])
        source_record = sources.get(candidate.get("sourceId"), {})
        evidence_id = node("evidence:" + candidate_id, "EvidenceExcerpt", candidate["quote"],
                           candidateId=candidate_id, sourceId=candidate.get("sourceId"),
                           sourceArtifactId=candidate.get("sourceArtifactId"),
                           sourceArtifactHash=candidate.get("sourceArtifactHash"), contentHash=candidate.get("contentHash"),
                           quote=candidate["quote"], startOffset=candidate.get("startOffset"), endOffset=candidate.get("endOffset"),
                           locator=deepcopy(source_record.get("locator", {})), sourceUri=candidate.get("sourceUri"))
        edge(claim_id, evidence_id, "supportedBy", candidateIds=[candidate_id])
        artifact_id = candidate["sourceArtifactId"]
        source_metadata = source_record.get("metadata", {}) if isinstance(source_record.get("metadata"), dict) else {}
        node(artifact_id, "SourceSnapshot", source_metadata.get("sourceRef") or artifact_id,
             sourceArtifactId=artifact_id, sourceArtifactHash=candidate["sourceArtifactHash"],
             sourceUri=source_record.get("sourceUri"), contentKind=source_record.get("contentKind"),
             sourceSnapshotId=output.get("sourceSnapshot", {}).get("sourceSnapshotId"),
             sourceRevision=output.get("sourceSnapshot", {}).get("revision"),
             importedAt=output.get("sourceSnapshot", {}).get("importedAt"),
             extractedAt=output.get("sourceSnapshot", {}).get("extractedAt"),
             lineage={"originalSource": source_metadata.get("sourceRef"),
                      "immutableSnapshot": output.get("sourceSnapshot", {}).get("sourceUri"),
                      "artifactHash": candidate.get("sourceArtifactHash")})
        edge(evidence_id, artifact_id, "excerptOf")
    for metric, item in output["assessment"]["compliance"]["items"].items():
        requirement_id = node("requirement:" + metric, "Requirement", metric, requirement=deepcopy(item["requirement"]))
        edge(entity_id, requirement_id, "assessedAgainst")
        for result in item["calculations"]:
            calculation_id = result["artifactId"]
            numeric = result.get("numericComparison") or {}
            numeric_status = numeric.get("result") or result.get("mathResult") or "unknown"
            engineering_status = result.get("reviewedCompliance", "unknown")
            node(calculation_id, "RequirementComparison", _requirement_label(metric, item["requirement"]),
                 artifactId=calculation_id, artifactHash=result.get("artifactHash"),
                 formulaId=result.get("formulaId"), formulaVersion=result.get("formulaVersion"),
                 numericComparison=numeric, mathResult=result.get("mathResult"),
                 numericStatus=numeric_status, engineeringStatus=engineering_status,
                 conditionMatch=result.get("conditionMatch"), reviewedCompliance=engineering_status,
                 interpretation="数值比较结果，不等于工程符合性" if numeric_status == "satisfied" else "无法完成可靠的数值比较")
            edge(calculation_id, requirement_id, "evaluates")
            observation = result.get("inputSnapshot", {}).get("observation", {})
            for candidate_id, candidate in candidates.items():
                if candidate.get("metric") == metric and candidate.get("fingerprint") == observation.get("inputVersion"):
                    edge(calculation_id, candidate_id, "usesCandidate", candidateIds=[candidate_id])
    for finding in output["findings"]:
        finding_id = node("finding-" + content_hash(finding)[:24], "Finding", finding["statement"],
                          statement=finding["statement"], candidateIds=deepcopy(finding["candidateIds"]))
        edge(entity_id, finding_id, "hasAnalysis")
        for candidate_id in finding["candidateIds"]:
            if candidate_id in candidates:
                edge(finding_id, candidate_id, "basedOn", candidateIds=[candidate_id])
    # One node per reason keeps the graph readable; the node retains every
    # affected metric and gap id so aggregation never discards provenance.
    grouped_gaps = {}
    for gap in output["gaps"]:
        grouped_gaps.setdefault(gap["reason"], {"metrics": set(), "gapIds": []})
        grouped_gaps[gap["reason"]]["metrics"].add(gap["metric"])
        grouped_gaps[gap["reason"]]["gapIds"].append(gap["gapId"])
    for reason, group in grouped_gaps.items():
        gap_id = "gap-reason-" + content_hash([run_id, reason])[:24]
        node(gap_id, "EvidenceGap", reason, metrics=sorted(group["metrics"]), reason=reason,
             gapIds=group["gapIds"], status="open", count=len(group["gapIds"]))
        for metric in sorted(group["metrics"]):
            edge("requirement:" + metric, gap_id, "limitedBy", gapIds=group["gapIds"])
    graph = {"schemaVersion": UAV_GRAPH_SCHEMA_VERSION, "runId": run_id, "entityId": entity_id,
             "contentHashAlgorithm": GRAPH_HASH_ALGORITHM,
             "nodes": nodes, "edges": edges, "projectionOnly": True,
             "projectionScope": "run-report-ontology-projection",
             "sourceSnapshot": deepcopy(output["sourceSnapshot"]),
             "statusSummary": {"truth": "unverified", "review": "pending", "publication": "not-published",
                               "numericComparisonsAreNotEngineeringCertification": True},
             "gapSummary": deepcopy(output.get("gapSummary", [])), **common}
    graph["contentHash"] = graph_content_hash(graph)
    return graph
