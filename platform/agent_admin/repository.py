"""Narrow PostgreSQL management and human review services."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
import re
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, SchemaError
from psycopg2.extras import Json

from agent_engine.evidence_repository import EvidenceRepository
from agent_engine.repository import new_id
from .credentials import Principal


RESOURCE_TABLES = {"agents": ("agent_definition", "agent_key"), "models": ("agent_model_policy", "agent_key"),
                   "tools": ("tool_registry", "tool_key"), "providers": ("provider_registry", "provider_key"),
                   "sources": ("data_source_registry", "source_key"), "scope": ("agent_scope", "agent_key"),
                   "resources": ("agent_resource_policy", "agent_key"),
                   "versions": ("tool_version", "tool_key"), "bindings": ("agent_tool_binding", "agent_key")}
FIELDS = {
    "agents": {"display_name", "description", "domain", "status", "current_version", "system_prompt"},
    "models": {"model_provider", "model_name", "temperature", "max_output_tokens", "structured_output_required",
               "planner_enabled", "prompt_version", "config_json"},
    "tools": {"tool_name", "category", "description", "protocol", "input_schema", "output_schema", "tags_json",
              "access_mode", "risk_level", "status", "current_version", "endpoint"},
    "providers": {"provider_type", "base_url", "credential_ref", "enabled", "version", "config_json"},
    "sources": {"connector", "root_uri", "credential_ref", "read_only", "enabled", "policy_version", "config_json"},
    "scope": {"responsibility_json", "input_entities_json", "output_entities_json", "allowed_write_json", "forbidden_actions_json"},
    "resources": {"max_parallel_tools", "max_retries_per_tool", "permitted_risk_levels_json"},
    "versions": {"endpoint", "input_schema", "output_schema", "changelog", "status", "execution_transport", "adapter_version"},
    "bindings": {"tool_version", "enabled", "approval_required", "config_json"},
}
TEXT_JSON = {"models": {"config_json"}, "scope": FIELDS["scope"], "resources": {"permitted_risk_levels_json"}}
JSON_FIELDS = {"input_schema", "output_schema", "tags_json", "config_json", "responsibility_json", "input_entities_json",
               "output_entities_json", "allowed_write_json", "forbidden_actions_json", "permitted_risk_levels_json", "policy_json"}
DECISIONS = frozenset({"accept", "reject", "request-evidence", "resolve-conflict"})


def reject_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {"apikey", "authorization", "password", "passwd", "secret", "accesstoken", "refreshtoken", "privatekey", "token"}:
                raise ValueError("raw credentials are not accepted")
            if normalized == "credentialref" and child is not None and (not isinstance(child, str)
                    or not re.fullmatch(r"(?:env:)?[A-Z_][A-Z0-9_]{0,127}", child)):
                raise ValueError("credential references must name server environment variables")
            reject_secrets(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            reject_secrets(child)
    elif isinstance(value, str):
        if re.search(r"\b(?:sk|fc)-[A-Za-z0-9_-]{8,}\b|\bBearer\s+[A-Za-z0-9._~-]{8,}", value, re.I):
            raise ValueError("raw credentials are not accepted")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("configuration numbers must be finite")


def validate_config(resource, key, payload):
    if resource not in FIELDS or not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
        raise ValueError("invalid configuration resource")
    if not isinstance(payload, dict) or not payload or set(payload) - FIELDS[resource]:
        raise ValueError("unknown or empty configuration fields")
    reject_secrets(payload)
    result = deepcopy(payload)
    for name in ("credential_ref", "base_url", "endpoint", "model_name", "model_provider", "protocol", "status", "current_version", "execution_transport", "adapter_version"):
        if name in result and result[name] is not None and not isinstance(result[name], str):
            raise ValueError(name + " must be text")
    for name in ("enabled", "read_only", "approval_required"):
        if name in result and type(result[name]) is not bool:
            raise ValueError(name + " must be boolean")
    for name in ("structured_output_required", "planner_enabled"):
        if name in result and type(result[name]) not in (int, bool):
            raise ValueError(name + " must be boolean or 0/1")
        if name in result and result[name] not in (0, 1):
            raise ValueError(name + " must be 0/1")
    if "credential_ref" in result and result["credential_ref"] is not None and not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]{0,127}", result["credential_ref"]):
        raise ValueError("credential_ref must be an environment reference")
    for name in ("base_url", "endpoint"):
        url = result.get(name)
        if url is None:
            continue
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or any(c.isspace() for c in url):
            raise ValueError("configured endpoint must be credential-free HTTPS")
    if "root_uri" in result:
        value = result["root_uri"]
        if not isinstance(value, str) or not value or "?" in value or "#" in value or re.search(r"://[^/]*@", value):
            raise ValueError("source root must not contain credentials or query")
    enums = {"status": {"active", "inactive", "disabled", "draft", "retired", "enabled"},
             "provider_type": {"model", "web", "http"}, "access_mode": {"read_only", "write", "read_write"},
             "risk_level": {"low", "medium", "high", "critical"}, "protocol": {"python", "http", "https"}}
    for name, allowed in enums.items():
        if name in result and result[name] not in allowed:
            raise ValueError("unsupported " + name)
    if "execution_transport" in result and result["execution_transport"] not in {None, "python", "http", "https"}:
        raise ValueError("unsupported execution transport")
    if result.get("adapter_version") is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", result["adapter_version"]):
        raise ValueError("invalid adapter version")
    for name in ("max_output_tokens", "max_parallel_tools", "max_retries_per_tool"):
        if name in result and (type(result[name]) is not int or not 0 <= result[name] <= 8192 or (name != "max_retries_per_tool" and result[name] == 0)):
            raise ValueError("invalid resource limit")
    if "temperature" in result and (type(result["temperature"]) not in (int, float) or not 0 <= result["temperature"] <= 2):
        raise ValueError("invalid model temperature")
    for name in ("input_schema", "output_schema"):
        if name in result:
            if not isinstance(result[name], dict):
                raise ValueError("schema must be an object")
            def refs(item):
                if isinstance(item, dict):
                    if "$ref" in item or "$dynamicRef" in item:
                        raise ValueError("remote or recursive schema references are unsupported")
                    for child in item.values(): refs(child)
                elif isinstance(item, list):
                    for child in item: refs(child)
            refs(result[name])
            try:
                Draft202012Validator.check_schema(result[name])
            except SchemaError:
                raise ValueError("invalid JSON schema") from None
    for name in JSON_FIELDS.intersection(result):
        if not isinstance(result[name], (dict, list)):
            raise ValueError(name + " must be JSON object or array")
        if name in {"config_json", "responsibility_json", "input_schema", "output_schema", "policy_json"} and not isinstance(result[name], dict):
            raise ValueError(name + " must be a JSON object")
        if name in {"input_entities_json", "output_entities_json", "allowed_write_json", "forbidden_actions_json", "permitted_risk_levels_json", "tags_json"} and not isinstance(result[name], list):
            raise ValueError(name + " must be a JSON array")
    if resource == "models" and "config_json" in result and "contextPolicy" in result["config_json"]:
        policy = result["config_json"]["contextPolicy"]
        if not isinstance(policy, dict):
            raise ValueError("contextPolicy must be a JSON object")
        if policy.get("schemaVersion", "context-policy.v1") != "context-policy.v1":
            raise ValueError("unsupported contextPolicy schema version")
        max_bytes = policy.get("maxBytes", 120000)
        if type(max_bytes) is not int or not 4096 <= max_bytes <= 2_000_000:
            raise ValueError("contextPolicy.maxBytes must be between 4096 and 2000000")
        for name in ("allowPrivateState", "allowHistoricalMemory", "toolProjection"):
            if name in policy and type(policy[name]) is not bool:
                raise ValueError("contextPolicy." + name + " must be boolean")
        if policy.get("freshness", "pinned") not in {"pinned", "run", "live"}:
            raise ValueError("contextPolicy.freshness must be pinned, run or live")
        for name in ("maxEvidenceItems", "maxMemoryItems"):
            if name in policy and (type(policy[name]) is not int or not 0 <= policy[name] <= 512):
                raise ValueError("contextPolicy." + name + " must be between 0 and 512")
        projections = policy.get("roleProjections", {})
        if not isinstance(projections, dict):
            raise ValueError("contextPolicy.roleProjections must be an object")
        for role, projection in projections.items():
            if not isinstance(role, str) or not isinstance(projection, dict):
                raise ValueError("contextPolicy role projections must be objects")
            for field in ("taskFields", "coordinationFields", "evidenceFields", "upstreamFields"):
                if field in projection and (not isinstance(projection[field], list) or
                                             any(not isinstance(item, str) for item in projection[field])):
                    raise ValueError("contextPolicy projection fields must be string arrays")
    for name, value in result.items():
        if isinstance(value, str) and len(value) > (30000 if name == "system_prompt" else 6000):
            raise ValueError("configuration text exceeds limit")
    if len(json.dumps(result, allow_nan=False).encode()) > 200_000:
        raise ValueError("configuration exceeds limit")
    return result


class AdminRepository(EvidenceRepository):
    def list_config(self, resource):
        if resource not in RESOURCE_TABLES:
            raise ValueError("invalid configuration resource")
        table, key = RESOURCE_TABLES[resource]
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute(f"SELECT * FROM {table} ORDER BY {key}")
                rows = [dict(row) for row in c.fetchall()]
                if resource in {"providers", "sources"}:
                    for row in rows:
                        ref = row.get("credential_ref") or ""
                        row["configured"] = bool(os.environ.get(ref[4:], "").strip()) if ref.startswith("env:") else not ref
                        if resource == "providers":
                            row.update(self._provider_connection_diagnostic(c, row))
                return rows

    @staticmethod
    def _provider_connection_diagnostic(cursor, provider):
        """Report a recorded result for this configuration, never probe it.

        Credential presence alone is not a successful connection. Audit records
        belong to immutable Run profiles, so a changed endpoint, reference,
        version or configuration cannot inherit another configuration's health.
        Only metadata is selected; prompts, provider bodies and credentials are
        neither read nor returned here.
        """
        unknown = {"connectionStatus": "unknown", "lastConnectionCheckedAt": None}
        if not provider["configured"]:
            return {"connectionStatus": "not-configured", "lastConnectionCheckedAt": None}
        if not provider.get("enabled"):
            return unknown
        if provider["provider_type"] == "model":
            frozen = {key: provider.get(key) for key in (
                "provider_key", "base_url", "credential_ref", "enabled", "version", "config_json")}
            cursor.execute("""SELECT a.status,a.created_at AS checked_at
                FROM agent_model_call a JOIN agent_run_profile_snapshot s USING(run_id)
                WHERE a.provider_key=%s AND a.status IN ('succeeded','failed','timeout')
                  AND s.profile_json #> '{modelPolicy,providerSnapshot}' = %s::jsonb
                ORDER BY a.created_at DESC,a.call_id DESC LIMIT 1""",
                (provider["provider_key"], Json(frozen)))
        elif provider["provider_type"] == "web":
            ref = provider.get("credential_ref") or ""
            if not ref.startswith("env:"):
                return unknown
            frozen = {"providerKey": provider["provider_key"], "baseUrl": provider["base_url"],
                      "credentialRef": ref[4:], "version": provider["version"]}
            # Older Firecrawl snapshots omit config. They can establish a
            # result only for the empty configuration, never a later custom one.
            cursor.execute("""SELECT a.status,COALESCE(a.finished_at,a.created_at) AS checked_at
                FROM agent_tool_call a JOIN agent_run_profile_snapshot s USING(run_id)
                WHERE a.tool_key IN ('web.search','web.scrape')
                  AND a.status IN ('succeeded','failed','timeout')
                  AND COALESCE(a.output_json->>'cacheHit','false') <> 'true'
                  AND s.profile_json #> '{sourcePolicy,firecrawlProvider}' @> %s::jsonb
                  AND COALESCE(s.profile_json #> '{sourcePolicy,firecrawlProvider,config}',
                               '{}'::jsonb) = %s::jsonb
                ORDER BY COALESCE(a.finished_at,a.created_at) DESC,a.call_id DESC LIMIT 1""",
                (Json(frozen), Json(provider.get("config_json") or {})))
        else:
            return unknown
        latest = cursor.fetchone()
        if not latest:
            return unknown
        return {"connectionStatus": "success" if latest["status"] == "succeeded" else "failed",
                "lastConnectionCheckedAt": latest["checked_at"]}

    def write_config(self, resource, key, payload, actor, *, create=False):
        if not isinstance(actor, Principal) or not actor.permits("admin"):
            raise PermissionError("admin role required")
        compound = key.split("~")
        if resource in {"versions", "bindings"}:
            if len(compound) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", part) for part in compound):
                raise ValueError("compound key must be tool~version or agent~tool")
            if resource == "versions" and not create:
                raise ValueError("tool versions are immutable; create a new version")
            validated_key = compound[0]
        else:
            validated_key = key
        payload = validate_config(resource, validated_key, payload)
        table, key_column = RESOURCE_TABLES[resource]
        encoded = {name: (json.dumps(value) if name in TEXT_JSON.get(resource, set()) else Json(value))
                   if name in JSON_FIELDS else value for name, value in payload.items()}
        if resource == "bindings":
            for name in ("enabled", "approval_required"):
                if name in encoded:
                    encoded[name] = int(encoded[name])
        if resource == "models":
            for name in ("planner_enabled", "structured_output_required"):
                if name in encoded:
                    encoded[name] = int(encoded[name])
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent-admin-profile-config',0))")
                c.execute("SELECT kind,enabled,roles_json FROM agent_admin_identity WHERE principal_id=%s", (actor.principal_id,))
                identity = c.fetchone()
                if not identity or identity["kind"] != "human" or not identity["enabled"] or "admin" not in identity["roles_json"]:
                    raise PermissionError("enabled human admin identity required")
                where, key_values = f"{key_column}=%s", (key,)
                if resource in {"versions", "bindings"}:
                    secondary = "version" if resource == "versions" else "tool_key"
                    where, key_values = f"{key_column}=%s AND {secondary}=%s", tuple(compound)
                c.execute(f"SELECT * FROM {table} WHERE {where} FOR UPDATE", key_values)
                previous = c.fetchone()
                if create and previous:
                    raise ValueError("configuration already exists")
                if not create and not previous:
                    raise ValueError("configuration not found")
                if create:
                    columns = [key_column, *([secondary] if resource in {"versions", "bindings"} else []), *encoded]
                    c.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join(['%s'] * len(columns))})",
                              (*key_values, *encoded.values()))
                else:
                    if resource == "tools" and {"endpoint", "input_schema", "output_schema"}.intersection(payload):
                        raise ValueError("tool contract edits require a new tool version and explicit current_version")
                    c.execute(f"UPDATE {table} SET {','.join(name + '=%s' for name in encoded)} WHERE {where}",
                              (*encoded.values(), *key_values))
                if resource == "tools" and "current_version" in payload:
                    c.execute("SELECT endpoint,input_schema,output_schema FROM tool_version WHERE tool_key=%s AND version=%s",
                              (key, payload["current_version"]))
                    version = c.fetchone()
                    if not version:
                        raise ValueError("create immutable tool version before selecting it")
                    c.execute("UPDATE tool_registry SET endpoint=%s,input_schema=%s,output_schema=%s WHERE tool_key=%s",
                              (version["endpoint"], Json(version["input_schema"]), Json(version["output_schema"]), key))
                if resource == "bindings" and "tool_version" in payload:
                    c.execute("SELECT 1 FROM tool_version WHERE tool_key=%s AND version=%s", (compound[1], payload["tool_version"]))
                    if not c.fetchone():
                        raise ValueError("binding requires an existing immutable tool version")
                c.execute("INSERT INTO agent_admin_config_audit(audit_id,actor_id,resource_type,resource_key,operation,config_json) VALUES(%s,%s,%s,%s,%s,%s)",
                          (new_id("config-audit"), actor.principal_id, resource, key, "create" if create else "update", Json(payload)))
                c.execute(f"SELECT * FROM {table} WHERE {where}", key_values)
                return dict(c.fetchone())

    def disable_config(self, resource, key, actor):
        if resource in {"providers", "sources"}:
            return self.write_config(resource, key, {"enabled": False}, actor)
        if resource in {"agents", "tools"}:
            return self.write_config(resource, key, {"status": "disabled"}, actor)
        raise ValueError("this resource cannot be disabled")

    def list_runs(self, limit=50, *, agent_key=None):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid run limit")
        if agent_key is not None and (not isinstance(agent_key, str) or not agent_key or len(agent_key) > 128):
            raise ValueError("invalid agent key")
        where = " WHERE agent_key=%s" if agent_key is not None else ""
        parameters = (agent_key, limit) if agent_key is not None else (limit,)
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("""SELECT run_id,agent_key,agent_version,status,current_step_key,model_call_count,tool_call_count,
                    created_at,updated_at,completed_at,agent_layer,
                    COALESCE(input_json->>'question',input_json->'commonSettings'->>'taskGoal',input_json->>'taskGoal') AS task_goal,
                    left(COALESCE(input_json->>'question',input_json->'commonSettings'->>'taskGoal',input_json->>'taskGoal'),64) AS task_title
                    FROM agent_run""" + where + " ORDER BY created_at DESC,run_id LIMIT %s", parameters)
                return [dict(row) for row in c.fetchall()]

    def events_after(self, run_id, after=0, limit=200):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid event cursor")
        with self.connection_factory() as con:
            with con.cursor() as c:
                c.execute("SELECT * FROM agent_run_event WHERE run_id=%s AND event_id>%s ORDER BY event_id LIMIT %s", (run_id, after, limit))
                rows = [dict(row) for row in c.fetchall()]
        return {"events": rows, "nextCursor": rows[-1]["event_id"] if rows else after}

    def _review_subject(self, c, run_id, subject_type, subject_id):
        if subject_type == "candidate":
            c.execute("SELECT c.fingerprint,s.artifact_id,s.artifact_hash FROM agent_evidence_candidate c JOIN agent_source_snapshot s USING(source_id) WHERE c.candidate_id=%s AND c.run_id=%s", (subject_id, run_id))
            row = c.fetchone()
            if not row:
                raise ValueError("candidate not found in run")
            return row["fingerprint"], row["artifact_id"], row["artifact_hash"]
        if subject_type == "artifact":
            c.execute("SELECT artifact_id,content_hash FROM agent_run_artifact WHERE artifact_id=%s AND run_id=%s", (subject_id, run_id))
            row = c.fetchone()
            if not row:
                raise ValueError("artifact not found in run")
            return row["content_hash"], row["artifact_id"], row["content_hash"]
        raise ValueError("invalid review subject")

    def review(self, run_id, subject_type, subject_id, expected_hash, decision, reason, actor, conflict_id=None):
        from agent_engine.review import ReviewService
        return ReviewService(self).decide(run_id, actor, subject_type=subject_type, subject_id=subject_id,
            expected_hash=expected_hash, decision=decision, reason=reason, conflict_id=conflict_id)

    def list_reviews(self, run_id):
        from agent_engine.review import ReviewService
        return ReviewService(self).list_reviews(run_id)

    def trusted_review_states(self, run_id):
        from agent_engine.review import ReviewService
        return ReviewService(self).trusted_review_states(run_id)
