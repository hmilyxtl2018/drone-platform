"""Validate the additive Agent TBox and runtime catalog independently.

The core validator intentionally keeps the original ``tbox.json`` source of
record. This check makes the extension's cross-file contracts explicit and is
safe to run in CI before importing the package into a context database.
"""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
AGENT_NS = "https://local.ontology/coast-raid-k/agent/1.0.0#"
BASE_NS = "https://local.ontology/coast-raid-k#"


def main() -> int:
    ext = json.loads((ROOT / "agent.extension.json").read_text(encoding="utf-8"))
    base = json.loads((ROOT / "tbox.json").read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "schema.agent.extension.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    terms = list(ext.get("classes", [])) + list(ext.get("objectProperties", [])) + list(ext.get("dataProperties", []))
    ids = [x.get("id") for x in terms]
    if len(ids) != len(set(ids)):
        errors.append("extension term IDs are not unique")
    classes = {x["id"] for x in base.get("classes", [])} | {x["id"] for x in ext.get("classes", [])}
    class_iris = {BASE_NS + x for x in classes if x in {y["id"] for y in base.get("classes", [])}}
    class_iris |= {x["iri"] for x in ext.get("classes", [])}
    known_sources = {x["id"] for x in ext.get("provenance", {}).get("sources", [])} | {"localResearch"}
    for item in terms:
        if item.get("provenance") not in known_sources:
            errors.append(f"{item.get('id')} references unknown provenance")
        for parent in item.get("subClassOf", []):
            if parent not in class_iris:
                errors.append(f"{item.get('id')} has unknown parent {parent}")
    for item in ext.get("objectProperties", []) + ext.get("dataProperties", []):
        if item.get("domain") not in class_iris:
            errors.append(f"{item.get('id')} has unknown domain {item.get('domain')}")
        value_range = item.get("range", "")
        if not value_range.startswith("xsd:") and value_range not in class_iris:
            errors.append(f"{item.get('id')} has unknown range {value_range}")

    catalog = json.loads((ROOT / "agent.instances.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator({"$ref": "#/$defs/AgentInstanceCatalog", "$defs": schema["$defs"]})
    errors.extend(error.message for error in validator.iter_errors(catalog))
    run_id = catalog.get("runId")
    for instance in catalog.get("instances", []):
        if instance.get("runId") != run_id:
            errors.append(f"{instance.get('id')} runId mismatch")
        if not instance.get("iri", "").startswith(AGENT_NS):
            errors.append(f"{instance.get('id')} is outside Agent namespace")
    print("Agent extension valid" if not errors else "Agent extension invalid")
    for error in errors:
        print(f"  - {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
