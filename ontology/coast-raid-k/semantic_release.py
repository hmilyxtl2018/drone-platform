"""Isolated semantic build for the M4 evidence package.

The normal package build may remain usable without rdflib/pySHACL.  A final
semantic build calls this module from the locked semantics environment and
writes only to a fresh staging directory.  It never replaces the current
SQLite snapshot or generated TTL files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from importlib.metadata import version as package_version
from pathlib import Path

from compile_semantics import compile_package
from convert_instances import convert_documents
from convert_sources import records_from_package


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_documents(source: Path) -> list[dict]:
    return [{**record, "_identity": meta} for record, meta in records_from_package(source)]


def _graph_reports(source, data_graph, generated_shapes, ontology_graph, run_shacl):
    from rdflib import Graph
    from semantic_validation import owl_rule_report, validate_graphs
    shapes = Graph()
    shapes.parse(source / "agent.shapes.ttl", format="turtle")
    for triple in generated_shapes:
        shapes.add(triple)
    if run_shacl:
        graph_report = validate_graphs(data_graph, shapes, ontology_graph)
        shacl = graph_report["shacl"]
        shacl["normalization"] = graph_report["normalization"]
        owl = graph_report["owlRl"]
    else:
        shacl = {"status": "not-run", "conforms": None, "coverage": [], "violations": []}
        owl = owl_rule_report(data_graph, ontology_graph)
    coverage = shacl.get("coverage", [])
    shacl.update({
        "engine": "pyshacl", "inference": "none", "advanced": False,
        "resultCount": len(shacl.get("violations", [])), "uncoveredIsPass": False,
        "targetClassCount": len({c for row in coverage for c in row["targetClasses"]}),
        "coveredTargetClasses": sorted({c for row in coverage if row["status"] == "covered" for c in row["targetClasses"]}),
        "uncoveredTargetClasses": sorted({c for row in coverage if row["status"] == "uncovered" for c in row["targetClasses"]}),
        "text": shacl.get("reportText", ""),
    })
    owl.update({"engine": "owlrl", "profile": owl["scope"],
                "conforms": owl["noDetectedContradiction"], "messages": owl["errors"],
                "violations": [{"focusNode": None, "message": message, "attribution": "global-rule-error"} for message in owl["errors"]],
                "closureTriples": owl["triplesAfter"], "completeOWL2DLDecision": False})
    return shacl, owl


def _pointer(parts):
    return "".join("/" + str(x).replace("~", "~0").replace("/", "~1") for x in parts)


def _source_record(source, meta, cache):
    """Locate the original JSON value; display identity never changes its shape."""
    name, typ, ident = meta["file"], meta["type"], meta["id"]
    if name not in cache:
        cache[name] = json.loads((source / name).read_text(encoding="utf-8"))
    doc = cache[name]
    profiles = {**dict.fromkeys(["Sensor", "CommandPost", "Shooter", "Platform", "Emitter"], "Site"),
                **dict.fromkeys(["Envelope", "Corridor", "SearchSector", "DetectionDisk", "JamBloom"], "Body"),
                "Package": "PackageCatalog", "Exclude": "ExcludeId",
                "Monitor": "ShellCatalog/properties/permitWriter"}
    profile = profiles.get(typ, typ)
    if typ == "Package" and name == "catalog.json":
        return doc, "", profile
    if typ == "Exclude" and name == "catalog.json":
        matches = [(value, _pointer(["excludes", i])) for i, value in enumerate(doc["excludes"]) if value == ident]
    elif typ == "Monitor" and name == "shells.json":
        matches = [(doc["permitWriter"], "/permitWriter")] if doc["permitWriter"] == ident else []
    elif typ == "CloudDef" and name == "constraints.json":
        matches = [(doc["clouds"][ident], _pointer(["clouds", ident]))] if ident in doc.get("clouds", {}) else []
    elif typ == "Shoreline" and name == "gis.json":
        matches = [(doc["shoreline"], "/shoreline")] if doc["shoreline"].get("id") == ident else []
    elif typ == "Needline" and name == "graph.json":
        matches = [(doc["shoreTdl"], "/shoreTdl")] if doc["shoreTdl"].get("id") == ident else []
    else:
        collections = {
            "theater.json": ["sites", "bodies", "platformTypes"],
            "gis.json": ["membership"] if typ == "Membership" else ["regions"],
            "graph.json": ["otherLinks"], "rules.json": ["rules"],
            "algorithms.json": ["tools"] if typ == "Algorithm" else ["functions"],
            "constraints.json": ["invariants"], "shells.json": ["templates"],
        }.get(name, ["instances"])
        matches = []
        for collection in collections:
            for i, record in enumerate(doc.get(collection, [])):
                record_id = str(record["of"]) + "∈" + str(record["region"]) if typ == "Membership" else record.get("id")
                if record_id == ident:
                    matches.append((record, _pointer([collection, i])))
    if len(matches) != 1:
        raise ValueError(f"source locator expected one record for {name} {typ}/{ident}; found {len(matches)}")
    value, pointer = matches[0]
    return value, pointer, profile


def _owned_nodes(data_graph, iri):
    """Associate nested blank-node records with owners without following URI links."""
    from rdflib import BNode, URIRef
    nodes, pending = {URIRef(iri)}, [URIRef(iri)]
    while pending:
        for obj in data_graph.objects(pending.pop(), None):
            if isinstance(obj, BNode) and obj not in nodes:
                nodes.add(obj)
                pending.append(obj)
    return {str(node) for node in nodes}


def _layer_status(statuses):
    if "engine-error" in statuses:
        return "engine-error"
    if "failed" in statuses:
        return "failed"
    if "not-run" in statuses:
        return "not-run"
    if "target-not-covered" in statuses:
        return "target-coverage-incomplete"
    return "passed"


def emit_semantic_release(source: Path, out_dir: Path, *, run_shacl: bool = True) -> dict:
    source = source.resolve()
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise FileExistsError(f"semantic output directory already exists: {out_dir}")
    required = [
        "identity-registry.json",
        "semantic-mapping.json",
        "semantic-decisions.json",
        "standards-evidence.json",
        "schema.semantic-mapping.json",
        "tbox.json",
        "agent.extension.json",
        "agent.shapes.ttl",
    ]
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"semantic source files missing: {missing}")

    out_dir.mkdir(parents=True)
    mapping = json.loads((source / "semantic-mapping.json").read_text(encoding="utf-8"))
    registry = json.loads((source / "identity-registry.json").read_text(encoding="utf-8"))
    ontology_graph, generated_shapes, compile_report = compile_package(source)
    documents=_source_documents(source)
    data_graph, conversion_report = convert_documents(documents, mapping, registry)

    ontology_graph.serialize(out_dir / "ontology.ttl", format="turtle")
    generated_shapes.serialize(out_dir / "generated.shapes.ttl", format="turtle")
    data_graph.serialize(out_dir / "abox.ttl", format="turtle")
    shutil.copyfile(source / "agent.shapes.ttl", out_dir / "agent.shapes.ttl")

    try:
        shacl_report, owl_report = _graph_reports(source, data_graph, generated_shapes, ontology_graph, run_shacl)
    except Exception as exc:
        # A validator crash is evidence, never a successful empty result.
        message = f"{type(exc).__name__}: {exc}"
        shacl_report = {"engine": "pyshacl", "inference": "none", "conforms": False,
                        "status": "engine-error", "error": message, "uncoveredIsPass": False,
                        "coverage": [], "violations": []}
        from semantic_validation import owl_rule_report
        raw_owl = owl_rule_report(data_graph, ontology_graph)
        owl_report = {**raw_owl, "engine": "owlrl", "profile": raw_owl["scope"],
                      "conforms": raw_owl["noDetectedContradiction"], "messages": raw_owl["errors"],
                      "violations": [{"focusNode": None, "message": e, "attribution": "global-rule-error"} for e in raw_owl["errors"]],
                      "completeOWL2DLDecision": False}
    executed_at = datetime.now(timezone.utc).isoformat()
    versions = {name: package_version(name) for name in ["rdflib", "pyshacl", "owlrl", "jsonschema"]}
    from jsonschema import Draft202012Validator, FormatChecker
    from convert_instances import json_hash
    schemas = json.loads((source / "schema.json").read_text(encoding="utf-8")).get("$defs", {})
    if (source / "schema.agent.extension.json").exists():
        schemas.update(json.loads((source / "schema.agent.extension.json").read_text(encoding="utf-8")).get("$defs", {}))
    source_names = set(required + ["schema.json", "semantic_release.py", "semantic_validation.py", "compile_semantics.py", "convert_instances.py", "convert_sources.py"])
    source_names.update(doc["_identity"]["file"] for doc in documents)
    source_names.update(p.name for p in source.glob("schema.*.json"))
    source_hashes = {name: sha256(source / name) for name in sorted(source_names) if (source / name).is_file()}
    subjects, source_cache = [], {}
    for doc, item in zip(documents, conversion_report["subjects"]):
        meta, iri = doc["_identity"], item["iri"]
        raw, pointer, profile = _source_record(source, meta, source_cache)
        covered = profile.split("/")[0] in schemas
        schema_errors = []
        if covered:
            validator = Draft202012Validator({"$ref": "#/$defs/" + profile, "$defs": schemas}, format_checker=FormatChecker())
            schema_errors = [{"focusNode": iri, "path": _pointer(e.absolute_path), "sourcePointer": pointer + _pointer(e.absolute_path),
                              "schemaPath": _pointer(e.absolute_schema_path), "message": e.message} for e in validator.iter_errors(raw)]
        common = {"executedAt": executed_at, "versions": versions,
                  "inputHashes": {"sourceRecord": json_hash(raw), "conversionRecord": item["sourceHash"],
                                  "sourceFile": source_hashes[meta["file"]]}}
        owned = _owned_nodes(data_graph, iri)
        shape_coverage = [{**row, "focusNodes": sorted(owned & set(row["focusNodes"])),
                           "targetCount": len(owned & set(row["focusNodes"]))}
                          for row in shacl_report.get("coverage", [])
                          if row["status"] == "covered" and owned & set(row["focusNodes"])]
        focus_nodes = sorted({node for row in shape_coverage for node in row["focusNodes"]})
        sh_violations = [{**v, "ownerIri": iri} for v in shacl_report.get("violations", []) if v["focusNode"] in owned]
        sh_status = shacl_report.get("status")
        if sh_status not in ["not-run", "engine-error"]:
            sh_status = "failed" if sh_violations else "passed" if shape_coverage else "target-not-covered"
        subjects.append({"iri": iri, "classIri": item["classIri"], "sourceFile": meta["file"], "sourcePointer": pointer,
          "JSONSchema": {**common, "tool": "jsonschema Draft202012Validator", "targetCount": int(covered),
                         "schemaProfile": "#/$defs/" + profile if covered else None, "sourcePointer": pointer,
                         "recordRepresentation": "original-source-value; display identity metadata excluded",
                         "status": "failed" if schema_errors else "passed" if covered else "target-not-covered", "violations": schema_errors},
          "SHACL": {**common, "tool": "pyshacl; inference=none; declared subclass hierarchy; RDF1.1 string normalization",
                    "targetCount": len(focus_nodes), "focusNodeCount": len(focus_nodes), "focusNodes": focus_nodes,
                    "shapeCount": len(shape_coverage), "coveredShapes": [row["shape"] for row in shape_coverage], "coverage": shape_coverage,
                    "status": sh_status, "violations": sh_violations, "error": shacl_report.get("error")},
          "OWLRL": {**common, "tool": "owlrl OWLRL_Semantics", "targetCount": 1,
                    "status": "scope-passed" if owl_report["conforms"] else "global-failure-unattributed",
                    "violations": [], "globalViolations": owl_report["violations"], "scope": owl_report["profile"],
                    "completeOWL2DLDecision": False}})
    active_shapes = [row for row in shacl_report.get("coverage", []) if row["status"] != "deactivated"]
    graph_focus_nodes = sorted({node for row in active_shapes for node in row["focusNodes"]})
    shacl_status = shacl_report.get("status")
    if shacl_status not in ["engine-error", "not-run"]:
        shacl_status = "failed" if not shacl_report.get("conforms") else "target-coverage-incomplete" if shacl_report.get("uncoveredShapeCount") or not active_shapes else "passed"
    json_status = _layer_status([row["JSONSchema"]["status"] for row in subjects])
    outputs = [
        "ontology.ttl",
        "generated.shapes.ttl",
        "abox.ttl",
        "agent.shapes.ttl",
    ]
    report = {
        "semanticBuildVersion": "1.1.0",
        "package": registry.get("package", "coast-raid-k"),
        "packageVersion": registry.get("packageVersion"),
        "sourceHashes": source_hashes,
        "outputs": {name: sha256(out_dir / name) for name in outputs},
        "compiler": compile_report,
        "conversion": conversion_report,
        "shacl": shacl_report,
        "owlrl":owl_report,
        "subjects":subjects,
        "executedAt":executed_at,
        "toolVersions":versions,
        "scope":"JSON document constraints, supplied-graph SHACL targets, and OWL RL forward closure; not complete standards certification or OWL 2 DL satisfiability",
        "validation": {
          "JSONSchema": {
            "status": json_status, "executedAt": executed_at, "tool": "jsonschema Draft202012Validator", "versions": versions,
            "inputHashes": source_hashes, "targetCount": sum(x["JSONSchema"]["targetCount"] for x in subjects),
            "uncoveredSubjectCount": sum(x["JSONSchema"]["status"] == "target-not-covered" for x in subjects),
            "violations": [v for x in subjects for v in x["JSONSchema"]["violations"]]},
          "SHACL": {
            "status": shacl_status, "executedAt": executed_at, "tool": "pyshacl; inference=none; RDF1.1 string normalization", "versions": versions,
            "inputHashes": source_hashes, "targetCount": len(graph_focus_nodes), "focusNodeCount": len(graph_focus_nodes),
            "focusNodes": graph_focus_nodes, "shapeCount": len(active_shapes), "coverage": shacl_report.get("coverage", []),
            "coveredShapeCount": shacl_report.get("coveredShapeCount", 0), "uncoveredShapeCount": shacl_report.get("uncoveredShapeCount", 0),
            "uncoveredShapes": [row["shape"] for row in active_shapes if row["status"] == "uncovered"],
            "violations": shacl_report.get("violations", []), "error": shacl_report.get("error"),
            "uncoveredTargetClasses": shacl_report.get("uncoveredTargetClasses", [])},
          "OWLRL": {
            "status": "scope-passed" if owl_report.get("conforms") else "failed",
            "executedAt": executed_at, "tool": "owlrl OWLRL_Semantics", "versions": versions,
            "inputHashes": source_hashes, "targetCount": len(subjects), "violations": owl_report.get("violations", []),
            "scope": owl_report.get("profile"), "completeOWL2DLDecision": False}
        },
        "status": "candidate-conforms" if (conversion_report.get("status") == "converted" and json_status == "passed" and shacl_status == "passed" and owl_report["conforms"]) else "candidate-with-data-gaps",
        "releaseGate": "Candidate-with-data-gaps may be reviewed with all residuals visible; it must not be presented as complete semantic acceptance or compliant production data.",
        "limitations": [
            "No full OWL 2 DL consistency decision is claimed.",
            "Uncovered SHACL shapes and JSON Schema subjects are not passes; targetCount counts actual focus nodes, not classes.",
            "Missing reference records are reported and never manufactured.",
            "Unmapped JSON fields remain in source records and conversion-report.json.",
        ],
    }
    (out_dir / "conversion-report.json").write_text(json.dumps(conversion_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "semantic-export-report.json").write_text(json.dumps(compile_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "semantic-validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--skip-shacl", action="store_true")
    args = parser.parse_args(argv)
    report = emit_semantic_release(args.source_dir, args.out_dir, run_shacl=not args.skip_shacl)
    print(json.dumps({"status": report["status"], "conversion": report["conversion"]["counts"], "shacl": report["shacl"].get("conforms")}, ensure_ascii=False))
    return 0 if report["status"] == "candidate-conforms" else 2


if __name__ == "__main__":
    raise SystemExit(main())
