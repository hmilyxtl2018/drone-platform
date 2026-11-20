"""Deterministic primitives for the future-battlefield research Agent.

These functions never call a provider. They turn untrusted source/model
payloads into bounded, auditable candidates for an Action Agent.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Iterable


# Source and model payloads are data.  These patterns deliberately produce a
# review signal only; they never cause source text to be executed or treated
# as a runtime instruction.
_INJECTION_PATTERNS = (
    ("ignore-instructions", re.compile(
        r"(?:ignore|disregard|forget|override)\s+(?:(?:all|any)\s+)?(?:(?:previous|prior|above|system|developer)\s+)?(?:instructions?|rules?|constraints?)|"
        r"忽略(?:以上|之前|先前|系统|开发者)(?:指令|规则|约束)|无视(?:以上|之前)(?:指令|规则)", re.IGNORECASE)),
    ("role-override", re.compile(
        r"(?:you are now|act as|pretend to be|system message|developer message)|"
        r"你现在是|请扮演|系统消息|开发者消息", re.IGNORECASE)),
    ("secret-exfiltration", re.compile(
        r"(?:reveal|print|dump|exfiltrate)\s+(?:the\s+)?(?:prompt|instructions?|secrets?|tokens?|credentials?)|"
        r"泄露|输出(?:系统提示|隐藏指令|密钥|令牌|凭据)", re.IGNORECASE)),
    ("unsafe-action-request", re.compile(
        r"(?:execute|run|call)\s+(?:shell|powershell|命令)|"
        r"执行(?:命令|脚本)|绕过(?:审核|审批|权限)", re.IGNORECASE)),
)
_NUMERIC_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:\d+(?:[.,]\d+)?|\d+\s*[-~至]\s*\d+)(?:\s*[A-Za-z%°μµ一-龥][A-Za-z0-9%°μµ/²³一-龥-]*)?"
)
_KNOWN_UNITS = {
    "kg", "g", "mg", "t", "lb", "lbs", "km", "m", "mm", "cm", "mile", "mi",
    "m/s", "km/h", "kn", "h", "hr", "min", "s", "hz", "khz", "mhz", "ghz",
    "w", "kw", "mw", "wh", "kwh", "mwh", "v", "a", "n", "pa", "db", "%", "°c", "c",
}
_UNIT_STOPWORDS = {"and", "or", "with", "is", "to", "from", "the", "of", "for", "in"}
_CERTAINTY_PATTERN = re.compile(
    r"(?:必然|必将|一定|显然|完全|已证明|无疑|绝不会|guarantee[ds]?|certainly|definitively|proves?)",
    re.IGNORECASE,
)


def detect_prompt_injection(text: Any) -> list[dict[str, Any]]:
    """Return review-only indicators for instruction-like source content."""
    if not isinstance(text, str) or not text:
        return []
    findings = []
    for code, pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append({"code": code, "startOffset": match.start(), "endOffset": match.end(),
                             "action": "treat-as-untrusted-data"})
    return findings


def detect_source_relations(sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find explicit reprints/origins without declaring independence."""
    rows = [item for item in sources if isinstance(item, dict)]
    findings = []
    for index, source in enumerate(rows):
        origin = source.get("originSourceUri")
        relation = source.get("sourceRelation")
        if origin or relation in {"republished", "aggregator", "translation"}:
            findings.append({"sourceIndex": index, "sourceUri": source.get("sourceUri"),
                             "originSourceUri": origin, "sourceRelation": relation or "republished",
                             "independenceStatus": "unknown", "resolution": "requires-human-review"})
    return findings


def detect_numeric_unit_risks(text: Any) -> list[dict[str, Any]]:
    """Flag numbers that need human/unit review without converting or guessing."""
    if not isinstance(text, str) or not text:
        return []
    risks = []
    date_spans = [match.span() for match in re.finditer(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text)]
    for match in _NUMERIC_TOKEN.finditer(text):
        # ISO/calendar dates are handled by the event normalizer and are not
        # dimensionless engineering quantities.
        if any(start <= match.start() and match.end() <= end for start, end in date_spans):
            continue
        token = match.group(0).strip()
        # A number followed by an alphabetic token is considered unit-bearing;
        # the unit itself still needs domain validation.  Bare values remain a
        # gap because a model must not infer their dimension.
        unit_match = re.search(r"([A-Za-z%°μµ一-龥][A-Za-z0-9%°μµ/²³一-龥-]*)$", token)
        unit = unit_match.group(1).lower() if unit_match else None
        if unit is None or unit in _UNIT_STOPWORDS:
            risks.append({"code": "numeric-value-without-unit", "token": token,
                          "startOffset": match.start(), "endOffset": match.end()})
        elif unit not in _KNOWN_UNITS:
            risks.append({"code": "unknown-unit", "token": token, "unit": unit,
                          "startOffset": match.start(), "endOffset": match.end()})
    return risks


