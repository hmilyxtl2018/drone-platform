"""Stable identity rules for the coast-raid-k knowledge package.

This module is deliberately independent from the current SQLite builder and
the browser.  It gives those consumers one deterministic identity contract
used by the SQLite candidate projection and browser identity contract:

* an explicit source ``iri`` always wins;
* classes without an IRI use ``base#<class-id>``;
* individuals without an IRI use ``base#individual/<type>/<id>``;
* the historical ``base#<id>`` form is a compatibility alias only.  An alias
  can resolve to more than one record and must never silently choose one.

The class fallback intentionally does not create a ``Class#`` namespace.  A
class and an individual are nevertheless distinguished by the registry key
and by the individual fallback path.  Existing Agent namespace IRIs are
preserved as-is.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import quote, urlsplit


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "identity-registry.json"


class IdentityError(ValueError):
    """Raised when an identity declaration is malformed or collides."""


def _text(value: object) -> str:
    return str(value or "").strip()


def is_absolute_iri(value: object) -> bool:
    if not isinstance(value,str) or not value or value!=value.strip() or any(c.isspace() for c in value):
        return False
    parsed = urlsplit(value)
    return bool(parsed.scheme and (parsed.netloc or (parsed.scheme in {"urn", "tag"} and parsed.path)))


def encode_id(value: object) -> str:
    """Encode an identifier for a stable IRI path segment."""

    ident = _text(value)
    if not ident:
        raise IdentityError("identity id is empty")
    return quote(ident, safe="-._~")


@dataclass(frozen=True)
class IdentityRecord:
    kind: str
    type_id: str
    local_id: str
    iri: str
    source_iri: str | None
    source_file: str | None
    legacy_alias: str
    run_id: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.kind, self.type_id, self.local_id, self.run_id or "")

DEFAULT_BASE_NS = "https://local.ontology/coast-raid-k#"


def load_registry(path: Path | str = REGISTRY_PATH) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise IdentityError("identity registry must be a JSON object")
    return data


def _namespaces(registry: Mapping[str, object]) -> tuple[str, str]:
    ns = registry.get("namespaces") or {}
    base = _text(ns.get("base")) or DEFAULT_BASE_NS
    agent = _text(ns.get("agent")) or f"{base.rstrip('#')}/agent/1.0.0#"
    if not base.endswith(("#", "/")) or not agent.endswith(("#", "/")):
        raise IdentityError("identity namespaces must end in # or /")
    return base, agent


def _kind(entry: Mapping[str, object]) -> str:
    declared = _text(entry.get("kind"))
    if declared in {"Class", "Individual"}:
        return declared
    return "Class" if _text(entry.get("type")) == "Class" else "Individual"


def canonical_iri(
    entry: Mapping[str, object],
    registry: Mapping[str, object] | None = None,
) -> str:
    """Return the canonical IRI for one class or individual entry.

    ``entry['iri']`` is a source declaration and is never rewritten.  The
    fallback for classes uses the base namespace directly; the fallback for
    individuals includes type and an encoded ID so two object classes can
    share an old short ID without sharing an IRI.
    """

    reg = registry or load_registry()
    base, _agent = _namespaces(reg)
    source_iri = entry.get("iri")
    if source_iri:
        if not is_absolute_iri(source_iri):
            raise IdentityError(f"source iri is not absolute: {source_iri!r}")
        return source_iri
    type_id = _text(entry.get("type"))
    local_id = _text(entry.get("id"))
    if not type_id or not local_id:
        raise IdentityError("identity entry requires type and id")
    registered = [r for r in reg.get("entries", ())
                  if r.get("type") == type_id and r.get("id") == local_id
                  and (not entry.get("file") or r.get("file") == entry.get("file"))
                  and (not entry.get("runId") or r.get("runId") == entry.get("runId"))]
    if len(registered) == 1:
        return _text(registered[0]["iri"])
    if len(registered) > 1:
        raise IdentityError(f"ambiguous identity registration: {type_id}/{local_id}; specify run or source")
    if _kind(entry) == "Class":
        return f"{base}{encode_id(local_id)}"
    if entry.get("runId"):
        return f"{base}individual/{encode_id(type_id)}/run/{encode_id(entry['runId'])}/{encode_id(local_id)}"
    return f"{base}individual/{encode_id(type_id)}/{encode_id(local_id)}"


def record_for(
    entry: Mapping[str, object],
    registry: Mapping[str, object] | None = None,
) -> IdentityRecord:
    source = _text(entry.get("iri")) or None
    return IdentityRecord(
        kind=_kind(entry),
        type_id=_text(entry.get("type")) or "Class",
        local_id=_text(entry.get("id")),
        iri=canonical_iri(entry, registry),
        source_iri=source,
        source_file=_text(entry.get("file")) or None,
        legacy_alias=f"{_namespaces(registry or load_registry())[0]}{_text(entry.get('id'))}",
        run_id=_text(entry.get("runId")) or None,
        aliases=tuple(entry.get("aliases") or ()),
    )


def build_index(
    entries: Iterable[Mapping[str, object]],
    registry: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build canonical and legacy indexes without silently resolving aliases."""

    records: list[IdentityRecord] = []
    by_key: dict[tuple[str, str, str, str], list[IdentityRecord]] = {}
    by_iri: dict[str, list[IdentityRecord]] = {}
    aliases: dict[str, list[IdentityRecord]] = {}
    for entry in entries:
        rec = record_for(entry, registry)
        records.append(rec)
        # A local name may be reused in another namespace.  Keep all full-IRI
        # identities and expose the short key as ambiguous instead of dropping
        # a legitimate term or choosing the first one.
        by_key.setdefault(rec.key, []).append(rec)
        by_iri.setdefault(rec.iri, []).append(rec)
        for alias in dict.fromkeys((rec.legacy_alias, *rec.aliases)):
            aliases.setdefault(alias, []).append(rec)
    collisions = {iri: rows for iri, rows in by_iri.items() if len(rows) > 1}
    if collisions:
        detail = "; ".join(f"{iri}: {[r.key for r in rows]}" for iri, rows in collisions.items())
        raise IdentityError(f"canonical IRI collision: {detail}")
    return {
        "records": tuple(records),
        "byKey": by_key,
        "byIri": by_iri,
        "legacyAliases": aliases,
        "shortKeyAmbiguities": {key:tuple(rows) for key,rows in by_key.items() if len(rows)>1},
    }


