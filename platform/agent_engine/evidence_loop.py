"""Serializable research-loop progress based on deterministic evidence identities."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
import hashlib
import json
import re


class EvidenceLoopError(ValueError):
    pass


def _hash(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise EvidenceLoopError("loop-input-not-json") from None
    return hashlib.sha256(encoded).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None


@dataclass(frozen=True)
class LoopDecision:
    state: dict
    should_continue: bool
    reason: str
    closed_gap_ids: tuple[str, ...]
    open_gap_ids: tuple[str, ...]
    requires_human_review: bool
    progress: dict

    def to_dict(self):
        return asdict(self)


class EvidenceLoop:
    """At most two supplemental rounds; a failed round never resets history.

    All evidence/candidates are deterministic-normalizer outputs, not model JSON.
    Human/verification flags are read only from trusted_review_states, which the
    caller must build from its authenticated repository, never from model output.
    Each evaluate consumes one completed supplemental round. start sets baseline.
    """
    def __init__(self, max_rounds=2, no_progress_limit=1):
        if type(max_rounds) is not int or not 1 <= max_rounds <= 2:
            raise EvidenceLoopError("loop-round-limit-invalid")
        if type(no_progress_limit) is not int or not 1 <= no_progress_limit <= 2:
            raise EvidenceLoopError("loop-no-progress-limit-invalid")
        self.max_rounds, self.no_progress_limit = max_rounds, no_progress_limit

    def _measure(self, evidence, candidates, gaps, conflicts, trusted_review_states):
        if any(not isinstance(items, list) or len(items) > 2000 for items in (evidence, candidates, gaps, conflicts)):
            raise EvidenceLoopError("loop-collection-invalid")
        if not isinstance(trusted_review_states, dict):
            raise EvidenceLoopError("trusted-review-states-invalid")
        gap_pairs = {(gap.get("entityId"), gap.get("metric")) for gap in gaps if isinstance(gap, dict)}
        snapshots = {}
        source_fingerprints = set()
        for source in evidence:
            if not isinstance(source, dict):
                continue
            artifact_id, digest = source.get("artifactId"), source.get("contentHash")
            if not isinstance(artifact_id, str) or not artifact_id or not _digest(digest):
                continue
            text = source.get("markdown")
            if isinstance(text, str):
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
                    continue
                # Whitespace-only changes and mirrored text do not count as new evidence.
                identity = hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()
            else:
                identity = digest.lower()
            # One ontology artifact contains many independently quoted cells.
            # Keying only by artifact would discard all but the last cell.
            snapshots[(artifact_id, digest)] = source
            source_fingerprints.add(identity)
        valid, candidate_fingerprints = [], set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("citationValidated") is not True:
                continue
            if gaps and (candidate.get("entityId"), candidate.get("metric")) not in gap_pairs:
                continue
            source = snapshots.get((candidate.get("sourceArtifactId"), candidate.get("contentHash")))
            if source is None or candidate.get("contentHash") != source.get("contentHash"):
                continue
            quote, start, end = candidate.get("quote"), candidate.get("startOffset"), candidate.get("endOffset")
            if (not isinstance(quote, str) or not quote or type(start) is not int or type(end) is not int
                    or not 0 <= start < end or end - start != len(quote)):
                continue
            if isinstance(source.get("markdown"), str) and source["markdown"][start:end] != quote:
                continue
            candidate_id = candidate.get("candidateId")
            if not isinstance(candidate_id, str) or not candidate_id:
                continue
            trusted = trusted_review_states.get(candidate_id, {})
            if not isinstance(trusted, dict):
                trusted = {}
            if trusted.get("contentHash") and trusted["contentHash"] != candidate.get("contentHash"):
                trusted = {}
            if trusted.get("candidateHash") and trusted["candidateHash"] != _hash(candidate):
                trusted = {}
            # Identity omits candidate/source IDs, review status and generated prose.
            identity = _hash({key: candidate.get(key) for key in
                             ("entityId", "metric", "value", "range", "unit", "qualifier", "conditions", "configuration", "variant", "observedAt", "quote")})
            candidate_fingerprints.add(identity)
            valid.append((candidate, trusted))
        resolved, blocked_pairs, open_conflicts = set(), set(), []
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            cid = conflict.get("conflictId") or _hash({key: conflict.get(key) for key in ("entityId", "metric", "candidateIds")})
            trusted = trusted_review_states.get("conflict:" + cid, {})
            if isinstance(trusted, dict) and trusted.get("status") == "resolved" and trusted.get("reviewStatus") == "approved":
                resolved.add(cid)
            else:
                blocked_pairs.add((conflict.get("entityId"), conflict.get("metric")))
                open_conflicts.append(cid)
        ids, closed, open_critical, open_all = set(), set(), set(), set()
        for gap in gaps:
            if not isinstance(gap, dict) or not isinstance(gap.get("gapId"), str) or not gap["gapId"]:
                raise EvidenceLoopError("gap-identity-required")
            gid = gap["gapId"]
            if gid in ids:
                raise EvidenceLoopError("duplicate-gap-identity")
            ids.add(gid)
            if not isinstance(gap.get("entityId"), str) or not isinstance(gap.get("metric"), str):
                raise EvidenceLoopError("gap-scope-required")
            for flag in ("critical", "requiresVerified", "requiresApprovedReview"):
                if flag in gap and type(gap[flag]) is not bool:
                    raise EvidenceLoopError("gap-policy-invalid")
            satisfied = False
            if (gap["entityId"], gap["metric"]) not in blocked_pairs:
                for candidate, trusted in valid:
                    if candidate.get("entityId") != gap["entityId"] or candidate.get("metric") != gap["metric"]:
                        continue
                    if candidate.get("value") is None and not candidate.get("range"):
                        continue
                    if candidate.get("qualifier") in {"unknown", "conflicting"}:
                        continue
                    verified = (trusted.get("truthVerified") is True and trusted.get("verificationStatus") == "verified"
                                and trusted.get("reviewStatus") == "approved")
                    if gap.get("requiresVerified", True) and not verified:
                        continue
                    if gap.get("requiresApprovedReview", True) and trusted.get("reviewStatus") != "approved":
                        continue
                    satisfied = True
                    break
            if satisfied:
                closed.add(gid)
            else:
                open_all.add(gid)
                if gap.get("critical", True):
                    open_critical.add(gid)
        pending = bool(open_conflicts) or any(trusted.get("reviewStatus") != "approved" for _, trusted in valid)
        return {"sourceFingerprints": sorted(source_fingerprints), "candidateFingerprints": sorted(candidate_fingerprints),
                "closedGapIds": sorted(closed), "openGapIds": sorted(open_all), "criticalOpenGapIds": sorted(open_critical),
                "resolvedConflictIds": sorted(resolved), "pendingReview": pending, "gapPolicyHash": _hash(gaps)}

    def start(self, *, evidence=None, candidates=None, gaps=None, conflicts=None, trusted_review_states=None):
        measurement = self._measure(evidence or [], candidates or [], gaps or [], conflicts or [], trusted_review_states or {})
        return {"schemaVersion": "1", "round": 0, "maxRounds": self.max_rounds,
                "noProgressLimit": self.no_progress_limit, "noProgressRounds": 0,
                "baseline": measurement, "seenSources": measurement["sourceFingerprints"],
                "seenCandidates": measurement["candidateFingerprints"], "seenClosedGaps": measurement["closedGapIds"],
                "seenResolvedConflicts": measurement["resolvedConflictIds"], "stopped": False, "stopReason": None}

    def evaluate(self, state, *, evidence=None, candidates=None, gaps=None, conflicts=None,
                 trusted_review_states=None, remaining_budget=None) -> LoopDecision:
        if (not isinstance(state, dict) or state.get("schemaVersion") != "1"
                or type(state.get("round")) is not int or not 0 <= state["round"] <= self.max_rounds
                or state.get("maxRounds") != self.max_rounds or state.get("noProgressLimit") != self.no_progress_limit
                or not isinstance(state.get("baseline"), dict)):
            raise EvidenceLoopError("loop-checkpoint-invalid")
        if state.get("stopped"):
            baseline = state["baseline"]
            return LoopDecision(deepcopy(state), False, state.get("stopReason") or "already-stopped",
                                tuple(baseline["closedGapIds"]), tuple(baseline["openGapIds"]),
                                baseline["pendingReview"], {"newSources": 0, "newCandidates": 0, "closedCriticalGaps": 0, "resolvedConflicts": 0})
        current = self._measure(evidence or [], candidates or [], gaps or [], conflicts or [], trusted_review_states or {})
        if state["baseline"].get("gapPolicyHash") != current["gapPolicyHash"]:
            raise EvidenceLoopError("gap-policy-changed-during-loop")
        previous = state["baseline"]
        new_sources = set(current["sourceFingerprints"]) - set(state.get("seenSources", []))
        new_candidates = set(current["candidateFingerprints"]) - set(state.get("seenCandidates", []))
        closed_gaps = (set(previous["criticalOpenGapIds"]) - set(current["criticalOpenGapIds"])) - set(state.get("seenClosedGaps", []))
        resolved = set(current["resolvedConflictIds"]) - set(state.get("seenResolvedConflicts", []))
        # New raw prose alone is not progress. A source counts only when it
        # supports a new valid candidate; candidate/status/closure changes count.
        progressed = bool(new_candidates or closed_gaps or resolved)
        updated = deepcopy(state)
        updated.update(round=state["round"] + 1, baseline=current,
                       noProgressRounds=0 if progressed else state.get("noProgressRounds", 0) + 1,
                       seenSources=sorted(set(state.get("seenSources", [])) | set(current["sourceFingerprints"])),
                       seenCandidates=sorted(set(state.get("seenCandidates", [])) | set(current["candidateFingerprints"])),
                       seenClosedGaps=sorted(set(state.get("seenClosedGaps", [])) | set(current["closedGapIds"])),
                       seenResolvedConflicts=sorted(set(state.get("seenResolvedConflicts", [])) | set(current["resolvedConflictIds"])))
        if remaining_budget is not None and (not isinstance(remaining_budget, dict)
                or any(type(value) is not int or value < 0 for value in remaining_budget.values())):
            raise EvidenceLoopError("remaining-budget-invalid")
        exhausted = remaining_budget is not None and any(remaining_budget.get(key, 0) <= 0 for key in ("model_calls", "tool_calls", "http_calls"))
        if not current["criticalOpenGapIds"]:
            reason = "review-required" if current["pendingReview"] else "evidence-sufficient"
        elif exhausted:
            reason = "budget-exhausted"
        elif updated["round"] >= self.max_rounds:
            reason = "maximum-rounds-reached"
        elif updated["noProgressRounds"] >= self.no_progress_limit:
            reason = "no-new-evidence"
        else:
            reason = "continue-targeted-research"
        continuing = reason == "continue-targeted-research"
        updated.update(stopped=not continuing, stopReason=None if continuing else reason)
        progress = {"newSources": len(new_sources) if new_candidates else 0, "newCandidates": len(new_candidates),
                    "closedCriticalGaps": len(closed_gaps), "resolvedConflicts": len(resolved)}
        return LoopDecision(updated, continuing, reason, tuple(current["closedGapIds"]),
                            tuple(current["openGapIds"]), current["pendingReview"], progress)


__all__ = ["EvidenceLoop", "EvidenceLoopError", "LoopDecision"]
