"""P4-08 requirements, calculations and verification traceability.

The service stores an immutable, reviewable verification plan. It deliberately
does not infer measurements, BOM/CAD completeness, manufacturing status or
flight-test results. Missing engineering material is represented as a data
request and keeps the matrix blocked/unknown.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import secrets

from psycopg2.extras import Json

from agent_admin.credentials import Principal, token_digest
from .evidence_repository import EvidenceRepository, artifact_json_hash


class TraceabilityValidationError(ValueError):
    pass


class TraceabilityConflictError(ValueError):
    pass


_ID = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_TYPES = {"requirement", "constraint", "calculation", "verification", "material", "data-request"}
_STATUSES = {"planned", "unknown", "blocked", "passed", "failed", "not-tested"}
_MISSING_TERMS = ("cad", "bom", "test", "measurement", "试验", "测量", "图纸", "物料")


def canonical_hash(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise TraceabilityValidationError("traceability content must be finite JSON") from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text(value, field, maximum=4000, required=True):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise TraceabilityValidationError(f"{field} must be bounded text")
    return value.strip()


def _id(value, field):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise TraceabilityValidationError(f"invalid {field}")
    return value


def _refs(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise TraceabilityValidationError("evidenceRefs must be a bounded array")
    result = []
    for ref in value:
        if not isinstance(ref, dict):
            raise TraceabilityValidationError("evidenceRefs must contain objects")
        if set(ref) - {"artifactId", "contentHash", "sourceId", "sourceVersion", "locator"}:
            raise TraceabilityValidationError("evidence reference contains unsupported fields")
        if "artifactId" not in ref and "sourceId" not in ref:
            raise TraceabilityValidationError("evidence reference needs artifactId or sourceId")
        item = dict(ref)
        for key in ("artifactId", "sourceId"):
            if key in item:
                item[key] = _id(item[key], key)
        for key in ("sourceVersion",):
            if key in item:
                item[key] = _text(item[key], key, 1000)
        if "locator" in item:
            if not isinstance(item["locator"], (dict, str)) or len(json.dumps(item["locator"], default=str)) > 2000:
                raise TraceabilityValidationError("evidence locator must be a bounded object or text")
        if not isinstance(item.get("contentHash"), str) or not _HASH.fullmatch(item["contentHash"]):
            raise TraceabilityValidationError("evidence contentHash must be sha256")
        result.append(item)
    return result


def validate_matrix(payload):
    """Validate and freeze a matrix payload without trusting client status flags."""
    if not isinstance(payload, dict):
        raise TraceabilityValidationError("matrix must be an object")
    allowed = {"title", "purpose", "items", "assumptions", "dataRequests", "manufacturingStatus", "flightTestStatus", "engineeringCertification"}
    if set(payload) - allowed:
        raise TraceabilityValidationError("matrix contains unsupported fields")
    title = _text(payload.get("title"), "title", 240)
    purpose = _text(payload.get("purpose"), "purpose", 4000)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 500:
        raise TraceabilityValidationError("items must contain one to five hundred entries")
    ids = set(); items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise TraceabilityValidationError("each traceability item must be an object")
        allowed_item = {"id", "type", "title", "inputs", "links", "responsibility", "method", "passCriteria", "evidenceRefs", "status", "dataRequest", "requiresData"}
        if set(raw) - allowed_item:
            raise TraceabilityValidationError("traceability item contains unsupported fields")
        item_id = _id(raw.get("id"), "item id")
        if item_id in ids:
            raise TraceabilityValidationError("duplicate traceability item id")
        ids.add(item_id)
        item_type = raw.get("type")
        if item_type not in _TYPES:
            raise TraceabilityValidationError("unsupported traceability item type")
        title_i = _text(raw.get("title"), "item title", 500)
        inputs = raw.get("inputs", [])
        links = raw.get("links", [])
        for name, value in (("inputs", inputs), ("links", links)):
            if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) for v in value):
                raise TraceabilityValidationError(f"{name} must be a bounded string array")
            for value_item in value:
                _id(value_item, f"{name} reference")
        responsibility = _text(raw.get("responsibility"), "responsibility", 500)
        method = _text(raw.get("method"), "method", 2000)
        pass_criteria = _text(raw.get("passCriteria"), "passCriteria", 2000)
        evidence = _refs(raw.get("evidenceRefs"))
        status = raw.get("status", "unknown")
        if status not in _STATUSES:
            raise TraceabilityValidationError("unsupported traceability status")
        if status == "passed":
            # Public-source snapshots and concept calculations establish
            # traceability, not a measurement accepted by a test authority.
            # There is no trusted measurement-ingestion contract in this stage.
            raise TraceabilityValidationError("passed requires a trusted measurement acceptance service; use planned or not-tested")
        data_request = raw.get("dataRequest")
        requires_data = raw.get("requiresData", [])
        if not isinstance(requires_data, list) or len(requires_data) > 30:
            raise TraceabilityValidationError("requiresData must be a bounded array")
        requires_data = [_text(v, "requiresData item", 500) for v in requires_data]
        if data_request is not None:
            data_request = _text(data_request, "dataRequest", 2000)
        if item_type == "data-request" and not data_request and not requires_data:
            data_request = title_i
        # A declared material/data gap is itself a blocking condition. Keep a
        # negative result if one was explicitly recorded, while never allowing
        # a merely planned row to hide the missing input.
        if (item_type == "data-request" or requires_data or data_request) and status != "failed":
            status = "blocked"
        if status == "passed" and not evidence:
            raise TraceabilityValidationError("a passed item requires evidence")
        item = {"id": item_id, "type": item_type, "title": title_i, "inputs": list(inputs), "links": list(links),
                "responsibility": responsibility, "method": method, "passCriteria": pass_criteria,
                "evidenceRefs": evidence, "status": status, "requiresData": requires_data}
        if data_request:
            item["dataRequest"] = data_request
        items.append(item)
    for item in items:
        for linked in item["inputs"] + item["links"]:
            if linked not in ids:
                raise TraceabilityValidationError(f"unknown traceability link: {linked}")
    assumptions = payload.get("assumptions", [])
    if not isinstance(assumptions, list) or len(assumptions) > 100:
        raise TraceabilityValidationError("assumptions must be a bounded array")
    assumptions = [_text(v, "assumption", 1000) for v in assumptions]
    requests = payload.get("dataRequests", [])
    if not isinstance(requests, list) or len(requests) > 100:
        raise TraceabilityValidationError("dataRequests must be a bounded array")
    requests = [_text(v, "data request", 1000) for v in requests]
    for item in items:
        requests.extend(item["requiresData"])
        if item.get("dataRequest"):
            requests.append(item["dataRequest"])
    # Client supplied completion/certification flags cannot grant a positive result.
    if payload.get("manufacturingStatus") not in (None, "not-started", "unknown", "data-required"):
        raise TraceabilityValidationError("manufacturing status cannot claim completion")
    if payload.get("flightTestStatus") not in (None, "not-tested", "unknown", "data-required"):
        raise TraceabilityValidationError("flight test status cannot claim pass")
    if payload.get("engineeringCertification") not in (None, False):
        raise TraceabilityValidationError("engineering certification is server-controlled and remains false")
    blocked = bool(requests) or any(item["status"] in {"unknown", "blocked", "not-tested"} for item in items)
    failed = any(item["status"] == "failed" for item in items)
    status = "failed" if failed else "blocked" if blocked else "passed" if all(item["status"] == "passed" for item in items) else "unknown"
    return {"schemaVersion": "traceability.v1", "title": title, "purpose": purpose, "items": items,
            "assumptions": assumptions, "dataRequests": sorted(set(requests)), "status": status,
            "manufacturingStatus": "data-required" if any(any(term in req.casefold() for term in _MISSING_TERMS) for req in requests) else "unknown",
            "flightTestStatus": "data-required" if any("test" in req.casefold() or "试验" in req for req in requests) else "not-tested",
            "engineeringCertification": False}


class TraceabilityService:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _authorize(cursor, actor, actor_token, *, write=False):
        if not isinstance(actor, Principal) or actor.kind != "human":
            raise PermissionError("authenticated human identity required")
        digest = token_digest(actor_token)
        if digest is None:
            raise PermissionError("current human token required")
        cursor.execute("""SELECT i.principal_id,i.roles_json FROM agent_admin_identity i
            JOIN agent_admin_token t USING(principal_id)
            WHERE i.principal_id=%s AND t.token_hash=%s AND t.revoked_at IS NULL
              AND t.expires_at>clock_timestamp() AND i.enabled AND i.kind='human'
              FOR SHARE OF i,t""", (actor.principal_id, digest))
        row = cursor.fetchone()
        if not row or not set(row["roles_json"]).intersection({"admin", "operator"} if write else {"admin", "operator", "reviewer"}):
            raise PermissionError("active human role is required")
        return row

    @staticmethod
    def _run(cursor, run_id):
        cursor.execute("SELECT r.input_json,p.profile_json,p.profile_hash FROM agent_run r "
                       "JOIN agent_run_profile_snapshot p USING(run_id) WHERE r.run_id=%s", (run_id,))
        run = cursor.fetchone()
        if not run:
            raise TraceabilityConflictError("run profile snapshot is missing")
        if artifact_json_hash(run["profile_json"]) != run["profile_hash"]:
            raise TraceabilityConflictError("run profile snapshot hash mismatch")
        return dict(run)

    @staticmethod
    def _workspace(cursor, identity, run_id, run_input, supplied):
        keys = ("workspaceId", "workspaceRevision", "workspaceRevisionHash")
        frozen = tuple(run_input.get(key) for key in keys)
        provided = any(value is not None for value in supplied)
        if provided:
            _id(supplied[0], "workspace id")
            if (type(supplied[1]) is not int or supplied[1] < 1 or not isinstance(supplied[2], str)
                    or not _HASH.fullmatch(supplied[2])):
                raise TraceabilityValidationError("complete workspace revision and sha256 binding required")
        if not any(value is not None for value in frozen):
            if provided:
                raise TraceabilityConflictError("Run has no bound workspace revision")
            return (None, None, None)
        workspace_id, revision, digest = frozen
        _id(workspace_id, "workspace id")
        if type(revision) is not int or revision < 1 or not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise TraceabilityConflictError("Run workspace binding is invalid")
        if provided and supplied != frozen:
            raise TraceabilityConflictError("caller workspace binding differs from Run")
        cursor.execute("SELECT owner_id FROM agent_research_workspace WHERE workspace_id=%s FOR SHARE", (workspace_id,))
        workspace = cursor.fetchone()
        if not workspace:
            raise TraceabilityConflictError("workspace is missing")
        if workspace["owner_id"] != identity["principal_id"] and "admin" not in identity["roles_json"]:
            raise PermissionError("workspace owner or administrator required")
        cursor.execute("SELECT content_json,content_hash FROM agent_workspace_revision WHERE workspace_id=%s AND revision=%s",
                       (workspace_id, revision))
        version = cursor.fetchone()
        if not version or version["content_hash"] != digest or canonical_hash(version["content_json"]) != digest:
            raise TraceabilityConflictError("frozen workspace revision hash mismatch")
        current, visited = run_id, set()
        for _ in range(16):
            if current in visited:
                raise TraceabilityConflictError("workspace handoff contains a cycle")
            visited.add(current)
            cursor.execute("SELECT input_json FROM agent_run WHERE run_id=%s", (current,))
            ancestor = cursor.fetchone()
            if not ancestor or tuple((ancestor["input_json"] or {}).get(key) for key in keys) != frozen:
                raise TraceabilityConflictError("workspace handoff revision differs from Run")
            cursor.execute("SELECT workspace_id,revision,revision_hash FROM agent_workspace_run WHERE run_id=%s", (current,))
            binding = cursor.fetchone()
            if binding:
                if (binding["workspace_id"], binding["revision"], binding["revision_hash"]) != frozen:
                    raise TraceabilityConflictError("immutable workspace admission differs from Run")
                return frozen
            cursor.execute("SELECT run_id FROM agent_run_handoff WHERE child_run_id=%s", (current,))
            parent = cursor.fetchone()
            if not parent:
                break
            current = parent["run_id"]
        raise TraceabilityConflictError("Run has no immutable workspace admission")

    def _bind_references(self, cursor, run_id, matrix):
        sources = EvidenceRepository(self.connection_factory)
        for item in matrix["items"]:
            references = []
            for ref in item["evidenceRefs"]:
                if ref.get("sourceId"):
                    try:
                        source = sources._source(cursor, run_id, ref["sourceId"])
                    except ValueError as exc:
                        raise TraceabilityConflictError(str(exc)) from None
                    version = source["metadata"].get("sourceVersion") or source["artifactHash"]
                    if (ref["contentHash"] != source["contentHash"]
                            or ref.get("artifactId", source["artifactId"]) != source["artifactId"]
                            or ref.get("sourceVersion", version) != version):
                        raise TraceabilityConflictError("evidence source identity/version/hash mismatch")
                    locator = ref.get("locator", source["locator"])
                    if isinstance(locator, str):
                        try:
                            locator = json.loads(locator)
                        except ValueError:
                            raise TraceabilityConflictError("evidence source locator differs from persisted source") from None
                    if locator != source["locator"]:
                        raise TraceabilityConflictError("evidence source locator differs from persisted source")
                    references.append({"sourceId": source["sourceId"], "artifactId": source["artifactId"],
                                       "contentHash": source["contentHash"], "sourceVersion": version,
                                       "locator": deepcopy(source["locator"])})
                else:
                    cursor.execute("SELECT run_id,content_hash,content_json FROM agent_run_artifact WHERE artifact_id=%s",
                                   (ref["artifactId"],))
                    artifact = cursor.fetchone()
                    if (not artifact or artifact["run_id"] != run_id or artifact["content_hash"] != ref["contentHash"]
                            or artifact_json_hash(artifact["content_json"]) != ref["contentHash"]):
                        raise TraceabilityConflictError("evidence artifact is missing, foreign or its hash differs")
                    if set(ref).intersection({"sourceVersion", "locator"}):
                        raise TraceabilityValidationError("source version and locator require a persisted sourceId")
                    references.append({"artifactId": ref["artifactId"], "contentHash": ref["contentHash"]})
            item["evidenceRefs"] = references
        return matrix

    def create(self, actor, run_id, payload, *, actor_token, workspace_id=None, workspace_revision=None, workspace_revision_hash=None):
        _id(run_id, "run id")
        matrix = validate_matrix(payload)
        matrix_id = "traceability-" + secrets.token_hex(12)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                identity = self._authorize(cursor, actor, actor_token, write=True)
                profile = self._run(cursor, run_id)
                workspace_id, workspace_revision, workspace_revision_hash = self._workspace(
                    cursor, identity, run_id, profile["input_json"] or {},
                    (workspace_id, workspace_revision, workspace_revision_hash))
                matrix = self._bind_references(cursor, run_id, matrix)
                matrix_hash = canonical_hash(matrix)
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("traceability:" + run_id,))
                cursor.execute("SELECT matrix_id FROM agent_traceability_matrix WHERE run_id=%s AND matrix_hash=%s", (run_id, matrix_hash))
                prior = cursor.fetchone()
                if prior:
                    return self._get(cursor, identity, prior["matrix_id"])
                cursor.execute("""INSERT INTO agent_traceability_matrix(matrix_id,run_id,workspace_id,workspace_revision,
                    workspace_revision_hash,run_profile_hash,matrix_json,matrix_hash,status,created_by)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (matrix_id, run_id, workspace_id, workspace_revision,
                    workspace_revision_hash, profile["profile_hash"], Json(matrix), matrix_hash, matrix["status"], identity["principal_id"]))
                for item in matrix["items"]:
                    cursor.execute("""INSERT INTO agent_traceability_item(item_id,matrix_id,item_type,item_json,item_hash,status)
                        VALUES(%s,%s,%s,%s,%s,%s)""", (matrix_id + ":" + item["id"], matrix_id, item["type"], Json(item), canonical_hash(item), item["status"]))
                return self._get(cursor, identity, matrix_id)

    def get(self, actor, matrix_id, *, actor_token):
        _id(matrix_id, "matrix id")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                identity = self._authorize(cursor, actor, actor_token)
                return self._get(cursor, identity, matrix_id)

    def list(self, actor, run_id, *, actor_token):
        _id(run_id, "run id")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                identity = self._authorize(cursor, actor, actor_token)
                run = self._run(cursor, run_id)
                self._workspace(cursor, identity, run_id, run["input_json"] or {}, (None, None, None))
                cursor.execute("SELECT matrix_id,matrix_json,status,created_at,matrix_hash FROM agent_traceability_matrix "
                               "WHERE run_id=%s ORDER BY created_at,matrix_id", (run_id,))
                return [dict(row) for row in cursor.fetchall()]

    def _get(self, cursor, identity, matrix_id):
        cursor.execute("SELECT * FROM agent_traceability_matrix WHERE matrix_id=%s", (matrix_id,))
        row = cursor.fetchone()
        if not row:
            raise TraceabilityConflictError("traceability matrix not found")
        profile = self._run(cursor, row["run_id"])
        binding = self._workspace(cursor, identity, row["run_id"], profile["input_json"] or {},
                                  (row["workspace_id"], row["workspace_revision"], row["workspace_revision_hash"]))
        matrix = row["matrix_json"]
        if (canonical_hash(matrix) != row["matrix_hash"] or row["run_profile_hash"] != profile["profile_hash"]
                or row["status"] != matrix["status"] or any(item["status"] == "passed" for item in matrix["items"])
                or binding != (row["workspace_id"], row["workspace_revision"], row["workspace_revision_hash"])):
            raise TraceabilityConflictError("traceability binding or hash mismatch")
        if canonical_hash(self._bind_references(cursor, row["run_id"], deepcopy(matrix))) != row["matrix_hash"]:
            raise TraceabilityConflictError("traceability evidence binding mismatch")
        cursor.execute("SELECT * FROM agent_traceability_item WHERE matrix_id=%s ORDER BY item_id", (matrix_id,))
        items = [dict(item) for item in cursor.fetchall()]
        expected = {item["id"]: item for item in matrix["items"]}
        if len(items) != len(expected):
            raise TraceabilityConflictError("traceability item set mismatch")
        for item in items:
            original = expected.get(item["item_json"].get("id"))
            if (original is None or item["item_json"] != original or canonical_hash(item["item_json"]) != item["item_hash"]
                    or item["status"] != original["status"] or item["item_type"] != original["type"]):
                raise TraceabilityConflictError("traceability item hash mismatch")
        return {"matrixId": matrix_id, "runId": row["run_id"], "workspaceId": row["workspace_id"],
                "workspaceRevision": row["workspace_revision"], "workspaceRevisionHash": row["workspace_revision_hash"],
                "runProfileHash": row["run_profile_hash"], "matrixHash": row["matrix_hash"],
                "status": row["status"], "createdBy": row["created_by"], "createdAt": row["created_at"],
                "matrix": matrix, "items": items}

