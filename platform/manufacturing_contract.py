"""Validation for the versioned UAV manufacturing extraction contract."""
from __future__ import annotations
import json
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1] / 'ontology' / 'coast-raid-k'
SCHEMA_PATH = ROOT / 'manufacturing-extraction.schema.json'

def validate_document(document: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    validator = Draft202012Validator(schema)
    return [f"{'.'.join(str(x) for x in error.absolute_path) or '$'}: {error.message}" for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))]

def assert_valid(document: dict) -> dict:
    errors = validate_document(document)
    if errors:
        raise ValueError('manufacturing extraction contract invalid: ' + '; '.join(errors))
    return document
