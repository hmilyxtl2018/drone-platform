"""Read-only situation-map projections over one immutable ontology Run."""
from copy import deepcopy

from .context_contract import content_hash


MAP_TYPES = {
    "war-system", "capability", "risk", "logistics",
    "electromagnetic-cyber", "technology-evolution", "scenario",
}
DETAIL_LEVELS = {"L0", "L1", "L2", "L3", "L4"}
EVIDENCE_LEVELS = {"E0", "E1", "E2", "E3", "E4"}
EVIDENCE_FILTERS = {'hasCitation', 'citationSupported', 'independentSources', 'factVerified', 'humanReviewed', 'published'}
LEVEL_ORDER = {f"E{i}": i for i in range(5)}
LEVEL_LABELS = {
    "E0": "无可定位支持证据",
    "E1": "单一来源支持",
    "E2": "多个来源支持，独立性未确认",
    "E3": "关系支持复核通过",
    "E4": "经授权审阅并发布",
}

EVIDENCE_TYPES = {"evidence", "EvidenceExcerpt", "source", "SourceSnapshot"}
GAP_TYPES = {"unknown", "evidence-gap", "EvidenceGap"}
L0_TYPES = {"research-question", "perspective", "conditional-judgment", "Finding", "evidence-gap", "EvidenceGap"}
L1_TYPES = L0_TYPES | {"system", "capability", "technology", "constraint", "context", "outcome",
                      "EquipmentModel", "CapabilityClaim", "Requirement"}
L2_TYPES = L1_TYPES | {"relation-assertion", "condition", "counter-hypothesis", "unknown", "RequirementComparison"}

MAP_PERSPECTIVES = {
    "war-system": {"war-system"},
    "logistics": {"industrial-logistics"},
    "electromagnetic-cyber": {"electromagnetic-cyber"},
    "technology-evolution": {"unmanned-autonomy", "sensing-data-link", "countermeasure-iteration"},
}
MAP_NODE_TYPES = {
    "capability": {"system", "capability", "technology", "EquipmentModel", "CapabilityClaim", "Requirement"},
    "risk": {"constraint", "unknown", "counter-hypothesis", "evidence-gap", "EvidenceGap"},
    "scenario": {"scenario", "event", "operating-scenario"},
}
DOMAIN_TYPES = {'system', 'capability', 'technology', 'constraint', 'context', 'outcome',
                'EquipmentModel', 'CapabilityClaim', 'Requirement', 'RequirementComparison',
                'scenario', 'event', 'operating-scenario', 'risk', 'vulnerability',
                'industrial-capacity', 'logistics', 'supply-chain'}
TYPE_ANCHORS = {
    'war-system': {'system', 'capability', 'EquipmentModel', 'CapabilityClaim'},
    'capability': {'capability', 'CapabilityClaim', 'Requirement', 'RequirementComparison'},
    'risk': {'constraint', 'risk', 'vulnerability'},
    'logistics': DOMAIN_TYPES,
    'electromagnetic-cyber': DOMAIN_TYPES,
    'technology-evolution': {'technology', 'system', 'capability', 'EquipmentModel'},
    'scenario': {'scenario', 'event', 'operating-scenario'},
}
DOMAIN_RELATIONS = {'depends-on', 'constrains', 'enables', 'changes', 'part-of', 'associated-with',
                    'has-capability', 'has-requirement', 'evaluates', 'compares', 'meets', 'violates',
                    'supports', 'limits', 'causes', 'precedes', 'requires'}


