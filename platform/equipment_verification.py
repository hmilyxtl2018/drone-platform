"""Object-directed Firecrawl verification for imported equipment candidates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any

try:
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound
    from osint_collector import FirecrawlSearchProvider, OSINTCollectionError
except ModuleNotFoundError:  # pragma: no cover
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound
    from .osint_collector import FirecrawlSearchProvider, OSINTCollectionError


TARGETS = (
    # These are bounded source groups, but the equipment registry spans legacy
    # systems whose official specifications predate the default 10-year web
    # window. ``sinceDays=0`` means all publicly indexed history; source role
    # and review status still prevent it from becoming trusted fact.
    ("identity", ("subcategory", "lifecycle-status", "first-flight-year", "service-entry-year"), ("defense-official", "test-evaluation"), "official designation manufacturer country model identity first flight service entry lifecycle", 8, 0),
    ("performance", ("length", "wingspan", "max-takeoff-weight", "payload-capacity", "max-speed", "ceiling", "flight-range", "endurance", "propulsion-type", "rcs", "jamming-resistance"), ("defense-official", "test-evaluation", "manufacturer-disclosure"), "length wingspan maximum takeoff weight payload speed ceiling range endurance propulsion radar cross section electronic protection specification test evaluation", 14, 0),
    ("systems", ("sensor-payload", "data-link", "primary-weapons", "stealth-feature"), ("defense-official", "test-evaluation", "manufacturer-disclosure"), "sensor payload data link communications weapons integration stealth low observable", 12, 0),
    ("collaboration", ("swarm-compatibility", "autonomy-mode", "human-oversight", "data-link"), ("defense-official", "test-evaluation", "defense-media"), "swarm teaming collaborative combat autonomy human control interoperability", 12, 0),
    ("industrial", ("reference-unit-cost", "production-count"), ("defense-official", "test-evaluation", "procurement-budget", "manufacturer-disclosure", "defense-media"), "aircraft configuration composition system architecture air vehicle ground control station equipment components manufacturing process fabrication assembly inspection testing manufacturer designer assembler supplier integrator production batch production rate quantity order delivery contract unit production cost flyaway price procurement amount contract quantity included excluded training spares support", 16, 0),
    ("combat", ("combat-status", "combat-conflict-count", "combat-hours-or-production"), ("defense-official", "test-evaluation", "defense-media", "public-imagery"), "combat deployment conflict theater event date operational use confirmed loss imagery sortie lessons", 12, 0),
    ("imagery", (), ("public-imagery",), "satellite imagery public historical publication", 8, 0),
)

# Extend directed targets with capability gaps even when the archive template
# predates them. Reading a profile never runs these searches.
from pathlib import Path
_CAPABILITY_REGISTRY = json.loads((Path(__file__).resolve().parents[1] / 'ontology/coast-raid-k/equipment-capability-registry.json').read_text('utf-8'))
TARGETS = tuple((target, tuple(dict.fromkeys([*keys, *(key for group in _CAPABILITY_REGISTRY['groups'] if group['target'] == target for key in group['keys'])])), scopes, terms, limit, days) for target, keys, scopes, terms, limit, days in TARGETS)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:20]}"


def _job(row) -> dict[str, Any]:
    value = dict(row)
    for field in ("attribute_keys_json", "missing_keys_json", "source_scope_ids", "result_json"):
        if value.get(field):
            value[field] = json.loads(value[field])
    mapping = {
        "verification_id": "verificationId", "entity_id": "entityId", "entity_revision_id": "entityRevisionId",
        "target_type": "targetType", "attribute_keys_json": "attributeKeys", "missing_keys_json": "missingKeys", "query_text": "query",
        "source_scope_ids": "sourceScopeIds", "max_items": "maxItems", "since_days": "sinceDays",
        "result_json": "result", "error_message": "error", "created_at": "createdAt",
        "started_at": "startedAt", "completed_at": "completedAt",
        "heartbeat_at": "heartbeatAt", "stop_reason": "stopReason", "recovered_at": "recoveredAt",
        "worker_token": "workerToken",
    }
    for source, target in mapping.items():
        if source in value:
            value[target] = value.pop(source)
    return value


class EquipmentVerificationQueue:
    def __init__(self, store, provider: FirecrawlSearchProvider | None = None):
        self.store = store
        self.provider = provider or FirecrawlSearchProvider()

    @staticmethod
    def _event_tx(con, verification_id: str, event_type: str, detail: dict[str, Any]):
        con.execute(
            "INSERT INTO ontology_verification_job_event(verification_id,event_type,detail_json,created_at) VALUES(?,?,?,?)",
            (verification_id, event_type, json.dumps(detail or {}, ensure_ascii=False, sort_keys=True), _now()),
        )

    @staticmethod
    def _parse_time(value: Any):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def reclaim_stale(self, max_age_seconds: int | None = None) -> list[dict[str, Any]]:
        """Release dead provider leases while retaining a retryable job record.

        A verification row is a lease, not proof that a worker still exists.
        The heartbeat is written by the worker below; if it stops, the next
        service start or API request converts the stale ``running`` row into a
        terminal ``failed`` row with an explicit recovery reason.  Source
        snapshots and the original query are never deleted.
        """
        try:
            timeout = int(max_age_seconds if max_age_seconds is not None else os.environ.get("CRK_VERIFICATION_STALE_SECONDS", "1800"))
        except (TypeError, ValueError):
            timeout = 1800
        timeout = max(60, min(timeout, 7 * 24 * 3600))
        cutoff = datetime.now(timezone.utc).timestamp() - timeout
        recovered: list[dict[str, Any]] = []
        with self.store._lock, self.store._connect() as con:
            rows = con.execute("SELECT * FROM ontology_verification_job WHERE status='running'").fetchall()
            for row in rows:
                stamp = self._parse_time(row["heartbeat_at"] or row["started_at"] or row["created_at"])
                if stamp is None or stamp.timestamp() > cutoff:
                    continue
                finished = _now()
                detail = {
                    "reason": "stale-timeout",
                    "timeoutSeconds": timeout,
                    "lastHeartbeatAt": row["heartbeat_at"] or row["started_at"],
                    "retryable": True,
                }
                old_result = {}
                try:
                    old_result = json.loads(row["result_json"] or "{}")
                except (TypeError, ValueError):
                    old_result = {}
                old_result["recovery"] = detail
                con.execute(
                    "UPDATE ontology_verification_job SET status='failed',result_json=?,error_message=?,stop_reason='stale-timeout',recovered_at=?,completed_at=?,worker_token=NULL WHERE verification_id=? AND status='running'",
                    (json.dumps(old_result, ensure_ascii=False), "执行租约超时，已自动回收；可从原任务重试", finished, finished, row["verification_id"]),
                )
                self._event_tx(con, row["verification_id"], "stale-recovered", detail)
                recovered.append({
                    "verificationId": row["verification_id"],
                    "entityId": row["entity_id"],
                    "targetType": row["target_type"],
                    "status": "failed",
                    "stopReason": "stale-timeout",
                    "recoveredAt": finished,
                })
        return recovered

    def plan(self, entity_id: str, *, force: bool = False, target_types: set[str] | None = None) -> list[dict[str, Any]]:
        self.reclaim_stale()
        with self.store._lock, self.store._connect() as con:
            entity = con.execute("SELECT entity_id,preferred_name,english_name,manufacturer,origin_country,current_revision FROM ontology_entity WHERE entity_id=? OR canonical_key=?", (entity_id, entity_id)).fetchone()
            if entity is None:
                raise IntelligenceNotFound(f"ontology entity not found: {entity_id}")
            revision = con.execute("SELECT entity_revision_id,attributes_json FROM ontology_entity_revision WHERE entity_id=? AND revision=?", (entity["entity_id"], entity["current_revision"])).fetchone()
            if revision is None:
                raise IntelligenceConflict("entity has no current attributes revision")
            document = json.loads(revision["attributes_json"])
            attributes = document.get("attributes") or {}

            def has_value(key: str) -> bool:
                """Treat absent, empty and explicitly unknown values as gaps."""
                bucket = attributes.get(key) or {}
                values = bucket.get("values") or []
                if not values or bucket.get("state") == "unknown":
                    return False
                for value in values:
                    if not isinstance(value, dict):
                        if value not in (None, "", "unknown"):
                            return True
                        continue
                    raw = value.get("rawValue", value.get("normalizedValue", value.get("value")))
                    if raw not in (None, "", "unknown") and value.get("claimState") != "unknown":
                        return True
                return False

            available = {key for key in attributes if has_value(key)}
            names = []
            for value in (entity["preferred_name"], entity["english_name"]):
                value = str(value or "").strip()
                if value and value not in names:
                    names.append(value)
            alias_rows = con.execute("SELECT alias FROM ontology_entity_alias WHERE entity_id=? ORDER BY confidence DESC, alias LIMIT 8", (entity["entity_id"],)).fetchall()
            for row in alias_rows:
                value = str(row[0] or "").strip()
                if value and value not in names:
                    names.append(value)
            # Public specifications often omit the system suffix or use the
            # family name ("Shadow UAS" rather than the full workbook label).
            # Add search-only variants; the original entity names remain
            # unchanged and every returned item still needs source review.
            for value in list(names):
                shortened = re.sub(r"\b(?:TUAS|STUAS|UAS|UAV|system|系统)\b", "", value, flags=re.IGNORECASE)
                shortened = re.sub(r"\s+", " ", shortened).strip(" -/（）()")
                if shortened and shortened not in names:
                    names.append(shortened)
                if re.search(r"\bShadow\b", value, re.IGNORECASE) and "Shadow UAS" not in names:
                    names.append("Shadow UAS")
            anchor = " OR ".join(f'"{value}"' for value in names[:8])
            context_parts = [entity["english_name"], entity["manufacturer"], entity["origin_country"]]
            # Category and subcategory are useful disambiguators for common
            # family names, and remain query context only.
            context_row = con.execute("SELECT category,subcategory FROM ontology_entity WHERE entity_id=?", (entity["entity_id"],)).fetchone()
            if context_row:
                context_parts.extend([context_row["category"], context_row["subcategory"]])
            lineage_terms: list[str] = []
            try:
                lineage_rows = con.execute(
                    """SELECT predecessor_name,successor_name,raw_chain,family_name
                       FROM ontology_lineage_edge
                       WHERE predecessor_entity_id=? OR successor_entity_id=?
                       ORDER BY family_name,sequence_no LIMIT 12""",
                    (entity["entity_id"], entity["entity_id"]),
                ).fetchall()
                for row in lineage_rows:
                    for value in (row["predecessor_name"], row["successor_name"]):
                        value = str(value or "").strip()
                        if value and value not in names and value not in lineage_terms:
                            lineage_terms.append(value)
            except Exception:
                # Older databases may be opened before the lineage migration;
                # query planning still works without optional lineage context.
                lineage_terms = []
            context = " ".join(str(value).strip() for value in (*context_parts, *lineage_terms[:8]) if value)
            created = _now()
            for target_type, keys, scopes, terms, limit, days in TARGETS:
                selected_keys = [key for key in keys if key in available or key in _CAPABILITY_REGISTRY['fields']]
                missing_keys = [key for key in selected_keys if key not in available]
                # Keep the first target expression at the beginning of the
                # query for audit readability and compatibility with the
                # workbook import contract; the collector adds its own
                # bounded scope terms afterwards.
                missing_context = " ".join(missing_keys[:18])
                lineage_context = " lineage " + " ".join(lineage_terms[:6]) if lineage_terms else ""
                query = f'{anchor} {context} {terms} {missing_context}{lineage_context}'.strip()
                verification_id = _id("verification", revision["entity_revision_id"], target_type)
                con.execute(
                    "INSERT OR IGNORE INTO ontology_verification_job(verification_id,entity_id,entity_revision_id,target_type,attribute_keys_json,missing_keys_json,query_text,source_scope_ids,max_items,since_days,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,'planned',?)",
                    (verification_id, entity["entity_id"], revision["entity_revision_id"], target_type, json.dumps(selected_keys), json.dumps(missing_keys), query, json.dumps(scopes), limit, days, created),
                )
                # Refresh an unstarted/failed job when its aliases or search
                # policy changed. Completed jobs retain their original query
                # for auditability and can be explicitly replanned later.
                con.execute(
                    "UPDATE ontology_verification_job SET attribute_keys_json=?,missing_keys_json=?,query_text=?,source_scope_ids=?,max_items=?,since_days=? WHERE verification_id=? AND status IN ('planned','failed')",
                    (json.dumps(selected_keys), json.dumps(missing_keys), query, json.dumps(scopes), limit, days, verification_id),
                )
                if force and (target_types is None or target_type in target_types):
                    # Explicit retry/replan keeps the same verification id for
                    # audit continuity while clearing the previous result.
                    # A running job is never reset by this path.
                    con.execute(
                        "UPDATE ontology_verification_job SET status='planned',result_json=NULL,error_message=NULL,started_at=NULL,completed_at=NULL,attribute_keys_json=?,missing_keys_json=?,query_text=?,source_scope_ids=?,max_items=?,since_days=? WHERE verification_id=? AND status!='running'",
                        (json.dumps(selected_keys), json.dumps(missing_keys), query, json.dumps(scopes), limit, days, verification_id),
                    )
            return [_job(row) for row in con.execute("SELECT * FROM ontology_verification_job WHERE entity_revision_id=? ORDER BY CASE target_type WHEN 'identity' THEN 1 WHEN 'performance' THEN 2 WHEN 'systems' THEN 3 WHEN 'collaboration' THEN 4 WHEN 'industrial' THEN 5 WHEN 'combat' THEN 6 ELSE 7 END", (revision["entity_revision_id"],)).fetchall()]

    def list(self, *, entity_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        self.reclaim_stale()
        clauses, values = [], []
        if entity_id:
            clauses.append("entity_id=?"); values.append(entity_id)
        if status:
            clauses.append("status=?"); values.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store._connect() as con:
            return [_job(row) for row in con.execute("SELECT * FROM ontology_verification_job" + where + " ORDER BY created_at,target_type", values).fetchall()]

    def execute(self, verification_id: str, *, deadline_monotonic=None) -> dict[str, Any]:
        # Opportunistically recover an abandoned lease before deciding whether
        # this request is a genuine duplicate execution.
        self.reclaim_stale()
        worker_token = secrets.token_hex(16)
        with self.store._lock, self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_verification_job WHERE verification_id=?", (verification_id,)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"verification job not found: {verification_id}")
            if row["status"] == "running":
                raise IntelligenceConflict("verification job is already running")
            started_at = _now()
            con.execute("UPDATE ontology_verification_job SET status='running',started_at=?,heartbeat_at=?,worker_token=?,error_message=NULL,stop_reason=NULL,recovered_at=NULL WHERE verification_id=?", (started_at, started_at, worker_token, verification_id))
            job = _job(row)
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(15):
                try:
                    with self.store._lock, self.store._connect() as con:
                        con.execute("UPDATE ontology_verification_job SET heartbeat_at=? WHERE verification_id=? AND status='running' AND worker_token=?", (_now(), verification_id, worker_token))
                except Exception:
                    # The provider result still determines the terminal state;
                    # a transient heartbeat write must not mask it.
                    pass

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True, name=f"heartbeat-{verification_id}")
        heartbeat_thread.start()
        try:
            anchors = re.findall(r'"([^"\n]{1,180})"', str(job.get("query") or ""))
            anchor_keywords = [f'"{anchor}"' for anchor in anchors[:8]] if anchors else [job["query"][:180]]
            # The grouped expression drives the subject gate and query;
            # individual aliases remain in the keyword list for audit and
            # backwards-compatible provider adapters.
            keywords = [" OR ".join(anchor_keywords), *anchor_keywords, *job["attributeKeys"]]
            deadline_args={'deadline_monotonic':deadline_monotonic} if isinstance(self.provider,FirecrawlSearchProvider) else {}
            loop_args={'ensure_target':True} if isinstance(self.provider,FirecrawlSearchProvider) else {}
            result = self.provider.search(job["query"], keywords, since_days=job["sinceDays"], max_items=job["maxItems"], source_ids=job["sourceScopeIds"], **loop_args, **deadline_args)
            retained = len(result.get("items") or [])
            errors = result.get("errors") or []
            status = "needs-review" if retained else "failed" if errors else "completed"
            error = "; ".join(str(item.get("error")) for item in errors)[:1000] or None
            completed = _now()
            with self.store._lock, self.store._connect() as con:
                for index, item in enumerate(result.get("items") or []):
                    item = dict(item)
                    body = str(item.get('body') or '')
                    # The hash identifies stored content, not a provider's URL hash.
                    item['contentHash'] = hashlib.sha256(body.encode('utf-8')).hexdigest()
                    item['snapshotId'] = _id('collected-source', verification_id, item['contentHash'], str(item.get('sourceUri') or item.get('itemUri') or ''))
                    result['items'][index] = item
                    con.execute('INSERT OR IGNORE INTO ontology_collected_source_snapshot(snapshot_id,verification_id,entity_revision_id,item_index,content_hash,item_json,created_at) VALUES(?,?,?,?,?,?,?)', (item['snapshotId'], verification_id, job['entityRevisionId'], index, item['contentHash'], json.dumps(item,ensure_ascii=False), completed))
                    quote = str(item.get("body") or item.get("title") or "")[:4000]
                    if not quote:
                        continue
                    evidence_id = _id("evidence", verification_id, str(index), str(item.get("contentHash") or ""))
                    con.execute(
                        "INSERT OR IGNORE INTO ontology_evidence_link(evidence_id,source_type,source_ref,source_version,quote,content_hash,locator_json,published_at,retrieved_at,source_reliability,verification_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (evidence_id, "media" if job["targetType"] == "imagery" else "web-page", item.get("itemUri") or item.get("sourceUri"), str(item.get("sourceVersion") or item.get("contentHash") or "unknown"), quote, str(item.get("contentHash") or hashlib.sha256(quote.encode()).hexdigest()), json.dumps({"lineStart": 1, "lineEnd": max(1, quote.count("\n") + 1)}), item.get("publishedAt"), result.get("fetchedAt"), item.get("sourceReliability") or "unknown", "unverified", completed),
                    )
                con.execute("UPDATE ontology_verification_job SET status=?,result_json=?,error_message=?,completed_at=?,heartbeat_at=?,worker_token=NULL WHERE verification_id=? AND worker_token=?", (status, json.dumps(result, ensure_ascii=False), error, completed, completed, verification_id, worker_token))
            return self.get(verification_id)
        except Exception as exc:
            with self.store._lock, self.store._connect() as con:
                finished = _now()
                reason = "provider-error" if isinstance(exc, (OSINTCollectionError, ValueError, OSError)) else "execution-error"
                con.execute("UPDATE ontology_verification_job SET status='failed',error_message=?,stop_reason=?,completed_at=?,heartbeat_at=?,worker_token=NULL WHERE verification_id=? AND worker_token=?", (str(exc)[:1000], reason, finished, finished, verification_id, worker_token))
            return self.get(verification_id)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)

    def get(self, verification_id: str) -> dict[str, Any]:
        with self.store._connect() as con:
            row = con.execute("SELECT * FROM ontology_verification_job WHERE verification_id=?", (verification_id,)).fetchone()
            if row is None:
                raise IntelligenceNotFound(f"verification job not found: {verification_id}")
            item = _job(row)
            item["events"] = [
                {"eventId": event["event_id"], "eventType": event["event_type"], "detail": json.loads(event["detail_json"] or "{}"), "createdAt": event["created_at"]}
                for event in con.execute("SELECT * FROM ontology_verification_job_event WHERE verification_id=? ORDER BY event_id", (verification_id,)).fetchall()
            ]
            return item


__all__ = ["EquipmentVerificationQueue", "TARGETS"]