def detect_temporal_conflicts(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain contradictory dates as review gaps; no date is selected."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        identity = event.get("eventId") or event.get("id") or event.get("sourceUri") or event.get("title") or event.get("name")
        if identity is None:
            continue
        date_value = event.get("date") or event.get("observedAt") or event.get("publishedAt")
        groups[str(identity)].append({"index": index, "date": date_value})
    conflicts = []
    for identity, rows in groups.items():
        known = {row["date"] for row in rows if isinstance(row["date"], str) and row["date"]}
        if len(known) > 1:
            conflicts.append({"identity": identity, "dates": sorted(known), "eventIndexes": [row["index"] for row in rows],
                              "resolution": "requires-human-review"})
    return conflicts


def detect_over_inference(judgments: Iterable[dict[str, Any]], claims: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag certainty language that is not accompanied by conditions/unknowns."""
    claim_by_id = {str(item.get("claimId")): item for item in claims if isinstance(item, dict) and item.get("claimId") is not None}
    flags = []
    for index, judgment in enumerate(judgments):
        if not isinstance(judgment, dict):
            continue
        statement = judgment.get("statement") if isinstance(judgment.get("statement"), str) else ""
        if not statement or not _CERTAINTY_PATTERN.search(statement):
            continue
        references = judgment.get("supportingClaims") if isinstance(judgment.get("supportingClaims"), list) else []
        cited_text = " ".join(str(claim_by_id.get(str(ref), {}).get("body", "")) for ref in references)
        # A strong statement with no conditions/unknowns is unsafe even when a
        # citation exists.  The presence of the trigger is retained as a gap,
        # never converted into a numerical confidence penalty.
        if not judgment.get("conditions") and not judgment.get("unknowns"):
            flags.append({"judgmentIndex": index, "statement": statement,
                          "certaintyTrigger": _CERTAINTY_PATTERN.search(statement).group(0),
                          "citedClaimCount": len(references), "citedTextLength": len(cited_text),
                          "resolution": "requires-human-review"})
    return flags


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def deduplicate_sources(sources: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Group exact-body duplicates and preserve all source identities."""
    sources = list(sources)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in sources:
        body = source.get("body", source.get("content"))
        if not isinstance(body, str) or not body.strip():
            raise ValueError("source body must be nonempty text")
        digest = body_hash(body)
        if source.get("contentHash") and source["contentHash"] != digest:
            raise ValueError("source content hash differs from its body")
        groups[digest].append({**source, "contentHash": digest})
    result = []
    for digest, rows in groups.items():
        canonical = next((r for r in rows if r.get("sourceRole") in {"official-publication", "evaluation-report", "procurement-record"}), rows[0])
        result.append({
            "contentHash": digest,
            "canonicalSource": canonical.get("sourceUri") or canonical.get("url"),
            "sourceUris": [r.get("sourceUri") or r.get("url") for r in rows if r.get("sourceUri") or r.get("url")],
            "duplicateCount": max(0, len(rows) - 1),
            "independenceStatus": "unknown",
            "sourceRelation": "same-content" if len(rows) > 1 else "unresolved",
            "contentSafety": [risk for row in rows for risk in detect_prompt_injection(row.get("body", row.get("content", "")))],
            "numericUnitRisks": [risk for row in rows for risk in detect_numeric_unit_risks(row.get("body", row.get("content", "")))],
        })
    return {"groups": result, "uniqueCount": len(result), "duplicateCount": len(sources) - len(result)}


def validate_citation(quote: str, body: str, locator: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check that a model quote is an exact contiguous substring."""
    if not isinstance(quote, str) or not isinstance(body, str):
        raise ValueError("quote and body must be text")
    if not quote.strip():
        return {"status": "invalid", "reason": "empty-quote", "match": False}
    position = body.find(quote)
    if position < 0:
        return {"status": "rejected", "reason": "quote-not-found", "match": False}
    return {
        "status": "matched",
        "match": True,
        "start": position,
        "end": position + len(quote),
        "locator": locator or {},
    }


def normalize_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize dated event candidates without inventing missing dates."""
    output = []
    for event in events:
        raw = _text(event.get("date") or event.get("observedAt") or event.get("publishedAt"))
        try:
            parsed = (date.fromisoformat(raw) if len(raw) == 10 else datetime.fromisoformat(raw).date()) if raw else None
        except ValueError:
            parsed = None
        output.append({
            **event,
            "normalizedDate": parsed.isoformat() if parsed else None,
            "dateStatus": "known" if parsed else "unknown",
        })
    # Keep the normalized list deterministic; callers separately receive all
    # contradictory identities so no event date is silently selected.
    return sorted(output, key=lambda row: (row["normalizedDate"] is None, row["normalizedDate"] or ""))


def detect_change_points(values: Iterable[float], *, threshold: float = 2.0) -> list[dict[str, Any]]:
    """Detect simple adjacent jumps; deterministic and explainable baseline."""
    rows = list(values)
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("threshold must be positive")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in rows):
        raise ValueError("values must be finite numbers")
    changes = []
    for index in range(1, len(rows)):
        prior, current = float(rows[index - 1]), float(rows[index])
        delta = current - prior
        if abs(delta) >= threshold:
            changes.append({"index": index, "from": prior, "to": current, "delta": delta, "method": "adjacent-delta"})
    return changes
