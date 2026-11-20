"""Deterministic evidence checks. Quotes authenticate text location, never truth."""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_source_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def normalize_candidate(candidate, snapshot, *, allowed_entities, allowed_metrics, allow_ontology_quote=False):
    """Require an exact Unicode-character slice in a stored full source snapshot.

    Values, ranges, units, configuration and conditions are preserved. No unit
    conversions, engineering judgements or model-supplied verification are trusted.
    """
    if not isinstance(candidate, dict) or not isinstance(snapshot, dict):
        raise ValueError("candidate and source snapshot must be objects")
    entity = candidate.get("entityId")
    metric = candidate.get("metric")
    if entity not in allowed_entities or metric not in allowed_metrics:
        raise ValueError("candidate entity or metric is outside the permitted task")
    permitted_kinds = {"markdown", "ontology-quote"} if allow_ontology_quote else {"markdown"}
    if snapshot.get("contentKind") not in permitted_kinds or not snapshot.get("artifactId"):
        raise ValueError("candidate requires a persisted full markdown source snapshot")
    text = snapshot.get("markdown")
    quote = candidate.get("quote")
    if not isinstance(text, str) or snapshot.get("contentHash") != content_hash(text):
        raise ValueError("source snapshot hash does not match its markdown")
    if not isinstance(quote, str) or not quote:
        raise ValueError("candidate quote is required")
    start, end = candidate.get("startOffset"), candidate.get("endOffset")
    if start is None and end is None:
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            raise ValueError("quote is absent or ambiguous; exact offsets required")
        end = start + len(quote)
    if any(not isinstance(n, int) or isinstance(n, bool) for n in (start, end)) or not 0 <= start < end <= len(text):
        raise ValueError("quote offsets must be valid character offsets")
    if text[start:end] != quote:
        raise ValueError("quote does not equal the saved source slice")
    value = candidate.get("value")
    def finite_quantities(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite candidate quantities are invalid")
        if isinstance(item, dict):
            for child in item.values():
                finite_quantities(child)
        if isinstance(item, list):
            for child in item:
                finite_quantities(child)
    finite_quantities(candidate)
    qualifier = candidate.get("qualifier", "unknown")
    if qualifier not in {"exact", "range", "minimum", "maximum", "approximate", "unknown", "conflicting"}:
        raise ValueError("unsupported candidate qualifier")
    if qualifier == "range" and not isinstance(candidate.get("range"), dict):
        raise ValueError("range candidate must preserve its bounds")
    if qualifier == "range":
        bounds = candidate["range"]
        low, high = bounds.get("min"), bounds.get("max")
        if low is None and high is None:
            raise ValueError("range must contain at least one bound")
        for bound in (low, high):
            if bound is not None and (isinstance(bound, bool) or not isinstance(bound, (int, float))):
                raise ValueError("range bounds must be numeric or unknown")
        if low is not None and high is not None and low > high:
            raise ValueError("range lower bound exceeds upper bound")
    result = {key: deepcopy(candidate[key]) for key in (
        "entityId", "metric", "value", "range", "unit", "conditions", "configuration",
        "variant", "observedAt", "rawValue", "assertionId") if key in candidate}
    result.update({"qualifier": qualifier, "quote": quote, "startOffset": start, "endOffset": end,
                   "offsetUnit": "unicode-codepoint", "sourceArtifactId": snapshot["artifactId"],
                   "sourceUri": snapshot.get("sourceUri"), "contentHash": snapshot["contentHash"],
                   "retrievedAt": snapshot.get("retrievedAt"), "publishedAt": snapshot.get("publishedAt"),
                   "contentKind": snapshot["contentKind"],
                   "truthVerified": False, "reviewStatus": "pending", "verificationStatus": "unverified", "citationValidated": True})
    if snapshot.get("sourceId"):
        result["sourceId"] = snapshot["sourceId"]
    if snapshot.get("artifactHash"):
        result["sourceArtifactHash"] = snapshot["artifactHash"]
    if snapshot["contentKind"] == "ontology-quote":
        result["citationScope"] = "stored-ontology-excerpt; original full source not available"
    # Fetch time and publication time are never substitutes for observation time.
    result.setdefault("observedAt", None)
    digest = candidate_fingerprint(result)
    result["fingerprint"] = digest
    result["candidateId"] = "candidate-" + digest[:24]
    return result


def candidate_fingerprint(candidate):
    """Stable full SHA256 excluding generated identity and audit status fields."""
    ignored = {"candidateId", "fingerprint", "createdAt", "reviewStatus", "verificationStatus", "truthVerified"}
    value = {key: item for key, item in candidate.items() if key not in ignored}
    return content_hash(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def source_groups(snapshots):
    """Group duplicates and syndication; count unknown independence conservatively.

    Different domains are not proof of independent reporting. Equal normalized
    text, canonical URL or a common explicitly recorded origin form one group.
    Other pages remain separate groups with independenceStatus=unknown.
    """
    groups, memberships = [], {}
    for source in snapshots:
        text = re.sub(r"\s+", " ", str(source.get("markdown", ""))).strip()
        keys = ["url:" + canonical_source_url(source["sourceUri"])] if source.get("sourceUri") else []
        if text:
            keys.append("text:" + content_hash(text))
        if source.get("originSourceUri"):
            keys.append("url:" + canonical_source_url(source["originSourceUri"]))
        indexes = {memberships[key] for key in keys if key in memberships}
        if indexes:
            index = min(indexes)
            # Merge previously separate groups connected by a new common origin.
            for other in sorted(indexes - {index}):
                groups[index]["sources"].extend(groups[other]["sources"])
                groups[other]["sources"] = []
                memberships = {key: index if value == other else value for key, value in memberships.items()}
        else:
            index = len(groups)
            groups.append({"groupId": f"source-group-{index + 1}", "sources": [], "independenceStatus": "unknown"})
        groups[index]["sources"].append(source.get("artifactId") or source.get("sourceUri"))
        for key in keys:
            memberships[key] = index
    return [group for group in groups if group["sources"]]


def detect_conflicts(candidates):
    """Preserve all conflicting claims; a unit/configuration mismatch stays unknown."""
    groups = {}
    for candidate in candidates:
        groups.setdefault((candidate.get("entityId"), candidate.get("metric")), []).append(candidate)
    issues = []
    for (entity, metric), claims in groups.items():
        if len(claims) < 2:
            continue
        contexts = {json.dumps({key: claim.get(key) for key in ("unit", "conditions", "variant", "configuration", "observedAt")}, sort_keys=True) for claim in claims}
        values = {json.dumps({key: claim.get(key) for key in ("value", "range", "qualifier")}, sort_keys=True) for claim in claims}
        if len(values) > 1 or len(contexts) > 1:
            issues.append({"entityId": entity, "metric": metric,
                           "status": "potential-conflict" if len(contexts) == 1 else "comparability-unknown",
                           "candidateIds": [c["candidateId"] for c in claims], "resolution": "requires-human-review"})
    return issues
