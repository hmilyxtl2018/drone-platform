"""Source-bound public equipment indicators, separate from combat predictions."""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit


BASES = {"reported-test", "manufacturer-claim", "program-target", "reported-observation"}
QUALIFIERS = {"exact", "approximately", "at-least", "at-most", "greater-than", "less-than"}
SCOPE = "公开装备参数与测试、保障指标；数值和引文定位已核对，指标语义及数据性质待人工核验。公开参数不能直接等同实战效果，不生成战力总分或交战效果预测。"
LIMITS = [
    "不同型号、试验条件和数据性质的指标不可直接排名或合成为战力评分。",
    "报道测试值不代表独立验证；厂商声明与计划目标不代表已形成能力。",
    "缺少测试条件、样本量或多期数据时，不计算成功率、可靠性区间或增长率；缺失数据不记为零。",
]
NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?!\w|\.\d|,\d)")


def _unit_in_quote(unit: str, quote: str) -> bool:
    # 'ratio' must not match the middle of 'Operations'.
    return bool(re.search(r"(?<![a-z])" + re.escape(unit.strip()) + r"(?![a-z])", quote, re.IGNORECASE))


def _number_in_quote(value: Any, quote: str) -> bool:
    try:
        if type(value) not in (int, float) or not math.isfinite(value):
            return False
        return Decimal(str(value)) in {Decimal(token.replace(",", "")) for token in NUMBER.findall(quote)}
    except (InvalidOperation, ValueError, OverflowError):
        return False


def _stats(metrics: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "metricCount": len(metrics),
        "equipmentCount": len({m["equipment"] for m in metrics}),
        "citedSourceCount": len({m["evidence"]["itemId"] for m in metrics}),
        **{key: sum(m["basis"] == basis for m in metrics) for key, basis in (
            ("reportedTestCount", "reported-test"), ("manufacturerClaimCount", "manufacturer-claim"),
            ("programTargetCount", "program-target"), ("reportedObservationCount", "reported-observation"),
        )},
    }


def empty_capabilities(reason: str = "尚未提取到可核对的公开能力指标，需补充包含数字、单位和测试条件的材料。") -> dict[str, Any]:
    return {"version": 1, "scope": SCOPE, "provider": "none", "model": None, "metrics": [], "statistics": _stats([]), "gaps": [reason, *LIMITS]}


def attach_capabilities(summary: dict[str, Any], capabilities: dict[str, Any], materials: list[dict[str, Any]]) -> None:
    summary["capabilityAssessment"] = capabilities
    cited = {ref for f in summary.get("findings", []) for ref in f.get("evidenceIds", [])}
    cited.update(m["evidence"]["itemId"] for m in capabilities["metrics"])
    publishers = {urlsplit(m.get("sourceUri", "")).netloc.casefold() for m in materials if m.get("itemId") in cited}
    summary.setdefault("statistics", {}).update({"citedItems": len(cited), "citedPublisherCount": len(publishers - {""})})