def map_availability(graph, map_type, perspective_key='all'):
    """Containers and gaps cannot qualify as modeled domain structures."""
    nodes = {n['id']: n for n in graph.get('nodes', []) if n.get('type') in DOMAIN_TYPES}
    scoped = set(nodes)
    required_keys = MAP_PERSPECTIVES.get(map_type) if map_type != 'war-system' else None
    if required_keys:
        scoped = {key for key, n in nodes.items() if set(n.get('perspectiveKeys') or []) & required_keys}
    if perspective_key != 'all':
        scoped &= {key for key, n in nodes.items() if perspective_key in (n.get('perspectiveKeys') or [])}
    anchors = {key for key in scoped if nodes[key].get('type') in TYPE_ANCHORS[map_type]}
    relations = set()
    for edge in graph.get('edges', []):
        source, target = _edge_nodes(edge)
        if source not in nodes or target not in nodes or not ({source, target} & anchors):
            continue
        if required_keys and edge.get('perspectiveKey') not in required_keys and not ({source, target} <= scoped):
            continue
        if perspective_key != 'all' and edge.get('perspectiveKey') != perspective_key and not ({source, target} <= scoped):
            continue
        if edge.get('kind') == 'assertion' or edge.get('relation', edge.get('predicate')) in DOMAIN_RELATIONS:
            relations.add(edge['id'])
    available = bool(anchors and relations)
    return {'available': available,
        'reason': None if available else '仅有报告结构或缺少匹配的领域节点与关系，尚未形成该类结构',
        'required': ['至少一个该类型的领域节点', '至少一条连接领域节点的已保存匹配关系'],
        'matchedDomainNodes': len(anchors), 'matchedDomainRelations': len(relations),
        'scope': 'task' if perspective_key == 'all' else 'perspective', 'perspectiveKey': perspective_key}


def _edge_nodes(edge):
    return edge.get("source", edge.get("subject")), edge.get("target", edge.get("object"))


def _assertion_levels(report, graph):
    evidence = {str(row.get("evidenceId") or row.get("candidateId") or row.get("claimId")): row
                for row in report.get("evidence", []) if isinstance(row, dict)}
    report_review = str(report.get("reviewStatus") or "").lower()
    publication = str(report.get("publicationStatus") or "").lower()
    published = report_review in {"approved", "reviewed", "published"} and publication == "published"
    result = {}
    for assertion in graph.get("assertions", []):
        ids = [str(identity) for identity in assertion.get("evidenceIds", [])]
        present = [evidence[identity] for identity in ids if identity in evidence]
        sources = {item.get("sourceUri") or item.get("sourceArtifactId") for item in present}
        sources.discard(None)
        if not present:
            level = "E0"
        elif assertion.get("supportReview", {}).get("verdict") == "supported":
            level = "E3"
        elif len(sources) > 1:
            level = "E2"
        else:
            level = "E1"
        if published and level == "E3":
            level = "E4"
        result[assertion["id"]] = {"level": level, "evidence": present, "sources": len(sources)}
    return result


def _map_type_match(map_type, node, node_by_id, assertions_by_id):
    kind = node.get("type")
    if map_type == "war-system":
        return True
    if map_type in MAP_PERSPECTIVES:
        key = node.get("id", "").removeprefix("perspective:")
        return key in MAP_PERSPECTIVES[map_type] or key in (node.get("perspectiveKeys") or [])
    if map_type == "capability":
        return kind in MAP_NODE_TYPES[map_type] or kind in {"perspective", "conditional-judgment", "relation-assertion"}
    if map_type == "risk":
        return kind in MAP_NODE_TYPES[map_type] or kind in {"perspective", "conditional-judgment", "relation-assertion"}
    if map_type == "scenario":
        return kind in MAP_NODE_TYPES[map_type] or kind in {"perspective", "conditional-judgment", "relation-assertion"}
    if map_type == "technology-evolution":
        return (kind in {"technology", "capability", "system", "constraint", "context", "outcome",
                         "perspective", "conditional-judgment", "relation-assertion"})
    if map_type == "electromagnetic-cyber":
        return kind in {"perspective", "conditional-judgment", "relation-assertion", "technology", "capability", "constraint", "context", "outcome"}
    return False


def _level_type_allowed(detail_level, kind):
    if detail_level == "L0":
        return kind in L0_TYPES
    if detail_level == "L1":
        return kind in L1_TYPES
    if detail_level == "L2":
        return kind in L2_TYPES
    if detail_level == "L3":
        return kind in L2_TYPES | EVIDENCE_TYPES
    return True


