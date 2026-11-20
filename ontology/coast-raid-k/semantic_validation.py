"""Shared RDF validation core for isolated release and read-only verification.

SHACL uses supplied types plus declared subclass hierarchy, never domain/range
inference to manufacture missing reference types.  OWL rule checks are separate.
"""
from __future__ import annotations
from typing import Any
from owlrl import OWLRL_Semantics
from pyshacl import validate
from rdflib import Graph, Literal, RDF, RDFS, XSD
from rdflib.namespace import SH

def targets(data: Graph, shapes: Graph, ontology: Graph) -> list[dict[str, Any]]:
    hierarchy = data + ontology
    records = []
    target_predicates = (SH.targetClass, SH.targetNode, SH.targetSubjectsOf, SH.targetObjectsOf)
    subject_shapes = set().union(*(set(shapes.subjects(predicate, None)) for predicate in target_predicates))
    # SHACL also supports implicit class targets when a shape is an RDFS class.
    for shape in set(shapes.subjects(RDF.type, SH.NodeShape)) | set(shapes.subjects(RDF.type, SH.PropertyShape)):
        if (shape, RDF.type, RDFS.Class) in shapes:
            subject_shapes.add(shape)
    for shape in sorted(subject_shapes, key=str):
        focus_nodes = set(shapes.objects(shape, SH.targetNode))
        target_classes = set(shapes.objects(shape, SH.targetClass))
        if (shape, RDF.type, RDFS.Class) in shapes:
            target_classes.add(shape)
        for target_class in target_classes:
            classes = {target_class}
            pending = [target_class]
            while pending:
                current = pending.pop()
                for child in hierarchy.subjects(RDFS.subClassOf, current):
                    if child not in classes:
                        classes.add(child)
                        pending.append(child)
            for klass in classes:
                focus_nodes.update(data.subjects(RDF.type, klass))
        for predicate in shapes.objects(shape, SH.targetSubjectsOf):
            focus_nodes.update(data.subjects(predicate, None))
        for predicate in shapes.objects(shape, SH.targetObjectsOf):
            focus_nodes.update(data.objects(None, predicate))
        deactivated = bool(shapes.value(shape, SH.deactivated))
        records.append({"shape": str(shape), "targetClasses": sorted(map(str, target_classes)), "targetCount": len(focus_nodes), "focusNodes": sorted(map(str, focus_nodes)), "status": "deactivated" if deactivated else ("covered" if focus_nodes else "uncovered")})
    return records


def normalize_simple_strings(graph: Graph) -> tuple[Graph, int]:
    """Canonicalize the RDF 1.1 simple-string spelling for RDFLib equality.

    RDFLib can retain datatype=None for a Turtle simple string, while an
    explicit xsd:string literal is a distinct Python object in sh:in checks.
    RDF 1.1 gives both spellings xsd:string; normalizing this spelling does not
    add statements or infer missing types. Language-tagged strings stay intact.
    """
    normalized = Graph()
    for prefix, ns in graph.namespaces():
        normalized.bind(prefix, ns)
    count = 0
    for subject, predicate, obj in graph:
        if isinstance(obj, Literal) and obj.datatype is None and obj.language is None:
            obj = Literal(str(obj), datatype=XSD.string)
            count += 1
        normalized.add((subject, predicate, obj))
    return normalized, count


def owl_rule_report(data: Graph, ontology: Graph) -> dict[str, Any]:
    data, _ = normalize_simple_strings(data)
    ontology, _ = normalize_simple_strings(ontology)
    combined = data + ontology
    before = len(combined)
    reasoner = OWLRL_Semantics(combined, axioms=False, daxioms=False, rdfs=True)
    reasoner.closure()
    return {"noDetectedContradiction": not reasoner.error_messages, "errors": list(reasoner.error_messages), "triplesBefore": before, "triplesAfter": len(combined), "scope": "OWL RL/RDF rules implemented by owlrl with rdfs=True, axioms=False, daxioms=False; not full OWL 2 DL consistency or satisfiability"}


def validate_graphs(data: Graph, shapes: Graph, ontology: Graph) -> dict[str, Any]:
    data, data_strings = normalize_simple_strings(data)
    shapes, shape_strings = normalize_simple_strings(shapes)
    ontology, ontology_strings = normalize_simple_strings(ontology)
    coverage = targets(data, shapes, ontology)
    conforms, result_graph, text = validate(data, shacl_graph=shapes, ont_graph=ontology, inference="none", advanced=False, abort_on_first=False)
    violations = []
    for result in result_graph.subjects(RDF.type, SH.ValidationResult):
        violations.append({"focusNode": str(result_graph.value(result, SH.focusNode)), "path": str(result_graph.value(result, SH.resultPath)), "sourceShape": str(result_graph.value(result, SH.sourceShape)), "severity": str(result_graph.value(result, SH.resultSeverity)), "message": str(result_graph.value(result, SH.resultMessage))})
    owl_report = owl_rule_report(data, ontology)
    active = [r for r in coverage if r["status"] != "deactivated"]
    covered = [r for r in active if r["status"] == "covered"]
    uncovered = [r for r in active if r["status"] == "uncovered"]
    failures = not conforms or bool(owl_report["errors"])
    return {
        "normalization": {"policy": "RDF 1.1 simple strings canonicalized to explicit xsd:string in validation copies only; language tags and source files preserved", "dataLiterals": data_strings, "shapeLiterals": shape_strings, "ontologyLiterals": ontology_strings},
        "shacl": {"conforms": bool(conforms), "inference": "none", "advanced": False, "coverage": coverage, "coveredShapeCount": len(covered), "uncoveredShapeCount": len(uncovered), "violations": violations, "reportText": text},
        "owlRl": owl_report,
        "outcome": "failed" if failures else ("passed-for-covered-targets" if covered else "uncovered"),
        "allActiveShapesCovered": bool(active) and not uncovered,
        "hasCoveredTargets": bool(covered),
    }

