"""Replay a saved live attribute through admission in an isolated temporary DB.

No network requests are made. The live database is opened with mode=ro; all
review and publication actions are explicitly simulated in a disposable store.
Run after the live extraction artifact has at least one saved mapped attribute.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from jsonschema import Draft202012Validator

try:
    from intelligence_store import IntelligenceConflict, IntelligenceStore
    from intelligence_ontology import attach_intelligence_ontology
    from object_verification import build_object_verification_checks
    from ontology_attribute_admission import AttributeAdmissionError, predicate_for
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_store import IntelligenceConflict, IntelligenceStore
    from .intelligence_ontology import attach_intelligence_ontology
    from .object_verification import build_object_verification_checks
    from .ontology_attribute_admission import AttributeAdmissionError, predicate_for


ROOT = Path(__file__).resolve().parents[1]
ONTOLOGY_ROOT = ROOT / "ontology" / "coast-raid-k"
PREDICATES_BY_KEY = {
    "architecture": "reportedArchitecture", "interface-standard": "reportedInterface",
    "human-oversight": "reportedHumanOversight", "public-test-status": "reportedTestStatus",
    "industrial-maturity": "reportedMaturity", "procurement-stage": "reportedContractStage",
}
TEST_REVIEWER = "ISOLATED E2E TEST REVIEWER — not the user"


def _read_live_snapshot(database: Path, assessment_id: str, graph_id: str | None):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        row = con.execute("SELECT * FROM intelligence_assessment WHERE assessment_id=?", (assessment_id,)).fetchone()
        if row is None:
            raise ValueError("live assessment not found")
        assessment = IntelligenceStore._assessment(con, row)
        if graph_id:
            graph_row = con.execute("SELECT * FROM intelligence_graph_revision WHERE assessment_id=? AND graph_revision_id=?",
                                    (assessment_id, graph_id)).fetchone()
        else:
            graph_row = con.execute("SELECT * FROM intelligence_graph_revision WHERE assessment_id=? ORDER BY revision DESC LIMIT 1",
                                    (assessment_id,)).fetchone()
        if graph_row is None:
            return assessment, None
        graph = {"graphRevisionId": graph_row["graph_revision_id"], "assessmentId": graph_row["assessment_id"],
                 "revision": graph_row["revision"], "contentHash": graph_row["content_hash"],
                 "createdAt": graph_row["created_at"], "nodes": json.loads(graph_row["nodes"]),
                 "edges": json.loads(graph_row["edges"])}
        return assessment, graph


def _choose_property(assessment: dict, graph: dict) -> tuple[dict, int, str] | None:
    candidates = []
    source_by_id = {source["itemId"]: source for source in assessment["sourceSnapshot"]}
    checks = {check.get("graphElementId"): check for check in build_object_verification_checks(assessment, graph)}
    for node in graph["nodes"]:
        payload = node.get("payload") or {}
        if payload.get("extractionMethod") != "llm":
            continue
        try:
            kind = IntelligenceStore._graph_node_type(node)
        except ValueError:
            continue
        node_check = checks.get(node["id"], {})
        # Another property's historical category failure must not prevent a
        # separately grounded target from reaching its own admission checks.
        identity_checks = [check for check in node_check.get('checks', []) if not check.get('propertyPath')
                           and check.get('code') != 'semantic-human-review']
        if not identity_checks or any(check.get('status') != 'passed' for check in identity_checks):
            continue
        for index, attribute in enumerate(payload.get("attributes") or []):
            if not isinstance(attribute, dict):
                continue
            path = f"payload.attributes[{index}].value"
            property_check = next((check for check in node_check.get('propertyEvidence', []) if check.get('propertyPath') == path), {})
            if property_check.get('machineStatus') != 'passed':
                continue
            requested = PREDICATES_BY_KEY.get(attribute.get("attributeKey"))
            try:
                predicate = predicate_for(attribute.get("name", ""), requested, kind, path)
            except AttributeAdmissionError:
                continue
            refs = attribute.get("evidenceRefs") or []
            if not refs or any(ref.get("itemId") not in source_by_id for ref in refs):
                continue
            # Require an observed per-object real-call record, not a fixture
            # injected into graph facts, for this live acceptance replay.
            extraction = payload.get("attributeExtraction") or {}
            if extraction.get("status") not in {"completed", "no-supported-attributes", "no-explicit-attributes"}:
                continue
            score = (0 if node.get("label") == "MARS Mission System" and predicate == "reportedArchitecture" else 1,
                     0 if predicate == "reportedArchitecture" else 1, node["id"], index)
            candidates.append((score, node, index, predicate))
    if not candidates:
        return None
    _, node, index, predicate = min(candidates, key=lambda candidate: candidate[0])
    return node, index, predicate


def verify_live_attribute(run: dict[str, Any], *, database: Path, ontology_root: Path = ONTOLOGY_ROOT) -> dict[str, Any]:
    result: dict[str, Any] = {
        "verification": "isolated-live-attribute-admission-and-KB-readback",
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status": "waiting-for-live-attributes", "testOnly": True, "humanReviewSimulated": True,
        "reviewer": TEST_REVIEWER, "truthVerified": False, "realPublicationPerformed": False,
        "networkRequests": 0, "liveDatabaseAccess": "read-only", "checks": [],
    }
    assessment_id = run.get("assessmentId")
    if not isinstance(assessment_id, str) or not assessment_id:
        result["reason"] = "live run artifact does not yet identify an assessment"
        return result
    graph_id = run.get("graphRevisionId") or run.get("resultGraphRevisionId")
    assessment, graph = _read_live_snapshot(database, assessment_id, graph_id)
    result["origin"] = {"assessmentId": assessment_id, "assessmentRevision": assessment["revision"],
                         "assessmentProvider": assessment.get("provider"), "assessmentModel": assessment.get("model"),
                         "taskId": assessment["taskId"], "sourceCount": len(assessment["sourceSnapshot"]),
                         "runStatus": run.get("status"), "graphRevisionId": graph.get("graphRevisionId") if graph else None,
                         "graphRevision": graph.get("revision") if graph else None}
    if graph is None:
        result["reason"] = "live run has no saved graph revision"
        return result
    chosen = _choose_property(assessment, graph)
    if chosen is None:
        result["reason"] = "no saved source-supported live attribute with a descriptive whitelist mapping"
        return result
    node, index, predicate = chosen
    attribute = node["payload"]["attributes"][index]
    property_path = f"payload.attributes[{index}].value"
    result["origin"].update(objectId=node["id"], objectLabel=node["label"], propertyPath=property_path,
                            rawValue=attribute["value"], displayValue=attribute.get("displayValue"),
                            predicate="https://local.ontology/coast-raid-k#" + predicate,
                            attributeEvidenceRefs=deepcopy(attribute["evidenceRefs"]),
                            sourceModel=node["payload"].get("model"), claimKind=attribute.get("claimKind"))
    schema = json.loads((ontology_root / "schema.intelligence.json").read_text(encoding="utf-8"))

    def validate(definition, value):
        Draft202012Validator({"$ref": "#/$defs/" + definition, "$defs": schema["$defs"]}).validate(value)

    def passed(code: str, **evidence):
        result["checks"].append({"code": code, "status": "passed", **evidence})

    def blocked_publish(store, admission, code):
        try:
            store.publish_admission(admission["admissionId"], {"reviewer": TEST_REVIEWER, "expectedRevision": admission["revision"]})
        except IntelligenceConflict:
            passed(code)
            return
        raise AssertionError(code + ": publication unexpectedly succeeded")

    def accept(store, admission):
        return store.review_admission(admission["admissionId"], {
            "action": "accept", "reviewer": TEST_REVIEWER, "expectedRevision": admission["revision"],
            "verifiedConfidence": "high",
            "reason": "TEST ONLY: simulate an explicit reviewer after checking source locator, object attribution and separate normalized wording; this is not a user acceptance or an independent verification.",
        })

    with tempfile.TemporaryDirectory(prefix="osint-live-attribute-e2e-") as directory:
        store = IntelligenceStore(Path(directory) / "isolated.sqlite")
        cloned = store.create_assessment(deepcopy(assessment))["assessment"]
        isolated_aid = cloned["assessmentId"]
        saved_graph = store.save_graph(isolated_aid, {"nodes": deepcopy(graph["nodes"]), "edges": deepcopy(graph["edges"])})
        assert cloned["sourceSnapshot"] == assessment["sourceSnapshot"]
        assert saved_graph["nodes"] == graph["nodes"] and saved_graph["edges"] == graph["edges"]
        passed("complete-source-snapshot-and-graph-preserved", sourceCount=len(cloned["sourceSnapshot"]))
        selection = {"graphRevisionId": saved_graph["graphRevisionId"], "graphNodeIds": [node["id"]],
                     "graphProperties": [{"graphNodeId": node["id"], "propertyPath": property_path,
                                           "ontologyPredicate": predicate}]}
        candidates = store.propose_graph_admissions(isolated_aid, selection)
        owner = next(candidate for candidate in candidates if candidate["kind"] == "graph-node")
        prop = next(candidate for candidate in candidates if candidate["kind"] == "graph-property")
        for candidate in candidates:
            validate("OntologyAdmission", candidate)
        assert owner["assertion"]["attributes"] == {}
        assert prop["assertion"]["rawValue"] == attribute["value"]
        assert prop["assertion"]["predicate"].endswith("#" + predicate)
        assert prop["assertion"]["evidenceRefs"][0]["quote"] == attribute["evidenceRefs"][0]["quote"]
        passed("server-derived-owner-and-independent-mapped-property")
        blocked_publish(store, owner, "unreviewed-owner-publication-blocked")
        blocked_publish(store, prop, "unreviewed-property-publication-blocked")
        accepted_property = accept(store, prop)
        blocked_publish(store, accepted_property, "accepted-property-with-unpublished-owner-blocked")
        accepted_owner = accept(store, owner)
        published_owner = store.publish_admission(accepted_owner["admissionId"], {"reviewer": TEST_REVIEWER, "expectedRevision": accepted_owner["revision"]})
        assert store.published_ontology()["objects"][0]["attributes"] == {}
        passed("simulated-review-and-owner-publication-does-not-publish-property")
        published_property = store.publish_admission(accepted_property["admissionId"], {"reviewer": TEST_REVIEWER, "expectedRevision": accepted_property["revision"]})
        layer = store.published_ontology()
        for obj in layer["objects"]:
            validate("PublishedIntelligenceObject", obj)
        owner_record = next(obj for obj in layer["objects"] if obj["iri"] == published_owner["candidateIri"])
        observation = next(obj for obj in layer["objects"] if obj["iri"] == published_property["candidateIri"])
        assert owner_record["attributes"][predicate] == [attribute["value"]]
        assert observation["assertion"]["rawValue"] == attribute["value"]
        assert owner_record["admittedProperties"][0]["evidenceRefs"] == prop["assertion"]["evidenceRefs"]
        provenance = next(record for record in layer["provenance"] if record["admissionId"] == published_property["admissionId"])
        assert provenance["propertyPath"] == property_path
        assert provenance["sourceSnapshotHash"] == store._snapshot_hash(assessment["sourceSnapshot"])
        passed("isolated-publication-source-qualified-readback", rawValue=observation["assertion"]["rawValue"],
               normalizedValue=observation["assertion"].get("normalizedValue"), propertyPath=property_path,
               originGraphRevisionId=graph["graphRevisionId"], isolatedGraphRevisionId=saved_graph["graphRevisionId"])
        base = {"files": {"tbox.json": {"classes": []}}, "inventory": {"entries": []}}
        boot = attach_intelligence_ontology(base, layer, ontology_root)
        assert base == {"files": {"tbox.json": {"classes": []}}, "inventory": {"entries": []}}
        restored_layer = boot["files"]["intelligence-ontology.json"]
        assert next(obj for obj in restored_layer["objects"] if obj["iri"] == owner_record["iri"])["attributes"][predicate] == [attribute["value"]]
        assert boot["intelligenceOntology"]["propertyCount"] == 1
        assert any(entry["iri"] == observation["iri"] and entry["type"] == "AttributeObservation" for entry in boot["inventory"]["entries"])
        passed("knowledge-base-boot-overlay-value-and-evidence-readable", propertyCount=boot["intelligenceOntology"]["propertyCount"])
        result["isolatedResult"] = {"admissionCount": layer["admissionCount"], "objectCount": len(layer["objects"]),
                                   "relationCount": len(layer["relations"]), "temporaryStoreRemovedAfterRun": True,
                                   "simulatedPublicationPerformed": True, "testOnly": True,
                                   "ownerAdmissionId": published_owner["admissionId"], "propertyAdmissionId": published_property["admissionId"]}
    result["status"] = "passed-isolated-e2e"
    result["limitations"] = [
        "Review and publication were simulated solely in a temporary store; the user did not accept or publish these facts.",
        "This verifies program gates, schema and source-qualified readback, not factual truth, independent sourcing or equipment capability.",
        "Live database was opened read-only. No model calls or knowledge publication occurred there.",
    ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-file", type=Path, default=ROOT / ".local" / "revision-workpackages-live.json")
    parser.add_argument("--database", type=Path, default=ONTOLOGY_ROOT / "coast-raid-k-intelligence.sqlite")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "verification" / "revision-workpackages-live-e2e-20261008.json")
    args = parser.parse_args()
    run = json.loads(args.run_file.read_text(encoding="utf-8"))
    try:
        result = verify_live_attribute(run, database=args.database)
    except Exception as exc:
        result = {"status": "failed-isolated-e2e", "errorType": type(exc).__name__, "testOnly": True,
                  "truthVerified": False, "realPublicationPerformed": False, "humanReviewSimulated": True,
                  "networkRequests": 0, "liveDatabaseAccess": "read-only"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "checks": len(result.get("checks", [])),
                      "output": str(args.output), "testOnly": True, "realPublicationPerformed": False}))
    return 0 if result["status"] == "passed-isolated-e2e" else 2 if result["status"] == "waiting-for-live-attributes" else 1


if __name__ == "__main__":
    raise SystemExit(main())
