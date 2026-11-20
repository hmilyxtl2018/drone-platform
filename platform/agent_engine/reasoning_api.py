"""Authenticated audit and explicit human review for Ontology Reasoning Harness.

Reading a historical Run never creates a policy snapshot or rewrites evidence.
Publication through candidate review creates a Run-scoped artifact only; it does
not promote a research relation into an external ontology fact.
"""
from __future__ import annotations

from copy import deepcopy
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from agent_admin.api import SESSION_COOKIE
from agent_admin.credentials import AdminAuth


REVIEW_ACTIONS = frozenset({"accept", "reject", "request-evidence", "mark-conflict", "withdraw", "publish"})


def create_reasoning_router(connection_factory, *, auth=None, repository=None):
    """Build the router; injectable auth/repository keep contract tests offline."""
    from .evidence_repository import EvidenceRepository

    auth = auth or AdminAuth(connection_factory)
    injected = repository is not None
    repository = repository or EvidenceRepository(connection_factory)
    router = APIRouter(prefix="/api/agent-admin", tags=["reasoning-harness"])

    def access(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (origin and origin.rstrip("/") != str(request.base_url).rstrip("/")) or request.headers.get("sec-fetch-site") == "cross-site":
                raise HTTPException(403, "cross-origin reasoning request rejected")
        bearer = request.headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None or getattr(actor, "kind", None) != "human":
            raise HTTPException(401, "authenticated local operator required")
        return actor

    def require_run(run_id):
        if injected:
            exists = repository.get_run(run_id)
        else:
            # A Run's raw output can be very large. Checking existence must not
            # deserialize complete source bodies just to list audit summaries.
            with connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT run_id FROM agent_run WHERE run_id=%s", (run_id,))
                    exists = cursor.fetchone()
        if not exists:
            raise HTTPException(404, "run not found")

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except HTTPException:
            raise
        except PermissionError:
            raise HTTPException(403, "reasoning action is not permitted") from None
        except LookupError:
            raise HTTPException(404, "reasoning record not found") from None
        except ValueError as exc:
            # Schema errors can include source text; expose only a bounded
            # service-defined message rather than serializing raw payloads.
            reason = str(exc)
            if "conflict" in reason.lower() or "version" in reason.lower() or "already" in reason.lower():
                raise HTTPException(409, "reasoning state or policy version changed; refresh before retrying") from None
            raise HTTPException(400, "reasoning request failed validation") from None
        except Exception:
            raise HTTPException(409, "reasoning data unavailable; inspect server verification records") from None

    @router.get("/runs/{run_id}/reasoning-policy")
    def run_policy(run_id: str, actor=Depends(access)):
        require_run(run_id)
        value = invoke(repository.get_reasoning_policy, run_id)
        return value or {"runId": run_id, "status": "not-configured", "policyHash": None}

    @router.get("/runs/{run_id}/reasoning-checkpoints")
    def checkpoints(run_id: str, actor=Depends(access)):
        require_run(run_id)
        return invoke(repository.list_reasoning_checkpoints, run_id)

    @router.get("/runs/{run_id}/quality-gates")
    def gates(run_id: str, actor=Depends(access)):
        require_run(run_id)
        return invoke(repository.list_reasoning_gates, run_id)

    @router.get("/runs/{run_id}/relation-candidates")
    def candidates(run_id: str, actor=Depends(access)):
        require_run(run_id)
        return invoke(repository.list_relation_candidates, run_id)

    @router.post("/runs/{run_id}/relation-candidates/{candidate_id}/review")
    def review(run_id: str, candidate_id: str, body: dict, actor=Depends(access)):
        if not actor.permits("reviewer"):
            raise HTTPException(403, "reviewer role required")
        require_run(run_id)
        if set(body) - {"action", "comment", "idempotencyKey"}:
            raise HTTPException(400, "unsupported relation review fields")
        action, comment, key = body.get("action"), body.get("comment", ""), body.get("idempotencyKey")
        if action not in REVIEW_ACTIONS:
            raise HTTPException(400, "unsupported relation review action")
        if not isinstance(comment, str) or len(comment) > 4000:
            raise HTTPException(400, "comment must be a bounded string")
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise HTTPException(400, "idempotencyKey is required")
        return invoke(repository.review_relation_candidate, run_id, candidate_id, action, actor,
                      comment=comment, idempotency_key=key)

    @router.get("/reasoning-policies/{agent_key}")
    def agent_policy(agent_key: str, version: int | None = None, profileVersion: str | None = None,
                     actor=Depends(access)):
        if version is not None and version < 1:
            raise HTTPException(400, "version must be a positive integer")
        if profileVersion is not None and (not profileVersion.strip() or len(profileVersion) > 120):
            raise HTTPException(400, "profileVersion is invalid")
        kwargs = {}
        if version is not None:
            kwargs['version'] = version
        if profileVersion is not None:
            kwargs['profile_version'] = profileVersion
        value = invoke(repository.load_reasoning_policy, agent_key, **kwargs)
        return value or {"agentKey": agent_key, "status": "not-configured", "version": None}

    @router.put("/reasoning-policies/{agent_key}")
    def save_policy(agent_key: str, body: dict, actor=Depends(access)):
        if not actor.permits("admin"):
            raise HTTPException(403, "admin role required")
        if set(body) != {"policy", "expectedVersion"} or not isinstance(body.get("policy"), dict):
            raise HTTPException(400, "policy and expectedVersion are required")
        if type(body["expectedVersion"]) is not int or body["expectedVersion"] < 0:
            raise HTTPException(400, "expectedVersion must be a nonnegative integer")
        if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > 128000:
            raise HTTPException(400, "reasoning policy exceeds configured cap")
        return invoke(repository.save_reasoning_policy, agent_key, body["policy"], actor,
                      expected_version=body["expectedVersion"])

    def ontology(run_id):
        if injected:
            value = repository.get_run(run_id)
            output = value.get("output_json") or {}
            graph = output.get("ontology") or (output.get("judgmentReport") or {}).get("ontology")
        else:
            # Read only the graph projection, never duplicate all source bodies.
            with connection_factory() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("""SELECT content_json->'ontology' AS ontology
                                      FROM agent_run_artifact WHERE run_id=%s
                                        AND artifact_type='future-candidate-report'
                                      ORDER BY created_at DESC,artifact_id DESC LIMIT 1""", (run_id,))
                    row = cursor.fetchone()
                    graph = row.get("ontology") if row else None
                    if not graph:
                        cursor.execute("SELECT output_json->'ontology' AS ontology FROM agent_run WHERE run_id=%s", (run_id,))
                        row = cursor.fetchone()
                        graph = row.get("ontology") if row else None
        if isinstance(graph, dict):
            return {"runId": run_id, **deepcopy(graph)}
        return {"runId": run_id, "schemaVersion": "1.0", "nodes": [], "edges": [], "assertions": [],
                "truthStatus": "unverified", "status": "not-generated"}

    @router.get("/runs/{run_id}/ontology-graph")
    def run_graph(run_id: str, actor=Depends(access)):
        require_run(run_id)
        return invoke(ontology, run_id)

    return router
