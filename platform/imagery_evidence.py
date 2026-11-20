"""Reference-only public imagery provenance, linked to immutable research targets.

This module never fetches a URL, interprets image pixels, changes confidence,
or writes ontology facts. A linked reference records a proposed corroboration
for human review, not a machine-verified observation of military capability.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import ipaddress
import json
import re
import secrets
from urllib.parse import parse_qsl, urlsplit

try:
    from osint_store import OSINTNotFound, OSINTValidationError
    from intelligence_store import IntelligenceConflict, IntelligenceNotFound
except ModuleNotFoundError:
    from .osint_store import OSINTNotFound, OSINTValidationError
    from .intelligence_store import IntelligenceConflict, IntelligenceNotFound


SCHEMA = """
CREATE TABLE IF NOT EXISTS osint_imagery_evidence (
 evidence_id TEXT PRIMARY KEY,
 task_id TEXT NOT NULL REFERENCES osint_task(task_id),
 assessment_id TEXT,
 content_hash TEXT NOT NULL,
 payload TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(task_id,content_hash)
);
CREATE INDEX IF NOT EXISTS osint_imagery_evidence_task_idx ON osint_imagery_evidence(task_id,created_at);
CREATE TRIGGER IF NOT EXISTS osint_imagery_evidence_no_update BEFORE UPDATE ON osint_imagery_evidence BEGIN SELECT RAISE(ABORT,'imagery references are immutable'); END;
CREATE TRIGGER IF NOT EXISTS osint_imagery_evidence_no_delete BEFORE DELETE ON osint_imagery_evidence BEGIN SELECT RAISE(ABORT,'imagery references are retained'); END;
"""

PUBLIC_IMAGERY_ROOTS = (
    "nasa.gov", "esa.int", "copernicus.eu", "usgs.gov", "noaa.gov",
    "planet.com", "maxar.com", "vantor.com", "airbus.com", "intelligence-airbusds.com",
)
SUPPORT_SCOPES = {"public-test-context", "industrial-context", "publication-image-context", "image-provenance"}
MEDIA_KINDS = {"satellite-image", "source-photo"}
FIXED_LIMITATIONS = [
    "仅保存公开发布页和图片引用；未下载图像、核验像素、获取采集坐标或开展地理分析。",
    "影像可辅助公开试验或产业背景核验，不能独立证明通信互操作、协同能力或作战效果。",
    "图片类型、拍摄日期、解释和来源独立性仍待人工核验；不会自动提升置信度。",
]


def _text(payload, field, *, required=False, limit=1600):
    value = payload.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise OSINTValidationError(f"{field} must be {'a non-empty' if required else 'a'} string of at most {limit} characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise OSINTValidationError(f"{field} contains unsupported control characters")
    return value.strip() or None


def public_https_reference(value, field="sourceUrl"):
    """Validate link syntax only; intentionally perform no DNS or network I/O."""
    if not isinstance(value, str) or not value or len(value) > 2000 or re.search(r"[\s\\\x00-\x1f]", value):
        raise OSINTValidationError(f"{field} must be a public HTTPS reference URL")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise OSINTValidationError(f"{field} contains an invalid URL") from exc
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or port not in (None, 443) or "%" in parsed.netloc):
        raise OSINTValidationError(f"{field} must be a public HTTPS reference without credentials or a custom port")
    host = host.rstrip(".")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        if ("." not in host or re.fullmatch(r"[\d.]+", host)
                or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid", ".example"))
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)):
            raise OSINTValidationError(f"{field} does not identify a public publisher domain")
    else:
        if not addr.is_global:
            raise OSINTValidationError(f"{field} must not identify a private or local network")
    if any(key.casefold() in {"api_key", "apikey", "key", "token", "access_token", "secret", "password"} for key, _ in parse_qsl(parsed.query)):
        raise OSINTValidationError(f"{field} must not contain a credential query parameter")
    return value


def _calendar_date(payload, field):
    value = _text(payload, field, limit=10)
    if value is None:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("calendar date required")
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise OSINTValidationError(f"{field} must be YYYY-MM-DD or null when unknown") from exc
    if parsed > datetime.now(timezone.utc).date():
        raise OSINTValidationError(f"{field} must not be a future date")
    return value


def _publisher_allowed(url):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == root or host.endswith("." + root) for root in PUBLIC_IMAGERY_ROOTS)


def _property_target(element, path):
    if path in {"label", "type", "relation", "payload.eventKind", "payload.date"}:
        target = element
        for part in path.split("."):
            if not isinstance(target, dict) or part not in target:
                raise OSINTValidationError("propertyPath does not exist on the selected graph element")
            target = target[part]
        return target
    match = re.fullmatch(r"payload\.attributes\[(\d+)\]\.(value|unit|conditions|name)", path)
    attributes = (element.get("payload") or {}).get("attributes") or []
    if not match or int(match[1]) >= len(attributes) or match[2] not in attributes[int(match[1])]:
        raise OSINTValidationError("propertyPath does not identify a saved graph property")
    return attributes[int(match[1])][match[2]]


class ImageryEvidenceRegistry:
    def __init__(self, osint_store, intelligence_store):
        self.osint = osint_store
        self.intelligence = intelligence_store
        with self.osint.connect() as con:
            con.executescript(SCHEMA)

    def _task(self, task_id):
        task = self.osint.get_task(task_id)
        if task is None:
            raise OSINTNotFound(f"task not found: {task_id}")
        return task

    def attach(self, task_id, payload):
        task = self._task(task_id)
        fields = {"title", "referenceType", "mediaKind", "sourceUrl", "imageUrl", "provider", "captureDate", "publishedDate", "sensor", "resolution", "license", "supportScope", "observedContent", "interpretation", "supportStatement", "limitations", "assessmentId", "graphRevisionId", "graphElementId", "propertyPath", "sourceItemId"}
        if not isinstance(payload, dict) or set(payload) - fields:
            raise OSINTValidationError("imagery evidence contains unsupported fields")
        record = {field: _text(payload, field, required=field in {"title", "provider", "observedContent", "interpretation", "supportStatement", "limitations"}, limit=300 if field in {"title", "provider", "sensor", "resolution", "license"} else 1600)
                  for field in {"title", "provider", "sensor", "resolution", "license", "observedContent", "interpretation", "supportStatement", "limitations", "assessmentId", "graphRevisionId", "graphElementId", "propertyPath", "sourceItemId"}}
        for field, allowed in (("referenceType", {"archive", "licensed-publication"}), ("mediaKind", MEDIA_KINDS), ("supportScope", SUPPORT_SCOPES)):
            if payload.get(field) not in allowed:
                raise OSINTValidationError(f"{field} must be one of {', '.join(sorted(allowed))}")
            record[field] = payload[field]
        if record["referenceType"] == "licensed-publication" and not record["license"]:
            raise OSINTValidationError("license is required for a licensed-publication reference")
        record["sourceUrl"] = public_https_reference(payload.get("sourceUrl"))
        record["imageUrl"] = public_https_reference(payload["imageUrl"], "imageUrl") if payload.get("imageUrl") is not None else None
        record["captureDate"] = _calendar_date(payload, "captureDate")
        record["publishedDate"] = _calendar_date(payload, "publishedDate")
        if record["captureDate"] and record["publishedDate"] and record["captureDate"] > record["publishedDate"]:
            raise OSINTValidationError("captureDate cannot be later than publishedDate")
        source = next((item for item in task.get("items", []) if item["id"] == record["sourceItemId"]), None)
        if record["sourceItemId"] and source is None:
            raise OSINTValidationError("sourceItemId does not identify a material in this task")
        record["sourceReferenceMatch"] = "external-public-publisher"
        if source is not None:
            direct_match = record["sourceUrl"] in {source.get("sourceUri"), source.get("itemUri")}
            provenance = source.get("provenance") or {}
            saved_media = provenance.get("mediaReferences") if isinstance(provenance, dict) else []
            media_match = bool(record["imageUrl"] and isinstance(saved_media, list) and any(
                isinstance(ref, dict) and ref.get("sourceUrl") == record["sourceUrl"]
                and ref.get("imageUrl") == record["imageUrl"] for ref in saved_media))
            if not direct_match and not media_match:
                raise OSINTValidationError("sourceUrl must match the immutable task material or its exact saved source/image reference pair")
            record["sourceReferenceMatch"] = "source-item-url" if direct_match else "saved-media-reference"
        if source is None and not _publisher_allowed(record["sourceUrl"]):
            raise OSINTValidationError("sourceUrl must be a supported public imagery publisher or an exact collected source item URL")
        record["sourceContentHash"] = source.get("contentHash") if source else None
        record["sourceRelationship"] = "task-material-pending-link" if source else "external-reference"
        links = [record[key] for key in ("assessmentId", "graphRevisionId", "graphElementId")]
        if any(links) and not all(links):
            raise OSINTValidationError("assessmentId, graphRevisionId and graphElementId must be supplied together")
        if record["propertyPath"] and not all(links):
            raise OSINTValidationError("propertyPath requires a complete graph target")
        if all(links):
            assessment = self.intelligence.get_assessment(record["assessmentId"])
            if assessment is None:
                raise IntelligenceNotFound(f"assessment not found: {record['assessmentId']}")
            followup_link = False
            if assessment.get("taskId") != task_id:
                try:
                    from research_followups import ResearchFollowups
                except ModuleNotFoundError:
                    from .research_followups import ResearchFollowups
                followup_link = ResearchFollowups(self.osint, self.intelligence).permits_imagery_link(
                    task_id, record["assessmentId"], record["graphRevisionId"], record["graphElementId"])
                if not followup_link:
                    raise OSINTValidationError("selected assessment does not belong to this task or its exact frozen imagery followup")
            graph = self.intelligence.get_graph(record["assessmentId"])
            if not graph or graph["graphRevisionId"] != record["graphRevisionId"]:
                raise IntelligenceConflict("graph revision changed; reload before linking imagery")
            element = next((item for item in graph["nodes"] + graph["edges"] if item["id"] == record["graphElementId"]), None)
            if element is None:
                raise OSINTValidationError("graphElementId does not identify an object or assertion in the saved graph")
            if record["sourceItemId"]:
                # Image discovery is deliberately excluded from model text
                # inputs. Later imagery corroboration therefore keeps its own
                # immutable task material provenance, without pretending to
                # have been part of the report's original evidence snapshot.
                in_snapshot = any(item.get("itemId") == record["sourceItemId"] for item in assessment.get("sourceSnapshot", []))
                record["sourceRelationship"] = "followup-task-material" if followup_link else "assessment-source-snapshot" if in_snapshot else "supplemental-task-material"
            if record["propertyPath"]:
                record["targetPropertySnapshot"] = _property_target(element, record["propertyPath"])
            record["targetLabel"] = element.get("label") or element.get("relation")
            record["targetKind"] = "relation" if "from" in element else "object"
            record["graphContentHash"] = graph.get("contentHash")
            record["sourceSnapshotHash"] = self.intelligence._snapshot_hash(assessment.get("sourceSnapshot") or [])
        record.update(type="PublicImageryEvidenceReference", taskId=task_id, reviewStatus="pending-review", sourceVerification="pending-human-review", imageTypeVerification="pending-human-review", licenseVerification="pending-human-review", independenceVerified=False, truthVerified=False, confidencePromotion=False, linkageStatus="linked-pending-review" if all(links) else "pending-link", captureDateStatus="supplied-unverified" if record["captureDate"] else "unknown", publishedDateStatus="supplied-unverified" if record["publishedDate"] else "unknown", hashScope="reference-metadata-only", fixedLimitations=list(FIXED_LIMITATIONS), fetched=False)
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.osint.lock, self.osint.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT payload FROM osint_imagery_evidence WHERE task_id=? AND content_hash=?", (task_id, digest)).fetchone()
            if old:
                return {"imageryEvidence": json.loads(old["payload"]), "duplicate": True}
            record.update(id="imagery-evidence-" + secrets.token_hex(8), contentHash=digest, createdAt=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
            con.execute("INSERT INTO osint_imagery_evidence(evidence_id,task_id,assessment_id,content_hash,payload,created_at) VALUES(?,?,?,?,?,?)", (record["id"], task_id, record["assessmentId"], digest, json.dumps(record, ensure_ascii=False, sort_keys=True), record["createdAt"]))
            return {"imageryEvidence": record, "duplicate": False}

    def list(self, task_id):
        self._task(task_id)
        with self.osint.connect() as con:
            records = [json.loads(row["payload"]) for row in con.execute("SELECT payload FROM osint_imagery_evidence WHERE task_id=? ORDER BY created_at,evidence_id", (task_id,))]
        for record in records:
            graph = self.intelligence.get_graph(record["assessmentId"]) if record["assessmentId"] else None
            record["staleGraph"] = bool(record["assessmentId"] and (not graph or graph["graphRevisionId"] != record["graphRevisionId"]))
        return records
