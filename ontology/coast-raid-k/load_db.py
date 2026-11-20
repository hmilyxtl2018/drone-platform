"""Materialize coast-raid-k.sqlite from the JSON package (SoR).

Tables follow ddl.sql plus code / source / align / facet views that match
the seven studio navs and their tabs.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from contextlib import closing
import json
import math
import sqlite3
from pathlib import Path

from identity import canonical_iri, load_registry

ROOT = Path(__file__).resolve().parent
DB = ROOT / "coast-raid-k.sqlite"
IRI = "https://local.ontology/coast-raid-k#"


def load(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def shoreline_sampled_wkt(shore: dict, x0: float = -6.0, x1: float = 6.0, n: int = 48) -> str | None:
    """A finite local-XYZ sample, never the exact harmonic shoreline."""
    if shore.get("kind") != "harmonic":
        return None
    terms = ((shore.get("params") or {}).get("terms")) or []
    pts = []
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n
        z = 0.0
        for t in terms:
            z += float(t.get("amp") or 0) * math.sin(float(t.get("freq") or 0) * x + float(t.get("phase") or 0))
        pts.append(f"{x:.4f} 0 {z:.4f}")
    return "LINESTRING Z (" + ", ".join(pts) + ")"


def body_bbox_wkt_xz(b: dict) -> str | None:
    """Return an enclosing box in the local x/z plane, not body geometry.

    Curved shapes and sectors are conservatively enclosed; even an AABB loses
    its vertical extent in this projection.  Callers must retain that scope.
    """
    shape = b.get("shape")
    if shape == "hemisphere" and b.get("c") and b.get("r") is not None:
        x, _y, z = b["c"]
        r = float(b["r"])
        return f"POLYGON(({x - r} {z - r}, {x + r} {z - r}, {x + r} {z + r}, {x - r} {z + r}, {x - r} {z - r}))"
    if shape == "disk" and b.get("c") and b.get("r") is not None:
        x, _y, z = b["c"]
        r = float(b["r"])
        return f"POLYGON(({x - r} {z - r}, {x + r} {z - r}, {x + r} {z + r}, {x - r} {z + r}, {x - r} {z - r}))"
    if shape == "aabb":
        x0, x1, z0, z1 = b["x0"], b["x1"], b["z0"], b["z1"]
        return f"POLYGON(({x0} {z0}, {x1} {z0}, {x1} {z1}, {x0} {z1}, {x0} {z0}))"
    if shape == "sector_slab" and b.get("c") and b.get("r") is not None:
        x, _y, z = b["c"]
        r = float(b["r"])
        return f"POLYGON(({x - r} {z - r}, {x + r} {z - r}, {x + r} {z + r}, {x - r} {z + r}, {x - r} {z - r}))"
    return None


def site_wkt(s: dict) -> str | None:
    p = s.get("p")
    if not p or len(p) < 3:
        return None
    return f"POINT Z ({p[0]} {p[1]} {p[2]})"


PART_FILES = [
    "catalog.json",
    "gis.json",
    "theater.json",
    "graph.json",
    "rules.json",
    "constraints.json",
    "algorithms.json",
    "shells.json",
    "tbox.json",
    "lexicon.json",
    "dictionary.json",
    "inventory.json",
    "sources.json",
    "schema.json",
    "agent.extension.json",
    "schema.agent.extension.json",
    "agent.instances.json",
    "openviking-contexts.json",
    "identity-registry.json",
    "semantic-mapping.json",
    "semantic-decisions.json",
    "standards-evidence.json",
    "class-definition-review.json",
]
TEXT_PART_FILES = ["agent.shapes.ttl", "agent.extension.ttl"]


def collect_agent_docs(base: Path) -> list[tuple[str, str, str, str, str, str, str]]:
    """Normalize Agent extension documents for the relational index."""
    docs: list[tuple[str, str, str, str, str, str, str]] = []
    for path in sorted(base.glob("*.json")):
        if path.name in {"agent.extension.json", "schema.agent.extension.json", "schema.json"}:
            continue
        try:
            root = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        candidates: list[dict] = []
        if isinstance(root, dict) and isinstance(root.get("instances"), list):
            candidates.extend(x for x in root["instances"] if isinstance(x, dict))
        if isinstance(root, dict) and root.get("type") in {
            "AgentInstance", "Observation", "BeliefAssertion", "Authorization",
        }:
            candidates.append(root)
        for item in candidates:
            typ = str(item.get("type") or "")
            ident = str(item.get("id") or "")
            iri = str(item.get("iri") or "")
            if not typ or not ident:
                continue
            docs.append((typ, ident, iri, str(item.get("runId") or ""), path.name, source_hash, dumps(item)))
    return docs


def collect_payload(base: Path) -> dict:
    cat = json.loads((base / "catalog.json").read_text(encoding="utf-8"))
    pid, ver = cat["id"], cat["version"]
    parts = []
    for name in PART_FILES:
        path = base / name
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        parts.append((name, hashlib.sha256(path.read_bytes()).hexdigest(), json.loads(raw)))
    for name in TEXT_PART_FILES:
        path = base / name
        if path.exists():
            parts.append((name, hashlib.sha256(path.read_bytes()).hexdigest(), {"format": "text", "text": path.read_text(encoding="utf-8")}))
    # Semantic candidate outputs are generated artifacts, not source-of-record
    # inputs.  Include them in the static snapshot only when an explicit
    # --semantic build created the directory; default builds remain usable
    # without rdflib/pySHACL.
    semantic_dir = base / "semantics"
    if semantic_dir.is_dir():
        for path in sorted(semantic_dir.iterdir()):
            if not path.is_file() or path.suffix not in {".json", ".ttl"}:
                continue
            name = f"semantics/{path.name}"
            if path.suffix == ".json":
                doc = json.loads(path.read_text(encoding="utf-8"))
            else:
                doc = {"format": "text", "text": path.read_text(encoding="utf-8")}
            parts.append((name, hashlib.sha256(path.read_bytes()).hexdigest(), doc))

    inv = json.loads((base / "inventory.json").read_text(encoding="utf-8"))
    registry = load_registry(base / "identity-registry.json")
    individuals = [
        (
            e["type"],
            e["id"],
            e.get("module") or "",
            e.get("file") or "",
            e.get("label"),
            canonical_iri(e, registry),
        )
        for e in inv.get("entries") or []
    ]
    agent_docs = collect_agent_docs(base)
    agent_by_key = {}
    for typ, ident, iri, run_id, file_name, _sha, _doc in agent_docs:
        key = (typ, ident)
        if key in agent_by_key:
            raise ValueError(f"ambiguous static Agent short key {key}; separate run-scoped records before projection")
        agent_by_key[key] = canonical_iri({"type": typ, "id": ident, "iri": iri, "runId": run_id, "file": file_name}, registry)
    individuals = [(*row[:-1], agent_by_key.get((row[0], row[1]), row[-1])) for row in individuals]
    # Keep Agent extension documents discoverable even when inventory was
    # generated by an older validator.  Generated inventory may already carry
    # these rows, so deduplicate by (type,id).
    indexed = {(x[0], x[1]) for x in individuals}
    for typ, ident, iri, _run, file_name, _sha, _doc in agent_docs:
        if (typ, ident) not in indexed:
            individuals.append((typ, ident, "Agent", file_name, ident, canonical_iri({"type": typ, "id": ident, "iri": iri, "runId": _run, "file": file_name}, registry)))
            indexed.add((typ, ident))
    # The source registry also registers extension classes which were not in
    # the legacy inventory.  Keep the full source IRI in the static projection.
    for e in registry.get("entries", []):
        if (e["type"], e["id"]) not in indexed:
            individuals.append((e["type"], e["id"], "Agent" if e.get("file", "").startswith("agent.") else "", e.get("file", ""), e.get("label", e["id"]), e["iri"]))
            indexed.add((e["type"], e["id"]))
    canonical = [row[-1] for row in individuals]
    if len(set(canonical)) != len(canonical):
        raise ValueError("canonical IRI collision in static individual projection")

    lines = []
    graph = json.loads((base / "graph.json").read_text(encoding="utf-8"))
    for r in graph.get("owns") or []:
        lines.append((f"owns:{r.get('site')}-{r.get('body')}", "owns", r.get("site"), "Site", r.get("body"), "Body", "战场", "graph.json", r))
    for r in graph.get("covers") or []:
        lines.append((f"covers:{r.get('sensor')}-{r.get('body')}", "covers", r.get("sensor"), "Sensor", r.get("body"), "Body", "战场", "graph.json", r))
    tdl = graph.get("shoreTdl") or {}
    for s in tdl.get("spokes") or []:
        lines.append((f"spoke:{tdl.get('id')}:{s.get('from')}-{s.get('to')}", s.get("role") or "feeds", s.get("from"), None, s.get("to"), None, "战场", "graph.json", s))
    gis = json.loads((base / "gis.json").read_text(encoding="utf-8"))
    for m in gis.get("membership") or []:
        lines.append((f"member:{m['of']}∈{m['region']}", "memberOf", m["of"], None, m["region"], "Region", "战场", "gis.json", m))

    dictionary = json.loads((base / "dictionary.json").read_text(encoding="utf-8"))
    codes = [(c["id"], c.get("label"), c.get("values") or []) for c in dictionary.get("codes") or []]

    sources = []
    src_path = base / "sources.json"
    if src_path.exists():
        src = json.loads(src_path.read_text(encoding="utf-8"))
        for it in src.get("items") or []:
            sources.append((it["id"], it.get("usedAs"), it.get("iri"), it.get("license"), it.get("what")))

    aligns = []
    tbox = json.loads((base / "tbox.json").read_text(encoding="utf-8"))
    for cls in tbox.get("classes") or []:
        for iri in cls.get("equivalentTo") or []:
            aligns.append((cls["id"], "equivalentTo", iri))
        for iri in cls.get("seeAlso") or []:
            aligns.append((cls["id"], "seeAlso", iri))

    facets = []

    def facet(nav, tab, typ, eid, key, value):
        if value is None or value == "":
            return
        facets.append((nav, tab, typ, eid, key, str(value)))

    def geometry_metadata(typ, eid, *, approximation, plane, axes):
        # The package declares an engineering CRS.  Do not add an EPSG URI or
        # emit these local coordinates as an unqualified geo:asWKT literal.
        facet("战场", "几何", typ, eid, "geometryCrs", dumps(gis.get("crs") or {}))
        facet("战场", "几何", typ, eid, "geometryFrame", dumps(gis.get("frame") or cat.get("frame") or {}))
        facet("战场", "几何", typ, eid, "geometryApproximation", dumps(approximation))
        facet("战场", "几何", typ, eid, "geometryPlane", plane)
        facet("战场", "几何", typ, eid, "geometryAxisOrder", dumps(axes))

    theater = json.loads((base / "theater.json").read_text(encoding="utf-8"))
    for s in theater.get("sites") or []:
        sidc = s.get("sidc") or {}
        facet("战场", "符号", s["type"], s["id"], "sidc", sidc.get("code"))
        facet("战场", "符号", s["type"], s["id"], "sidcLabel", sidc.get("label"))
        facet("战场", "几何", s["type"], s["id"], "wkt", site_wkt(s))
        geometry_metadata(s["type"], s["id"], approximation=False, plane="local 3D", axes=["x", "y", "z"])
        facet("战场", "位置", s["type"], s["id"], "p", dumps(s.get("p")))
        facet("战场", "能力", s["type"], s["id"], "covers", s.get("covers"))
        facet("战场", "能力", s["type"], s["id"], "envelope", s.get("envelope"))
        facet("战场", "能力", s["type"], s["id"], "channels0", s.get("channels0"))
    for b in theater.get("bodies") or []:
        facet("战场", "几何", b["type"], b["id"], "bbox_wkt_xz", body_bbox_wkt_xz(b))
        geometry_metadata(b["type"], b["id"], approximation=True, plane="x/z ground projection; y omitted", axes=["x", "z"])
        facet("战场", "几何", b["type"], b["id"], "geometryRepresentation", "enclosing bounding box of ground projection; not exact body geometry")
        facet("战场", "几何", b["type"], b["id"], "geometrySourceRecord", dumps(b))
        facet("战场", "几何", b["type"], b["id"], "shape", b.get("shape"))
        facet("战场", "能力", b["type"], b["id"], "role", b.get("role"))
        facet("战场", "几何", b["type"], b["id"], "r", b.get("r"))
    for p in theater.get("platformTypes") or []:
        sidc = p.get("sidc") or {}
        facet("战场", "符号", "PlatformType", p["id"], "sidc", sidc.get("code"))
        facet("战场", "符号", "PlatformType", p["id"], "sidcLabel", sidc.get("label"))
        facet("战场", "型谱", "PlatformType", p["id"], "side", p.get("side"))
        facet("战场", "型谱", "PlatformType", p["id"], "inScene", p.get("inScene"))
        facet("战场", "型谱", "PlatformType", p["id"], "uasGroup", p.get("uasGroup"))
        facet("战场", "型谱", "PlatformType", p["id"], "natoClass", p.get("natoClass"))
        facet("战场", "型谱", "PlatformType", p["id"], "airframe", p.get("airframe"))
        facet("战场", "能力", "PlatformType", p["id"], "role", p.get("role"))
        facet("战场", "能力", "PlatformType", p["id"], "speed_kmh", p.get("speed_kmh"))
        facet("战场", "能力", "PlatformType", p["id"], "speed_kt_max", p.get("speed_kt_max"))
        facet("战场", "能力", "PlatformType", p["id"], "mtow_kg_max", p.get("mtow_kg_max"))
        facet("战场", "能力", "PlatformType", p["id"], "alt_m_max", p.get("alt_m_max"))
        facet("战场", "能力", "PlatformType", p["id"], "missionRadius", p.get("missionRadius"))
        facet("战场", "约束", "PlatformType", p["id"], "source", p.get("source"))
        facet("战场", "约束", "PlatformType", p["id"], "note", p.get("note"))

    sh = gis.get("shoreline") or {}
    if sh:
        facet("战场", "几何", "Shoreline", sh.get("id", "SHORE_0"), "sampled_wkt_xyz", shoreline_sampled_wkt(sh))
        geometry_metadata("Shoreline", sh.get("id", "SHORE_0"), approximation=True, plane=sh.get("plane") or "unspecified", axes=["x", "y", "z"])
        facet("战场", "几何", "Shoreline", sh.get("id", "SHORE_0"), "geometryRepresentation", "finite sampled polyline; not exact harmonic curve")
        facet("战场", "几何", "Shoreline", sh.get("id", "SHORE_0"), "geometrySampling", dumps({"x0": -6.0, "x1": 6.0, "segments": 48, "points": 49}))
        facet("战场", "几何", "Shoreline", sh.get("id", "SHORE_0"), "geometrySourceRecord", dumps(sh))
        facet("战场", "几何", "Shoreline", sh.get("id", "SHORE_0"), "crs", (gis.get("crs") or {}).get("id"))

    rules = json.loads((base / "rules.json").read_text(encoding="utf-8"))
    for r in rules.get("rules") or []:
        facet("规则", "表述", "Rule", r["id"], "text", r.get("text"))
        facet("规则", "模态", "Rule", r["id"], "modality", r.get("modality"))
        facet("规则", "执行", "Rule", r["id"], "enforce", r.get("enforce"))
        facet("规则", "表述", "Rule", r["id"], "sbvr", r.get("sbvr"))

    alg = json.loads((base / "algorithms.json").read_text(encoding="utf-8"))
    for t in alg.get("tools") or []:
        facet("算法", "主张", "Algorithm", t["id"], "claim", t.get("claim"))
        facet("算法", "实现", "Algorithm", t["id"], "impl", t.get("impl"))
        facet("算法", "实现", "Algorithm", t["id"], "paper", t.get("paper"))
        facet("算法", "职能", "Algorithm", t["id"], "bind", t.get("bind"))

    clouds = json.loads((base / "constraints.json").read_text(encoding="utf-8")).get("clouds") or {}
    for cid, c in clouds.items():
        facet("知识", "数字特征", "CloudDef", cid, "Ex", c.get("Ex"))
        facet("知识", "数字特征", "CloudDef", cid, "En", c.get("En"))
        facet("知识", "数字特征", "CloudDef", cid, "He", c.get("He"))
        facet("知识", "来源", "CloudDef", cid, "source", c.get("source"))
        facet("知识", "谱系", "CloudDef", cid, "wasGeneratedBy", c.get("wasGeneratedBy"))
        facet("知识", "可见性", "CloudDef", cid, "vis", c.get("vis"))

    # Agent extension facets make the existing Agent navigation useful for
    # instance-level inspection while the full JSON document stays in the
    # crk_agent_doc projection.
    for typ, ident, _iri, _run, _file, _sha, raw in agent_docs:
        item = json.loads(raw)
        if typ == "AgentInstance":
            facet("Agent", "型谱", typ, ident, "agentType", item.get("agentType"))
            facet("Agent", "型谱", typ, ident, "implementedOn", item.get("implementedOn"))
            facet("Agent", "阶段", typ, ident, "lifecycleState", item.get("lifecycleState"))
            facet("Agent", "阶段", typ, ident, "decisionMode", item.get("decisionMode"))
            facet("Agent", "角色", typ, ident, "organization", item.get("organization"))
            facet("Agent", "角色", typ, ident, "roles", dumps(item.get("roles") or []))
            facet("Agent", "能力", typ, ident, "capabilities", dumps(item.get("capabilities") or []))
            facet("Agent", "读写", typ, ident, "authorityScope", dumps(item.get("authorityScope") or []))
            facet("Agent", "禁写", typ, ident, "autonomyLevel", item.get("autonomyLevel"))
        elif typ == "Observation":
            facet("Agent", "读写", typ, ident, "observedEntity", item.get("observedEntity"))
            facet("Agent", "读写", typ, ident, "observationKind", item.get("observationKind"))
            facet("Agent", "阶段", typ, ident, "observedAt", item.get("observedAt"))
        elif typ == "BeliefAssertion":
            facet("Agent", "读写", typ, ident, "assertedBy", item.get("assertedBy"))
            facet("Agent", "读写", typ, ident, "aboutIri", item.get("aboutIri"))
            facet("Agent", "能力", typ, ident, "confidence", item.get("confidence"))
        elif typ == "Authorization":
            facet("Agent", "角色", typ, ident, "decidedBy", item.get("decidedBy"))
            facet("Agent", "角色", typ, ident, "result", item.get("result"))
            facet("Agent", "禁写", typ, ident, "reasonCode", item.get("reasonCode"))

    return {
        "pid": pid,
        "ver": ver,
        "title": cat.get("title"),
        "kind": cat["kind"],
        "envelope": cat["envelope"],
        "unit": cat["unit"],
        "frame": cat.get("frame") or {},
        "excludes": cat.get("excludes") or [],
        "parts_map": cat.get("parts") or {},
        "parts": parts,
        "individuals": individuals,
        "lines": lines,
        "codes": codes,
        "sources": sources,
        "aligns": aligns,
        "facets": facets,
        "agent_docs": agent_docs,
    }


def _populate_sqlite(con: sqlite3.Connection, base: Path) -> None:
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(
        """
        CREATE TABLE crk_package (
          id TEXT NOT NULL,
          version TEXT NOT NULL,
          title TEXT,
          kind TEXT NOT NULL,
          envelope TEXT NOT NULL,
          unit TEXT NOT NULL,
          frame TEXT NOT NULL,
          excludes TEXT NOT NULL,
          parts TEXT NOT NULL,
          PRIMARY KEY (id, version)
        );
        CREATE TABLE crk_part (
          package_id TEXT NOT NULL,
          version TEXT NOT NULL,
          file TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          doc TEXT NOT NULL,
          PRIMARY KEY (package_id, version, file),
          FOREIGN KEY (package_id, version) REFERENCES crk_package (id, version) ON DELETE CASCADE
        );
        CREATE TABLE crk_individual (
          package_id TEXT NOT NULL,
          version TEXT NOT NULL,
          type TEXT NOT NULL,
          id TEXT NOT NULL,
          nav TEXT NOT NULL,
          file TEXT NOT NULL,
          label TEXT,
          iri TEXT NOT NULL,
          PRIMARY KEY (package_id, version, type, id),
          UNIQUE (package_id, version, iri),
          FOREIGN KEY (package_id, version) REFERENCES crk_package (id, version) ON DELETE CASCADE
        );
        CREATE TABLE crk_line (
          package_id TEXT NOT NULL,
          version TEXT NOT NULL,
          id TEXT NOT NULL,
          pred TEXT NOT NULL,
          from_id TEXT NOT NULL,
          from_type TEXT,
          to_id TEXT,
          to_type TEXT,
          nav TEXT NOT NULL,
          file TEXT NOT NULL,
          doc TEXT,
          PRIMARY KEY (package_id, version, id),
          FOREIGN KEY (package_id, version) REFERENCES crk_package (id, version) ON DELETE CASCADE
        );
        CREATE TABLE crk_code (
          package_id TEXT NOT NULL,
          version TEXT NOT NULL,
          id TEXT NOT NULL,
          label TEXT,
          vals TEXT NOT NULL,
          PRIMARY KEY (package_id, version, id)
        );
        CREATE TABLE crk_source (
          package_id TEXT NOT NULL,
          version TEXT NOT NULL,
          id TEXT NOT NULL,
          used_as TEXT,
          iri TEXT,
          license TEXT,
          what TEXT,
          PRIMARY KEY (package_id, version, id)
        );
        CREATE TABLE crk_align (
          package_id TEXT NOT NULL,
          version TEXT NOT NULL,
          class_id TEXT NOT NULL,
          rel TEXT NOT NULL,
          iri TEXT NOT NULL,
          PRIMARY KEY (package_id, version, class_id, rel, iri)
        );
        CREATE TABLE crk_facet (
          package_id TEXT NOT NULL,
          version TEXT NOT NULL,
          nav TEXT NOT NULL,
          tab TEXT NOT NULL,
          type TEXT NOT NULL,
          id TEXT NOT NULL,
          key TEXT NOT NULL,
          value TEXT,
          PRIMARY KEY (package_id, version, nav, tab, type, id, key)
        );
        CREATE TABLE crk_agent_doc (
          package_id TEXT NOT NULL,
          version TEXT NOT NULL,
          type TEXT NOT NULL,
          id TEXT NOT NULL,
          iri TEXT,
          run_id TEXT,
          file TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          doc TEXT NOT NULL,
          PRIMARY KEY (package_id, version, type, id)
        );
        CREATE INDEX crk_agent_doc_run_idx ON crk_agent_doc (package_id, version, run_id);
        CREATE VIEW v_nav AS
          SELECT nav, type, id, label, file FROM crk_individual;
        CREATE VIEW v_agent AS
          SELECT type, id, iri, run_id, file, sha256, doc
          FROM crk_agent_doc;
        """
    )

    payload = collect_payload(base)
    pid, ver = payload["pid"], payload["ver"]
    con.execute(
        "INSERT INTO crk_package VALUES (?,?,?,?,?,?,?,?,?)",
        (
            pid,
            ver,
            payload["title"],
            payload["kind"],
            dumps(payload["envelope"]),
            dumps(payload["unit"]),
            dumps(payload["frame"]),
            dumps(payload["excludes"]),
            dumps(payload["parts_map"]),
        ),
    )
    for name, sha, doc in payload["parts"]:
        con.execute("INSERT INTO crk_part VALUES (?,?,?,?,?)", (pid, ver, name, sha, dumps(doc)))
    for row in payload["individuals"]:
        con.execute("INSERT INTO crk_individual VALUES (?,?,?,?,?,?,?,?)", (pid, ver, *row))
    for row in payload["lines"]:
        *head, doc = row
        con.execute("INSERT INTO crk_line VALUES (?,?,?,?,?,?,?,?,?,?,?)", (pid, ver, *head, dumps(doc)))
    for cid, label, vals in payload["codes"]:
        con.execute("INSERT INTO crk_code VALUES (?,?,?,?,?)", (pid, ver, cid, label, dumps(vals)))
    for row in payload["sources"]:
        con.execute("INSERT INTO crk_source VALUES (?,?,?,?,?,?,?)", (pid, ver, *row))
    for row in payload["aligns"]:
        con.execute("INSERT INTO crk_align VALUES (?,?,?,?,?)", (pid, ver, *row))
    for row in payload["facets"]:
        con.execute("INSERT INTO crk_facet VALUES (?,?,?,?,?,?,?,?)", (pid, ver, *row))
    for row in payload["agent_docs"]:
        con.execute("INSERT INTO crk_agent_doc VALUES (?,?,?,?,?,?,?,?,?)", (pid, ver, *row))
    con.commit()


def emit_sqlite(
    root: Path | None = None, *, output_path: Path | None = None, overwrite: bool = False
) -> Path:
    """Build beside the requested target, promoting only a complete database.

    Existing callers can still supply a package root.  Replacing an existing
    snapshot now requires explicit overwrite=True; failures preserve it.
    """
    base = (root or ROOT).resolve()
    db_path = (output_path or base / "coast-raid-k.sqlite").resolve()
    if db_path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing static SQLite snapshot: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{db_path.name}.", suffix=".tmp", dir=db_path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        with closing(sqlite3.connect(temporary_path)) as con:
            with con:
                _populate_sqlite(con, base)
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("generated SQLite database failed integrity_check")
        if overwrite:
            os.replace(temporary_path, db_path)
        else:
            # Hard-link promotion is atomic and fails if another writer has
            # created the target since the initial existence check.
            os.link(temporary_path, db_path)
            temporary_path.unlink()
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    print(f"emit {db_path.as_posix()} ({db_path.stat().st_size} bytes)")
    return db_path


def _sql_statements(text: str):
    """Yield SQL statements without splitting semicolons inside quoted text.

    The PostgreSQL DDL contains comments and string literals with semicolons.
    A plain ``str.split(';')`` truncates those literals and produces invalid
    statements, while PostgreSQL itself correctly handles them.
    """
    current: list[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                current.append(char)
            index += 1
            continue
        if not in_single and not in_double and char == "-" and next_char == "-":
            in_line_comment = True
            index += 2
            continue
        current.append(char)
        if char == "'" and not in_double:
            if in_single and next_char == "'":
                current.append(next_char)
                index += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            if in_double and next_char == '"':
                current.append(next_char)
                index += 2
                continue
            in_double = not in_double
        elif char == ";" and not in_single and not in_double:
            current.pop()
            statement = "".join(current).strip()
            if statement:
                yield statement
            current = []
        index += 1
    statement = "".join(current).strip()
    if statement:
        yield statement


def emit_postgres(root: Path | None = None, dbname: str = "coast_raid_k") -> str:
    import os
    import re

    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    from psycopg2.extras import Json

    if not re.fullmatch(r"[a-z][a-z0-9_]*", dbname):
        raise ValueError(f"bad database name {dbname!r}")
    password = os.environ.get("PGPASSWORD")
    if not password:
        raise SystemExit("set PGPASSWORD in the environment; do not put it in files")
    kw = {
        "host": os.environ.get("PGHOST", "127.0.0.1"),
        "port": int(os.environ.get("PGPORT", "5432")),
        "user": os.environ.get("PGUSER", "postgres"),
        "password": password,
    }
    admin = psycopg2.connect(dbname="postgres", **kw)
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = admin.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
    if cur.fetchone():
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        cur.execute(f"DROP DATABASE {dbname}")
    cur.execute(f"CREATE DATABASE {dbname}")
    cur.close()
    admin.close()

    base = root or ROOT
    ddl = (base / "ddl.sql").read_text(encoding="utf-8")
    con = psycopg2.connect(dbname=dbname, **kw)
    cur = con.cursor()
    for stmt in _sql_statements(ddl):
        cur.execute(stmt)

    payload = collect_payload(base)
    pid, ver = payload["pid"], payload["ver"]
    cur.execute(
        "INSERT INTO crk_package VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            pid,
            ver,
            payload["title"],
            payload["kind"],
            Json(payload["envelope"]),
            Json(payload["unit"]),
            Json(payload["frame"]),
            Json(payload["excludes"]),
            Json(payload["parts_map"]),
        ),
    )
    for name, sha, doc in payload["parts"]:
        cur.execute("INSERT INTO crk_part VALUES (%s,%s,%s,%s,%s)", (pid, ver, name, sha, Json(doc)))
    for row in payload["individuals"]:
        cur.execute("INSERT INTO crk_individual VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (pid, ver, *row))
    for row in payload["lines"]:
        *head, doc = row
        cur.execute("INSERT INTO crk_line VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (pid, ver, *head, Json(doc)))
    for cid, label, vals in payload["codes"]:
        cur.execute("INSERT INTO crk_code VALUES (%s,%s,%s,%s,%s)", (pid, ver, cid, label, Json(vals)))
    for row in payload["sources"]:
        cur.execute("INSERT INTO crk_source VALUES (%s,%s,%s,%s,%s,%s,%s)", (pid, ver, *row))
    for row in payload["aligns"]:
        cur.execute("INSERT INTO crk_align VALUES (%s,%s,%s,%s,%s)", (pid, ver, *row))
    for row in payload["facets"]:
        cur.execute("INSERT INTO crk_facet VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (pid, ver, *row))
    for row in payload["agent_docs"]:
        typ, ident, iri, run_id, file_name, sha, doc = row
        cur.execute(
            "INSERT INTO crk_agent_doc VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (pid, ver, typ, ident, iri, run_id, file_name, sha, Json(json.loads(doc))),
        )
    con.commit()
    cur.execute(
        """
        SELECT 'crk_package' AS t, count(*) FROM crk_package
        UNION ALL SELECT 'crk_part', count(*) FROM crk_part
        UNION ALL SELECT 'crk_individual', count(*) FROM crk_individual
        UNION ALL SELECT 'crk_line', count(*) FROM crk_line
        UNION ALL SELECT 'crk_code', count(*) FROM crk_code
        UNION ALL SELECT 'crk_source', count(*) FROM crk_source
        UNION ALL SELECT 'crk_align', count(*) FROM crk_align
        UNION ALL SELECT 'crk_facet', count(*) FROM crk_facet
        UNION ALL SELECT 'crk_agent_doc', count(*) FROM crk_agent_doc
        """
    )
    counts = cur.fetchall()
    cur.close()
    con.close()
    print(f"postgres {kw['host']}:{kw['port']}/{dbname}")
    for name, n in counts:
        print(f"  {name}\t{n}")
    return dbname


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize the package into an explicit output database.")
    parser.add_argument("--postgres", action="store_true", help="load PostgreSQL (legacy explicit operation)")
    parser.add_argument("--out-dir", type=Path, help="directory for a new static SQLite snapshot")
    args = parser.parse_args(argv)
    if args.postgres:
        if args.out_dir is not None:
            parser.error("--out-dir is not valid with --postgres")
        emit_postgres(ROOT)
    else:
        if args.out_dir is None:
            parser.error("SQLite generation requires --out-dir; use validate.py build for a complete release")
        target = args.out_dir.expanduser().resolve() / "coast-raid-k.sqlite"
        if target == (ROOT / "coast-raid-k.sqlite").resolve():
            parser.error("--out-dir must not be the live source package")
        emit_sqlite(ROOT, output_path=target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
