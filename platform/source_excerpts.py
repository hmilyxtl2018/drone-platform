"""Deterministic, bounded windows into immutable research source text.

Excerpts are separate continuous slices, never a synthetic concatenated body.
The heuristic improves coverage beyond navigation and tables of contents; it
does not establish relevance, completeness, source truth or independence.
"""
from __future__ import annotations

import re
from typing import Any


MAX_SOURCE_CHARACTERS = 5000
MAX_SOURCE_ITEMS = 30
SELECTION_METHOD = "deterministic-object-topic-windows-v2"
EXCERPT_CONTEXT_INSTRUCTION = (
    "正文按BODY_EXCERPTS/excerpts提供，每项text是原始保存正文的一个连续窗口，"
    "startChar/endChar为原文Unicode字符偏移（从0开始，endChar不含）。"
    "只引用单个窗口内连续逐字text；不能跨窗口拼接、补省略号或把相邻窗口当连续原文。"
    "窗口由确定性关键词选择，并非全文阅读，未出现的相反证据或限定条件不能视为不存在。"
)

# Broad public engineering/project terms, deliberately without tactical,
# targeting or operational parameters. Different topics retain head fallback.
_TOPICS = (
    (8, r"interoperab\w*|open[ -](?:(?:system|systems|mission)[ -])?architecture|开放架构|互操作|标准接口|通信标准"),
    (8, r"human\s+(?:oversight|supervis\w*|in[ -]the[ -]loop)|human.{0,12}control|人机监督|人工监督|人工确认"),
    (6, r"\b(?:UAS|UAV|STANAG|MOSA)\b|multi[ -]UAV|un(?:crewed|manned)[ -](?:aircraft|air|system)\w*|无人机|无人航空"),
    (4, r"compatib\w*|standardi[sz]\w*|reliab\w*|availab\w*|certific\w*|安全审查|可靠性|可用率|兼容性"),
    (3, r"demonstrat\w*|evaluat\w*|prototype\w*|maturity|procure\w*|contract\w*|deliver\w*|公开试验|试验评估|成熟度|采购|交付"),
)
_MATCH = re.compile("|".join("(" + pattern + ")" for _, pattern in _TOPICS), re.IGNORECASE)
_LINK = re.compile(r"\[[^\]\n]{0,500}\]\([^\)\n]{0,1200}\)")
_LINK_TARGET = re.compile(r"\]\([^\)\n]{0,4000}\)")
_TOC_LINE = re.compile(r"^\s*.{0,160}(?:\.{3,}|…{2,}|\.\s){1,}\s*\d{1,4}\s*$", re.MULTILINE)
_MEDIA_UI = re.compile(r"audio track|modal dialog|picture-in-picture|font size|text edge style|playback rate|captions settings", re.IGNORECASE)
_RELATED_HEADING = re.compile(r"^\s*#{1,6}\s+(?:discover more|related (?:articles|stories|content)|you may also|更多阅读|相关阅读)\b", re.IGNORECASE | re.MULTILINE)
# Generic designation/context cues select text windows, never assert entities.
# No specific manufacturer, equipment name or topical allowlist is required.
_DESIGNATION = re.compile(r"\b[A-Z][A-Za-z0-9]{0,10}[-–][A-Z]?[0-9]{1,4}[A-Za-z0-9-]*\b")
_OBJECT_CONTEXT = re.compile(r"\b(?:aircraft|UAVs?|UAS|vehicle|platform|sensor|camera|model|product|system)\b|型号|平台|装备|产品", re.IGNORECASE)


def is_discovery_only_material(material: dict[str, Any]) -> bool:
    """Image-search titles/snippets are discovery leads, never factual bodies.

    The marker is immutable collector provenance. Check both the stored and
    top-level shapes without assuming that a nonempty body means image pixels
    or their original publication have been fetched or interpreted.
    """
    provenance = material.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    return (any(str(container.get("contentKind") or "").casefold() == "image-search-metadata"
                for container in (material, provenance))
            or any(str(container.get("retrievalKind") or "").casefold() == "search-discovery-only"
                   for container in (material, provenance)))