def _time_scope(graph):
    scopes = [row.get("scope") for row in graph.get("assertions", []) if isinstance(row.get("scope"), dict)]
    scope = deepcopy(scopes[0]) if scopes else {}
    window = scope.get("timeWindow") if isinstance(scope.get("timeWindow"), dict) else {}
    return {
        "asOf": window.get("asOf"),
        "horizonYears": window.get("horizonYears"),
        "geography": scope.get("geography"),
        "source": "run-scope" if scope else "not-recorded",
    }


def build_situation_map(report, graph, *, map_type="war-system", perspective_key="all",
                        detail_level="L2", minimum_evidence_level="E0", evidence_filters=None):
    """Build a stable view that only references nodes and edges in the source graph."""
    if map_type not in MAP_TYPES:
        raise ValueError("unsupported situation map type")
    if detail_level not in DETAIL_LEVELS:
        raise ValueError("unsupported situation map detail level")
    if minimum_evidence_level not in EVIDENCE_LEVELS:
        raise ValueError("unsupported evidence threshold")
    evidence_filters = evidence_filters or {}
    if not isinstance(evidence_filters, dict) or set(evidence_filters) - EVIDENCE_FILTERS or any(type(v) is not bool for v in evidence_filters.values()):
        raise ValueError('unsupported independent evidence filters')
    perspective_keys = {item.get("key") for item in graph.get("perspectives", [])}
    if perspective_key != "all" and perspective_key not in perspective_keys:
        raise ValueError("perspective does not belong to this Run")

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_by_id = {node["id"]: node for node in nodes}
    assertions = {row["id"]: row for row in graph.get("assertions", [])}
    assertion_levels = _assertion_levels(report, graph)
    from .review_summary import evidence_dimensions
    from .ontology_explorer import evidence_index
    evidence = evidence_index(report)
    dimensions = {key: evidence_dimensions(a, evidence, report) for key, a in assertions.items()}
    matching_assertions = {key for key, value in dimensions.items()
                           if all(value.get(field) is expected for field, expected in evidence_filters.items())}

    node_assertions = {identity: [] for identity in node_by_id}
    for assertion in graph.get("assertions", []):
        for identity in (assertion.get("subject"), assertion.get("object"), assertion.get("judgmentId"), assertion.get("id")):
            if identity in node_assertions:
                node_assertions[identity].append(assertion["id"])
    for edge in edges:
        if edge.get("assertionId") and edge.get("assertionId") in assertion_levels:
            for identity in _edge_nodes(edge):
                if identity in node_assertions:
                    node_assertions[identity].append(edge["assertionId"])

    node_levels = {}
    for node in nodes:
        linked = [assertion_levels[key]["level"] for key in set(node_assertions.get(node["id"], []))]
        if node.get("type") in EVIDENCE_TYPES:
            level = "E1"
        elif node.get("type") in GAP_TYPES or not linked:
            level = "E0"
        else:
            level = max(linked, key=LEVEL_ORDER.get)
        node_levels[node["id"]] = level

    source_keys = MAP_PERSPECTIVES.get(map_type) if map_type != "war-system" else None
    map_types = MAP_NODE_TYPES.get(map_type)
    scoped_nodes = []
    for node in nodes:
        scoped = perspective_key == "all" or perspective_key in (node.get("perspectiveKeys") or []) or node.get("id") == f"perspective:{perspective_key}"
        if not scoped:
            continue
        kind = node.get("type")
        node_perspectives = set(node.get("perspectiveKeys") or [])
        node_perspective = node.get("id", "").removeprefix("perspective:") if kind == "perspective" else None
        if source_keys and not (node_perspectives & source_keys or node_perspective in source_keys):
            continue
        scoped_nodes.append(node)

    scoped_ids = {node["id"] for node in scoped_nodes}
    if map_types:
        anchors = {node["id"] for node in scoped_nodes if node.get("type") in map_types}
        map_available = any(node.get("type") in map_types for node in nodes)
        relevant_assertions = [assertion for assertion in graph.get("assertions", [])
                               if assertion.get("subject") in anchors or assertion.get("object") in anchors]
        relevant_perspectives = {key for node in scoped_nodes if node["id"] in anchors
                                 for key in node.get("perspectiveKeys", [])}
        relevant_perspectives.update(assertion.get("perspectiveKey") for assertion in relevant_assertions
                                     if assertion.get("perspectiveKey"))
        relevant_evidence = {str(identity) for assertion in relevant_assertions
                             for identity in assertion.get("evidenceIds", [])}
        allowed = set(anchors)
        for assertion in relevant_assertions:
            allowed.update(identity for identity in (assertion.get("subject"), assertion.get("object"), assertion.get("id"))
                           if identity in scoped_ids)
        for node in scoped_nodes:
            kind = node.get("type")
            key = node.get("id", "").removeprefix("perspective:") if kind == "perspective" else None
            if kind == "perspective" and key in relevant_perspectives:
                allowed.add(node["id"])
            elif kind == "conditional-judgment" and node.get("id", "").removeprefix("judgment:") in relevant_perspectives:
                allowed.add(node["id"])
            elif kind in {"condition", "counter-hypothesis"} and (set(node.get("perspectiveKeys") or []) & relevant_perspectives):
                allowed.add(node["id"])
            elif kind == "research-question" and relevant_perspectives:
                allowed.add(node["id"])
            elif kind in EVIDENCE_TYPES and str(node.get("evidenceId") or node.get("candidateId") or "") in relevant_evidence:
                allowed.add(node["id"])
        # Keep provenance source nodes only when an included evidence excerpt
        # points to them; the evidence and exact source remain Run-scoped.
        included_evidence = {node_id for node_id in allowed if node_by_id[node_id].get("type") in EVIDENCE_TYPES}
        for edge in edges:
            source, target = _edge_nodes(edge)
            if edge.get("relation") == "quoted-from" and source in included_evidence and target in scoped_ids:
                allowed.add(target)
    else:
        declared_keys = {node.get("id", "").removeprefix("perspective:") for node in nodes
                         if node.get("type") == "perspective"}
        declared_keys.update(item.get("key") for item in graph.get("perspectives", []) if item.get("key"))
        map_available = map_type == "war-system" or not source_keys or bool(declared_keys & source_keys)
        allowed = {node["id"] for node in scoped_nodes} if map_available else set()

    for node in scoped_nodes:
        kind = node.get("type")
        if not _level_type_allowed(detail_level, kind):
            allowed.discard(node["id"])
            continue
        level = node_levels[node["id"]]
        if LEVEL_ORDER[level] < LEVEL_ORDER[minimum_evidence_level] and kind not in GAP_TYPES:
            allowed.discard(node["id"])
        if evidence_filters and kind not in GAP_TYPES:
            # Keep only paths backed by an assertion meeting every predicate.
            linked = set(node_assertions.get(node['id'], []))
            node_evidence = str(node.get('evidenceId') or node.get('candidateId') or '')
            cited = {key for key in matching_assertions if node_evidence and node_evidence in [str(v) for v in assertions[key].get('evidenceIds', [])]}
            if not (linked & matching_assertions or cited):
                allowed.discard(node['id'])

    projected_nodes = []
    for node in nodes:
        if node["id"] not in allowed:
            continue
        projected_nodes.append({**deepcopy(node), "sourceNodeId": node["id"],
                                "evidenceLevel": node_levels[node["id"]],
                                "displayState": "visible"})

    assertion_by_node = {row["id"]: row for row in graph.get("assertions", [])}
    projected_edges = []
    for edge in edges:
        source, target = _edge_nodes(edge)
        if source not in allowed or target not in allowed:
            continue
        assertion_id = edge.get("assertionId")
        assertion = assertion_by_node.get(assertion_id)
        level_info = assertion_levels.get(assertion_id, {})
        if assertion_id and LEVEL_ORDER[level_info.get("level", "E0")] < LEVEL_ORDER[minimum_evidence_level]:
            continue
        if evidence_filters and assertion_id and assertion_id not in matching_assertions:
            continue
        if detail_level in {"L0", "L1"} and edge.get("kind") == "assertion":
            continue
        projected_edges.append({**deepcopy(edge), "sourceEdgeId": edge["id"],
                                "relationClass": (assertion.get("semantics", {}).get("kind") if assertion else None) or
                                                ("analytical-inference" if assertion else "report-structure"),
                                "evidenceLevel": level_info.get("level", "E0"),
                                "evidenceDimensions": deepcopy(dimensions.get(assertion_id)),
                                "displayState": "visible"})

    availability = map_availability(graph, map_type, perspective_key)
    map_available = availability['available']
    if not map_available:
        # Retain genuine gaps as report-structure entry points, never a domain map.
        projected_nodes = [{**deepcopy(n), 'sourceNodeId': n['id'], 'evidenceLevel': 'E0',
            'displayState': 'report-structure'} for n in nodes if n.get('type') in GAP_TYPES and
            (perspective_key == 'all' or perspective_key in (n.get('perspectiveKeys') or []) or n['id'] == 'gap:' + perspective_key)]
        retained = {n['id'] for n in projected_nodes}
        projected_edges = [e for e in projected_edges if e['source'] in retained and e['target'] in retained]
    config = {"mapType": map_type, "perspectiveKey": perspective_key,
              "detailLevel": detail_level, "minimumEvidenceLevel": minimum_evidence_level,
              "evidenceFilters": dict(evidence_filters)}
    payload = {
        "schemaVersion": "situation-map.v1",
        "projectionVersion": "situation-map.independent-evidence.v1",
        **config,
        "runId": graph.get("runId"),
        "timeScope": _time_scope(graph),
        "sourceGraphHash": graph.get("sourceGraphHash"),
        "sourceProjectionHash": graph.get("contentHash"),
        "availability": "available" if map_available else "not-modeled",
        "availabilityDetail": availability,
        "taskAvailability": map_availability(graph, map_type),
        "viewAvailability": map_availability(graph, map_type, perspective_key),
        "mapAvailability": {key: map_availability(graph, key) for key in sorted(MAP_TYPES)},
        "nodes": projected_nodes,
        "edges": projected_edges,
        "rootNodeIds": [node["id"] for node in projected_nodes
                        if node.get("type") in {"conditional-judgment", "Finding", "perspective", "evidence-gap", "EvidenceGap"}],
        "evidenceLevels": deepcopy(LEVEL_LABELS),
        "evidenceLevelSemantics": "legacy-coverage-process-filter-not-confidence",
        "evidenceFilterSemantics": {"combination": "all", "unknownMatches": False,
            "notice": "独立条件按已保存记录筛选；发布与人工审阅不代表事实已核验。旧 E0–E4 仅为覆盖/流程筛选。"},
        "counts": {"nodes": len(projected_nodes), "edges": len(projected_edges),
                   "assertions": sum(1 for edge in projected_edges if edge.get("kind") == "assertion"),
                   "evidenceNodes": sum(1 for node in projected_nodes if node.get("type") in EVIDENCE_TYPES),
                   "gaps": sum(1 for node in projected_nodes if node.get("type") in GAP_TYPES)},
    }
    payload["contentHash"] = content_hash(payload)
    return payload


def validate_situation_map(value, graph):
    """Fail closed if a projection points outside its source Run graph."""
    node_ids = {node.get("id") for node in graph.get("nodes", [])}
    edge_ids = {edge.get("id") for edge in graph.get("edges", [])}
    if value.get("schemaVersion") != "situation-map.v1":
        raise ValueError("unsupported situation map schema")
    if any(node.get("sourceNodeId") not in node_ids for node in value.get("nodes", [])):
        raise ValueError("situation map contains a node outside its source graph")
    if any(edge.get("sourceEdgeId") not in edge_ids for edge in value.get("edges", [])):
        raise ValueError("situation map contains an edge outside its source graph")
    if any(source not in node_ids for edge in value.get("edges", []) for source in _edge_nodes(edge)):
        raise ValueError("situation map edge endpoint is not in its source graph")
    return True
