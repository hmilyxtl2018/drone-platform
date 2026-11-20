"""Attach reviewed, published intelligence assertions to the KB boot view."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from typing import Any


def attach_intelligence_ontology(boot: dict[str, Any], layer: dict[str, Any], ontology_root: Path) -> dict[str, Any]:
    result = copy.deepcopy(boot)
    files = result.setdefault("files", {})
    entries = result.setdefault("inventory", {}).setdefault("entries", [])
    vocabulary = json.loads((ontology_root / "tbox.json").read_text(encoding="utf-8"))
    tbox = files.setdefault("tbox.json", {"classes": []})
    # Only the added vocabulary is imported; all existing terms keep the
    # versioned base snapshot as their source of truth.
    additions = [term for term in vocabulary.get("classes", [])
                 if "intelligence-contract.json" in str(term.get("definitionSource") or "")]
    current_ids = {term["id"] for term in tbox.get("classes", [])}
    for term in additions:
        if term["id"] not in current_ids:
            tbox.setdefault("classes", []).append(term)
            current_ids.add(term["id"])
        if not any(entry.get("type") == "Class" and entry.get("id") == term["id"] for entry in entries):
            entries.append({"id": term["id"], "type": "Class", "label": term.get("label", term["id"]),
                            "iri": term.get("iri") or "https://local.ontology/coast-raid-k#" + term["id"],
                            "file": "tbox.json", "module": "Knowledge"})
    for collection in ("objectProperties", "dataProperties"):
        present = {prop["id"] for prop in tbox.get(collection, [])}
        for prop in vocabulary.get(collection, []):
            if prop.get("definitionStatus") == "project-definition-pending-domain-review" and prop["id"] not in present:
                tbox.setdefault(collection, []).append(prop)
                present.add(prop["id"])
    # Runtime objects use the same declared vocabulary as the source package.
    # Graph categories such as EventRecord are views, not RDF classes; the
    # admission gate must map them to a declared public event subtype first.
    class_ids = {term["id"] for term in tbox.get("classes", [])}
    property_ids = {term["id"] for term in tbox.get("objectProperties", [])}
    data_property_ids = {term["id"] for term in tbox.get("dataProperties", [])}
    object_iris = {record.get("iri") for record in layer.get("objects", [])}
    for record in layer.get("objects", []):
        ontology_class = str(record.get("ontologyClass") or "")
        ontology_class_id = ontology_class.rsplit("#", 1)[-1]
        if record.get("type") not in class_ids or ontology_class_id != record.get("type") or ontology_class != "https://local.ontology/coast-raid-k#" + record["type"]:
            raise ValueError("published intelligence object uses an undeclared ontology class")
        if record.get("type") == "AttributeObservation":
            assertion = record.get("assertion") or {}
            predicate = str(assertion.get("predicate") or "")
            if predicate != "https://local.ontology/coast-raid-k#" + predicate.split("#")[-1] or predicate.split("#")[-1] not in data_property_ids:
                raise ValueError("published property uses an undeclared data predicate")
            if assertion.get("subject") not in object_iris or not assertion.get("evidenceRefs"):
                raise ValueError("published property subject or source evidence is missing")
    for relation in layer.get("relations", []):
        name = relation.get("relation")
        if name not in property_ids or relation.get("predicate") != "https://local.ontology/coast-raid-k#" + name:
            raise ValueError("published intelligence relation uses an undeclared ontology predicate")
        if relation.get("from") not in object_iris or relation.get("to") not in object_iris:
            raise ValueError("published intelligence relation endpoint is missing")
    files["intelligence-ontology.json"] = copy.deepcopy(layer)
    files["schema.intelligence.json"] = json.loads((ontology_root / "schema.intelligence.json").read_text(encoding="utf-8"))
    for record in layer.get("objects", []):
        if not any(entry.get("iri") == record["iri"] and entry.get("type") == record["type"] for entry in entries):
            entries.append({"id": record["id"], "iri": record["iri"], "type": record["type"],
                            "label": record.get("label", record["id"]), "file": "intelligence-ontology.json", "module": "Knowledge"})
    result["intelligenceOntology"] = {"admissionCount": layer.get("admissionCount", 0), "objectCount": len(layer.get("objects", [])),
                                       "relationCount": len(layer.get("relations", [])),
                                       "propertyCount": sum(record.get("type") == "AttributeObservation" for record in layer.get("objects", [])),
                                       "source": "intelligence-ontology.json", "classIds": [term["id"] for term in additions]}
    result["intelligenceOntology"]["detailLevel"] = layer.get("detailLevel", "compatibility")
    return result
