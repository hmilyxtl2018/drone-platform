"""Validation and deterministic hashing for the evidence factor graph contract."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "ontology" / "coast-raid-k" / "evidence-factor-graph.schema.json"
SCHEMA_VERSION = "evidence-factor-graph.v1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any, field: str = "contentHash") -> str:
    payload = copy.deepcopy(value)
    if isinstance(payload, dict):
        payload.pop(field, None)
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def validate_document(document: dict[str, Any]) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = [
        f"{'.'.join(str(x) for x in error.absolute_path) or '$'}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    ]
    if errors or not isinstance(document, dict):
        return errors

    if document.get("contentHash") != content_hash(document):
        errors.append("$: contentHash does not match canonical graph document")
    for section, id_key in (("variables", "variableId"), ("observations", "observationId"), ("factors", "factorId"), ("constraints", "constraintId")):
        ids = [row.get(id_key) for row in document.get(section, []) if isinstance(row, dict)]
        if len(ids) != len(set(ids)):
            errors.append(f"{section}: duplicate IDs are not allowed")
        for index, row in enumerate(document.get(section, [])):
            if isinstance(row, dict) and row.get("contentHash") != content_hash(row):
                errors.append(f"{section}.{index}: contentHash does not match row")

    variable_ids = {row.get("variableId") for row in document.get("variables", [])}
    observation_ids = {row.get("observationId") for row in document.get("observations", [])}
    for index, row in enumerate(document.get("factors", [])):
        if not set(row.get("variableIds", [])).issubset(variable_ids):
            errors.append(f"factors.{index}: references an unknown variable")
        if not set(row.get("observationIds", [])).issubset(observation_ids):
            errors.append(f"factors.{index}: references an unknown observation")
    for index, row in enumerate(document.get("constraints", [])):
        if not set(row.get("variableIds", [])).issubset(variable_ids):
            errors.append(f"constraints.{index}: references an unknown variable")
    return errors


def assert_valid(document: dict[str, Any]) -> dict[str, Any]:
    errors = validate_document(document)
    if errors:
        raise ValueError("evidence factor graph contract invalid: " + "; ".join(errors))
    return document


def with_content_hash(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    for section, id_key in (("variables", "variableId"), ("observations", "observationId"), ("factors", "factorId"), ("constraints", "constraintId")):
        for row in result.get(section, []):
            row["contentHash"] = content_hash(row)
    result["contentHash"] = content_hash(result)
    return assert_valid(result)


__all__ = ["SCHEMA_VERSION", "assert_valid", "content_hash", "validate_document", "with_content_hash"]