def resolve(
    value: str,
    index: Mapping[str, object],
    *,
    kind: str | None = None,
    type_id: str | None = None,
) -> dict[str, object]:
    """Resolve a canonical IRI or old short alias.

    The result has ``status`` equal to ``resolved``, ``ambiguous`` or
    ``unknown``.  Callers must display ambiguity and ask for a type/context;
    this function never returns an arbitrary first match.
    """

    text = _text(value)
    by_iri = index.get("byIri") or {}
    aliases = index.get("legacyAliases") or {}
    rows = list(by_iri.get(text, ())) or list(aliases.get(text, ()))
    if kind:
        rows = [r for r in rows if r.kind == kind]
    if type_id:
        rows = [r for r in rows if r.type_id == type_id]
    if len(rows) == 1:
        return {"status": "resolved", "record": rows[0], "via": "iri" if text in by_iri else "legacyAlias"}
    if len(rows) > 1:
        return {"status": "ambiguous", "candidates": tuple(rows), "via": "legacyAlias"}
    return {"status": "unknown", "candidates": tuple()}


def source_entries(
    *,
    inventory: Mapping[str, object],
    agent_instances: Mapping[str, object] | None = None,
    classes: Iterable[Mapping[str, object]] = (),
) -> list[dict[str, object]]:
    """Normalize current package sources for registry/index construction."""

    # Inventory may already contain the same AgentInstance rows that are
    # present in agent.instances.json.  Prefer the row carrying the source IRI
    # and keep one registry key per (kind, type, id).
    out: list[dict[str, object]] = []
    seen: dict[tuple[str, str, str], int] = {}
    for raw in inventory.get("entries", ()):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        key = ("Class" if _text(item.get("type")) == "Class" else "Individual", _text(item.get("type")), _text(item.get("id")))
        if key not in seen:
            seen[key] = len(out)
            out.append(item)
        elif _text(item.get("iri")) and not _text(out[seen[key]].get("iri")):
            out[seen[key]] = item
    for cls in classes:
        item = dict(cls)
        item["type"] = "Class"
        key = ("Class", "Class", _text(item.get("id")))
        if key in seen and canonical_iri(out[seen[key]]) == canonical_iri(item):
            out[seen[key]] = {**out[seen[key]], **item}
        else:
            seen[key] = len(out)
            out.append(item)
    if agent_instances:
        for item in agent_instances.get("instances", ()):
            if isinstance(item, Mapping):
                candidate = dict(item)
                key = ("Class" if _text(candidate.get("type")) == "Class" else "Individual", _text(candidate.get("type")), _text(candidate.get("id")))
                if key in seen:
                    out[seen[key]] = {**out[seen[key]], **candidate}
                else:
                    seen[key] = len(out)
                    out.append(candidate)
    return out


__all__ = [
    "DEFAULT_BASE_NS",
    "IdentityError",
    "IdentityRecord",
    "build_index",
    "canonical_iri",
    "encode_id",
    "is_absolute_iri",
    "load_registry",
    "record_for",
    "resolve",
    "source_entries",
]