def _window(body: str, position: int, width: int = 1500) -> tuple[int, int]:
    start = max(0, position - width // 2)
    end = min(len(body), start + width)
    start = max(0, end - width)
    # Preserve complete paragraphs/lines when this costs no extra budget.
    next_line = body.find("\n", start, min(position, start + 180))
    if start and next_line >= 0:
        start = next_line + 1
    previous_line = body.rfind("\n", max(position + 1, end - 180), end)
    if end < len(body) and previous_line > position:
        end = previous_line
    return start, end


def _score(text: str) -> float:
    counts = [0] * len(_TOPICS)
    # Search strings inside URL targets are navigation, not source prose.
    # Keep displayed labels intact; returned excerpts remain exact source text.
    visible_text = _LINK_TARGET.sub("]", text)
    for match in _MATCH.finditer(visible_text):
        counts[match.lastindex - 1] += 1
    score = sum(weight * min(3, count) for (weight, _), count in zip(_TOPICS, counts))
    if _OBJECT_CONTEXT.search(visible_text) and _DESIGNATION.search(visible_text):
        score += 10
    # Link directories and dotted contents entries may repeat the keywords many
    # times without supplying factual sentences. Keep them eligible as context,
    # but prefer prose about the subject elsewhere in the same saved source.
    link_ratio = sum(len(match.group()) for match in _LINK.finditer(text)) / max(1, len(text))
    toc_ratio = sum(len(match.group()) for match in _TOC_LINE.finditer(text)) / max(1, len(text))
    if link_ratio > 0.35:
        score *= max(0.03, 1.0 - link_ratio) * 0.2
    if toc_ratio > 0.2:
        score *= 0.1
    if len(_MEDIA_UI.findall(text)) >= 2:
        score *= 0.03
    return score


def select_source_excerpts(body: str, *, max_characters: int = MAX_SOURCE_CHARACTERS) -> dict[str, Any]:
    """Return ≤budget characters as ordered exact slices with source offsets.

    All input characters count toward the budget, including whitespace. Long
    sources retain a small opening window and at most three topical windows;
    unmatched documents retain their original bounded-head behavior.
    """
    body = str(body or "")
    budget = max(0, min(MAX_SOURCE_CHARACTERS, int(max_characters)))
    if not body or not budget:
        spans = []
    elif len(body) <= budget:
        spans = [(0, len(body))]
    else:
        positions = [match.start() for match in list(_MATCH.finditer(body))[:2000]]
        positions.extend(match.start() for match in list(_DESIGNATION.finditer(body))[:200])
        candidates = set(_window(body, position, min(1500, budget)) for position in positions)
        related = _RELATED_HEADING.search(body)
        related_start = related.start() if related else len(body)
        def candidate_score(span: tuple[int, int]) -> float:
            start, end = span
            score = _score(body[start:end])
            # Recommendations and related-story cards must not displace the
            # article's named object, attribute and controlled-test paragraphs.
            if start >= related_start:
                score *= 0.05
            elif end > related_start:
                score *= max(0.05, (related_start - start) / (end - start))
            return score
        ranked = sorted(candidates, key=lambda span: (-candidate_score(span), span[0], span[1]))
        if not ranked or candidate_score(ranked[0]) <= 0:
            spans = [(0, budget)]
        else:
            spans = []
            remaining = budget
            for start, end in ranked:
                if len(spans) >= 3 or (spans and remaining < 300):
                    break
                if candidate_score((start, end)) < max(1.0, candidate_score(ranked[0]) * 0.2):
                    break
                if any(start < old_end and end > old_start for old_start, old_end in spans):
                    continue
                end = min(end, start + remaining)
                spans.append((start, end))
                remaining -= end - start
            # Opening context is kept separately; it cannot bridge a quotation.
            head_end = min(500, remaining, min((start for start, _ in spans), default=0))
            if head_end:
                spans.append((0, head_end))
            spans.sort()
    excerpts = [{"startChar": start, "endChar": end, "text": body[start:end]} for start, end in spans]
    count = sum(len(excerpt["text"]) for excerpt in excerpts)
    return {"selectionMethod": SELECTION_METHOD, "sourceCharacters": len(body),
            "selectedCharacters": count, "complete": count == len(body), "excerpts": excerpts}


def selected_quote_offset(body: str, selection: dict[str, Any], quote: Any) -> int | None:
    """Locate a quote inside one sent slice, then recheck the original body."""
    if not isinstance(quote, str) or not quote.strip():
        return None
    for excerpt in selection.get("excerpts", []):
        relative = excerpt["text"].find(quote)
        if relative >= 0:
            start = excerpt["startChar"] + relative
            if body[start:start + len(quote)] == quote:
                return start
    return None


def recover_selected_quote(body: str, selection: dict[str, Any], quote: Any) -> tuple[int, str] | None:
    """Recover only whitespace runs inside one sent window as exact source text.

    Words, case, punctuation and whitespace boundaries remain significant. The
    return value is always a continuous slice of the immutable original body.
    Adjacent or separated windows are never joined to make a match.
    """
    exact = selected_quote_offset(body, selection, quote)
    if exact is not None:
        return exact, quote
    if not isinstance(quote, str) or not quote.strip():
        return None
    normalized_quote = re.sub(r"\s+", " ", quote).strip()
    for excerpt in selection.get("excerpts", []):
        tokens = list(re.finditer(r"\s+|\S", excerpt["text"]))
        normalized_text = "".join(" " if token.group().isspace() else token.group() for token in tokens)
        position = normalized_text.find(normalized_quote)
        if position < 0:
            continue
        relative_start = tokens[position].start()
        relative_end = tokens[position + len(normalized_quote) - 1].end()
        canonical = excerpt["text"][relative_start:relative_end]
        start = excerpt["startChar"] + relative_start
        if body[start:start + len(canonical)] == canonical:
            return start, canonical
    return None
