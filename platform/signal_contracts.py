"""Contracts for signal observations and evidence-backed capability claims.

The API keeps model proposals separate from published ontology facts.  This
module is intentionally provider-agnostic: Firecrawl/DeepSeek may produce
candidate records, while the audit/review workflow owns verification and
publication.  Records are validated against the checked-in Draft 2020-12
schema before they are persisted.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1] / "ontology" / "coast-raid-k"
SCHEMA_PATH = ROOT / "schema.signal-intelligence.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class SignalContractError(ValueError):
    """Raised when a signal/evidence contract is malformed."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(record: Mapping[str, Any]) -> str:
    """Hash a record without its self-referential ``contentHash`` field."""

    payload = {key: value for key, value in record.items() if key != "contentHash"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def validate_record(record: Mapping[str, Any], *, require_hash: bool = True) -> dict[str, Any]:
    """Validate and return a defensive copy of a contract record.

    ``contentHash`` is required by default and must be the SHA-256 digest of
    the canonical record with that field removed.  Set ``require_hash=False``
    for an inbound candidate before calling :func:`with_content_hash`.
    """

    if not isinstance(record, Mapping):
        raise SignalContractError("signal contract must be a JSON object")
    value = copy.deepcopy(dict(record))
    if not require_hash:
        value.pop("contentHash", None)
    errors = sorted(_VALIDATOR.iter_errors(value), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(f"{'/'.join(map(str, error.path)) or '$'}: {error.message}" for error in errors[:8])
        raise SignalContractError(details)
    expected = content_hash(value)
    if require_hash and value.get("contentHash", "").lower() != expected:
        raise SignalContractError("contentHash does not match the canonical record")
    _validate_semantics(value)
    return value


def with_content_hash(record: Mapping[str, Any]) -> dict[str, Any]:
    """Fill a deterministic hash and validate a new candidate record."""

    value = copy.deepcopy(dict(record))
    value.pop("contentHash", None)
    value["contentHash"] = content_hash(value)
    return validate_record(value)


def record_type(record: Mapping[str, Any]) -> str:
    kind = str(record.get("recordType") or "").strip()
    if kind not in {"SignalObservation", "EmitterSignature", "CapabilityClaim", "EvidenceBundle", "VerificationRun"}:
        raise SignalContractError("recordType must identify a supported signal contract")
    return kind


def _validate_semantics(value: Mapping[str, Any]) -> None:
    """Rules that are easier to express in code than in JSON Schema."""

    kind = record_type(value)
    refs = value.get("sourceRefs", [])
    if kind in {"SignalObservation", "EmitterSignature"} and not refs:
        raise SignalContractError(f"{kind} requires at least one sourceRefs entry")
    if kind == "CapabilityClaim":
        if value.get("reviewStatus") in {"accepted", "published"} and value.get("evidenceLevel") in {"E0", "E1"}:
            raise SignalContractError("accepted/published capability claims require evidence level E2 or higher")
        if value.get("claimState") == "unknown" and value.get("normalizedValue") is not None:
            raise SignalContractError("unknown capability claims cannot contain normalizedValue")
        if value.get("valueType") == "range" and not isinstance(value.get("normalizedValue"), Mapping):
            # A range may be left un-normalized while under review, but a
            # normalized range must carry lower/upper bounds.
            if value.get("reviewStatus") in {"accepted", "published"}:
                raise SignalContractError("accepted/published range claims require normalized lower/upper bounds")
    if kind == "EvidenceBundle":
        if not (value.get("evidenceIds") or value.get("observationIds") or value.get("claimIds")):
            raise SignalContractError("evidence bundle must reference at least one evidence, observation or claim")
        if value.get("verificationStatus") == "verified" and not value.get("sourceIndependence"):
            raise SignalContractError("verified evidence bundles require source independence metadata")
    if kind == "VerificationRun" and value.get("status") == "completed" and not value.get("checks"):
        raise SignalContractError("completed verification runs require checks")


def validate_records(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate a batch and preserve input order."""

    if not isinstance(records, list):
        raise SignalContractError("records must be an array")
    return [validate_record(record) for record in records]


def db_json(value: Any) -> str:
    """Canonical JSON for SQLite TEXT / PostgreSQL JSONB adapters."""

    return canonical_json(value if value is not None else {})


__all__ = [
    "SCHEMA_PATH",
    "SignalContractError",
    "canonical_json",
    "content_hash",
    "db_json",
    "record_type",
    "validate_record",
    "validate_records",
    "with_content_hash",
]