def normalize_capabilities(response: Any, materials: list[dict[str, Any]]) -> dict[str, Any]:
    """Accept only exact source substrings with the reported number and unit.

    Locators and provenance come from the server snapshot, never from the LLM.
    This validates transcription, not the semantic interpretation of a metric.
    """
    result = empty_capabilities()
    rows = response.get("metrics") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        return result
    sources = {str(m.get("itemId") or m.get("id")): m for m in materials}
    metrics, seen = [], set()
    rejected = 0
    for row in rows[:24]:
        if not isinstance(row, dict):
            rejected += 1
            continue
        source_id = str(row.get("itemId") or "")
        source = sources.get(source_id)
        quote, unit = row.get("quote"), row.get("unit")
        required = ("equipment", "dimension", "metric", "conditions", "assessment")
        if (source is None or not isinstance(quote, str) or not 10 <= len(quote) <= 1200
                or not isinstance(unit, str) or not unit.strip() or len(unit) > 80
                or row.get("basis") not in BASES or row.get("qualifier") not in QUALIFIERS
                or any(not isinstance(row.get(k), str) or not row[k].strip() or len(row[k]) > 800 for k in required)
                or not _number_in_quote(row.get("value"), quote) or not _unit_in_quote(unit, quote)):
            rejected += 1
            continue
        body = str(source.get("body") or "")
        start = body.find(quote)
        if start < 0:
            rejected += 1
            continue
        unit = unit.strip()
        key = (source_id, row["equipment"].strip(), row["metric"].strip(), row["value"], unit, quote)
        if key in seen:
            continue
        seen.add(key)
        metrics.append({
            "id": f"metric-{len(metrics) + 1}",
            **{k: row[k].strip() for k in required},
            "value": row["value"], "unit": unit, "basis": row["basis"], "qualifier": row["qualifier"],
            "evidence": {
                "itemId": source_id, "quote": quote, "startChar": start, "endChar": start + len(quote),
                "sourceUri": source["sourceUri"], "sourceVersion": str(source.get("sourceVersion") or ""),
                "contentHash": source["contentHash"], "publishedAt": source.get("publishedAt"),
            },
        })
    result["metrics"] = metrics
    result["statistics"] = _stats(metrics)
    result["gaps"] = [] if metrics else [result["gaps"][0]]
    if rejected:
        result["gaps"].append(f"{rejected} 条提议因数字、单位、引文或字段校验未通过而未纳入。")
    raw_gaps = response.get("gaps")
    if isinstance(raw_gaps, list):
        result["gaps"].extend(str(g)[:600] for g in raw_gaps[:8] if isinstance(g, str) and g.strip())
    result["gaps"].extend(LIMITS)
    return result


def validate_capabilities(capabilities: Any, materials: list[dict[str, Any]]) -> None:
    """Recheck persisted indicators at the store boundary, including offsets."""
    if not isinstance(capabilities, dict) or type(capabilities.get("version")) is not int or capabilities["version"] != 1:
        raise ValueError("capabilityAssessment version must be 1")
    if not isinstance(capabilities.get("scope"), str) or not capabilities["scope"].strip():
        raise ValueError("capabilityAssessment scope is required")
    if not isinstance(capabilities.get("gaps"), list) or any(not isinstance(g, str) or not g.strip() for g in capabilities["gaps"]):
        raise ValueError("capabilityAssessment gaps must be strings")
    if "provider" in capabilities and (not isinstance(capabilities["provider"], str) or not capabilities["provider"].strip()):
        raise ValueError("capability provider must be a string")
    if capabilities.get("model") is not None and not isinstance(capabilities["model"], str):
        raise ValueError("capability model must be a string or null")
    metrics = capabilities.get("metrics")
    if not isinstance(metrics, list) or len(metrics) > 24:
        raise ValueError("capability metrics must be a bounded array")
    sources = {str(m.get("itemId") or m.get("id")): m for m in materials}
    ids = set()
    for m in metrics:
        if not isinstance(m, dict) or not isinstance(m.get("evidence"), dict):
            raise ValueError("capability metric requires evidence")
        evidence = m["evidence"]
        source = sources.get(evidence.get("itemId"))
        if not source:
            raise ValueError("capability refers to an unknown source")
        start, end = evidence.get("startChar"), evidence.get("endChar")
        body = str(source.get("body") or "")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(body) or body[start:end] != evidence.get("quote"):
            raise ValueError("capability quote and character range do not match the source snapshot")
        for key in ("sourceUri", "sourceVersion", "contentHash", "publishedAt"):
            if evidence.get(key) != source.get(key):
                raise ValueError("capability provenance does not match the source snapshot")
        row = {**m, "itemId": evidence["itemId"], "quote": evidence["quote"]}
        normalized = normalize_capabilities({"metrics": [row]}, materials)["metrics"]
        if not normalized or not isinstance(m.get("id"), str) or not m["id"] or m["id"] in ids:
            raise ValueError("capability number, unit or required fields are not supported by the quote")
        ids.add(m["id"])
    statistics = capabilities.get("statistics")
    if not isinstance(statistics, dict) or any(type(v) is not int for v in statistics.values()) or statistics != _stats(metrics):
        raise ValueError("capability statistics do not match the evidence ledger")
