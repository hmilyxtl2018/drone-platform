"""Validation helpers for the canonical object ``attributes`` JSON document.

The JSON Schema validates shape and primitive types.  This module adds the
cross-field checks that JSON Schema cannot express reliably: completeness set
accounting, bucket cardinality, evidence presence, and the publication gate.
It intentionally treats the document as a projection of immutable assertion
and evidence records; it never mutates or publishes a document.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1] / "ontology" / "coast-raid-k"
SCHEMA_PATH = ROOT / "schema.object-attributes-jsonb.json"


class AttributesValidationError(ValueError):
    """Raised when a JSONB attributes envelope is structurally or semantically invalid."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def canonical_attributes_hash(document: dict[str, Any]) -> str:
    """Return the content hash over canonical JSON, excluding provenance hash."""
    copy = json.loads(json.dumps(document, ensure_ascii=False))
    provenance = copy.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("contentHash", None)
    payload = json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_attributes_jsonb(
    document: dict[str, Any],
    *,
    attribute_registry: set[str] | None = None,
    require_publication: bool = False,
) -> list[str]:
    """Return validation errors; raise :class:`AttributesValidationError` on request.

    ``attribute_registry`` is supplied by the entity-type template.  Without
    it, the function still checks shape, evidence, state/value cardinality,
    key accounting, and the coverage formula.  ``require_publication`` adds
    the human-review gate for every supplied value.
    """
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["document must be an object"]
    validator = Draft202012Validator(_schema(), format_checker=FormatChecker())
    errors.extend(error.message for error in validator.iter_errors(document))
    if errors:
        return sorted(set(errors))

    fields = document["attributes"]
    complete = document["completeness"]
    expected = set(complete["expectedKeys"])
    present = set(complete["presentKeys"])
    unknown = set(complete["unknownKeys"])
    not_applicable = set(complete["notApplicableKeys"])
    conflicts = set(complete["conflictKeys"])
    groups = {"presentKeys": present, "unknownKeys": unknown,
              "notApplicableKeys": not_applicable}
    if conflicts - present:
        errors.append(f"conflictKeys must be a subset of presentKeys: {sorted(conflicts - present)}")
    for key in conflicts:
        if key not in fields:
            errors.append(f"conflictKeys contains a missing attribute bucket: {key}")
    for name, values in groups.items():
        outside = values - expected
        if outside:
            errors.append(f"{name} contains keys outside expectedKeys: {sorted(outside)}")
    names = list(groups)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1:]:
            overlap = groups[left_name] & groups[right_name]
            if overlap:
                errors.append(f"completeness key sets overlap: {left_name}/{right_name}: {sorted(overlap)}")
    union = present | unknown | not_applicable
    if complete["mode"] == "template" and union != expected:
        errors.append("template completeness requires expectedKeys to equal the four status key sets")
    if set(fields) - expected:
        errors.append(f"attributes contains keys outside expectedKeys: {sorted(set(fields) - expected)}")
    if attribute_registry is not None:
        unknown_keys = expected - attribute_registry
        if unknown_keys:
            errors.append(f"expectedKeys contains unregistered attributes: {sorted(unknown_keys)}")

    expected_coverage = (len(present) / len(expected)) if expected else 1.0
    if abs(float(complete["coverageRatio"]) - expected_coverage) > 1e-9:
        errors.append("coverageRatio does not equal presentKeys / expectedKeys")

    for key, bucket in fields.items():
        state = bucket["state"]
        values = bucket["values"]
        if state in {"unknown", "not-applicable", "withheld", "rejected"} and values:
            errors.append(f"{key}: {state} bucket must have no values")
        if state in {"reported", "verified", "inferred"} and not values:
            errors.append(f"{key}: {state} bucket requires at least one value")
        if state == "conflict" and len(values) < 2:
            errors.append(f"{key}: conflict bucket requires at least two values")
        for value in values:
            if not str(value.get("rawValue", "")).strip():
                errors.append(f"{key}/{value.get('assertionId')}: rawValue is empty")
            if not value.get("evidenceRefs"):
                errors.append(f"{key}/{value.get('assertionId')}: evidenceRefs is empty")
            status = value.get("reviewStatus")
            if status == "accepted":
                if value.get("truthVerified") is not True or value.get("verifiedConfidence") != "high":
                    errors.append(f"{key}/{value.get('assertionId')}: accepted value requires high verified confidence")
            if require_publication and status != "accepted":
                errors.append(f"{key}/{value.get('assertionId')}: publication requires accepted reviewStatus")
            if require_publication and value.get("claimKind") == "inference":
                errors.append(f"{key}/{value.get('assertionId')}: inferred claims cannot be published as facts")
    return sorted(set(errors))


def assert_valid_attributes_jsonb(
    document: dict[str, Any],
    *,
    attribute_registry: set[str] | None = None,
    require_publication: bool = False,
) -> dict[str, Any]:
    errors = validate_attributes_jsonb(document, attribute_registry=attribute_registry,
                                       require_publication=require_publication)
    if errors:
        raise AttributesValidationError(errors)
    return document


__all__ = [
    "AttributesValidationError",
    "assert_valid_attributes_jsonb",
    "canonical_attributes_hash",
    "validate_attributes_jsonb",
]
