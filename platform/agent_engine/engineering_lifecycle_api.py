"""Human-gated Future Battlefield engineering lifecycle API."""
from __future__ import annotations

from agent_engine.application_identity import FUTURE_AGENT_KEYS

from copy import deepcopy
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg2.extras import Json

from agent_admin.api import SESSION_COOKIE
from agent_admin.credentials import AdminAuth, Principal, token_digest
from .engineering_lifecycle import (
    LifecycleConflictError,
    LifecycleValidationError,
    canonical_hash,
    initial_state,
    validate_stage_gate,
)


class EngineeringLifecycleService:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _authorize(cursor, actor, token, *, write=False):
        if not isinstance(actor, Principal) or actor.kind != "human" or not token_digest(token):
            raise PermissionError("authenticated human identity required")
        cursor.execute("""SELECT i.principal_id,i.roles_json
            FROM agent_admin_identity i JOIN agent_admin_token t USING(principal_id)
            WHERE i.principal_id=%s AND t.token_hash=%s AND t.revoked_at IS NULL
              AND t.expires_at>clock_timestamp() AND i.enabled AND i.kind='human'
              FOR SHARE OF i,t""", (actor.principal_id, token_digest(token)))
        row = cursor.fetchone()
        if not row or set(row["roles_json"]) != set(actor.roles):
            raise PermissionError("authenticated human identity required")
        roles = {"admin", "operator"} if write else {"admin", "operator", "reviewer"}
        if not set(row["roles_json"]).intersection(roles):
            raise PermissionError("authorized operator or reviewer role is required")
        return row

    @staticmethod
    def _source_run(cursor, run_id):
        cursor.execute("""SELECT r.run_id,r.agent_key,r.status,r.input_json,r.output_json,s.state_json
                          FROM agent_run r LEFT JOIN agent_run_state s USING(run_id)
                         WHERE r.run_id=%s FOR SHARE OF r""", (run_id,))
        row = cursor.fetchone()
        if not row:
            raise LookupError("research Run not found")
        if row["agent_key"] not in FUTURE_AGENT_KEYS:
            raise LifecycleValidationError("engineering lifecycle is only enabled for Future Battlefield research")
        return row

    @staticmethod
    def _source_hash(run):
        output = run.get("output_json") or {}
        # Future Battlefield pauses at human-review before copying the report
        # to agent_run.output_json. Freeze the composed report itself so the
        # gate can be reviewed while the Run is waiting for a human.
        state = run.get("state_json") or {}
        if isinstance(state, dict):
            report = ((state.get("steps") or {}).get("compose-judgment-report") or {}).get("judgmentReport")
            if isinstance(report, dict):
                output = report
        return canonical_hash(output)

    @staticmethod
    def _approval(cursor, run_id, gate, target_stage):
        human = gate.get("humanReview") if isinstance(gate, dict) else None
        if not isinstance(human, dict) or human.get("status") != "approved":
            raise LifecycleValidationError("every lifecycle transition requires an approved humanReview gate")
        approval_id = human.get("approvalId")
        cursor.execute("SELECT approval_id,run_id,status,reviewed_by,action FROM agent_approval_request WHERE approval_id=%s", (approval_id,))
        row = cursor.fetchone()
        if not row or row["run_id"] != run_id or row["status"] != "approved":
            raise LifecycleConflictError("approval is absent, pending, rejected, or belongs to another Run")
        if row["reviewed_by"] != human.get("reviewerId"):
            raise LifecycleConflictError("humanReview reviewer does not match persisted approval")
        expected = "publish:future-battlefield-report" if target_stage == "decision-support" else "engineering-gate:" + target_stage
        if row["action"] not in {expected, "publish:future-battlefield-report"}:
            raise LifecycleConflictError("approval action is not bound to this lifecycle gate")
        return row

    def create(self, run_id, actor, token):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                identity = self._authorize(cursor, actor, token, write=True)
                run = self._source_run(cursor, run_id)
                cursor.execute("SELECT run_id,revision,stage FROM future_battlefield_lifecycle WHERE run_id=%s ORDER BY revision DESC LIMIT 1", (run_id,))
                existing = cursor.fetchone()
                if existing:
                    return self._get(cursor, run_id)
                source_hash = self._source_hash(dict(run))
                state = initial_state(run_id, source_hash)
                cursor.execute("""INSERT INTO future_battlefield_lifecycle
                    (run_id,revision,stage,source_run_id,source_run_hash,state_json,state_hash,created_by)
                    VALUES(%s,1,'exploration',%s,%s,%s,%s,%s)""",
                    (run_id, run_id, source_hash, Json(state), canonical_hash(state), identity["principal_id"]))
                return self._get(cursor, run_id)

    def advance(self, run_id, actor, token, target_stage, gate):
        if not isinstance(target_stage, str):
            raise LifecycleValidationError("targetStage is required")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                identity = self._authorize(cursor, actor, token, write=True)
                run = self._source_run(cursor, run_id)
                cursor.execute("SELECT * FROM future_battlefield_lifecycle WHERE run_id=%s ORDER BY revision DESC LIMIT 1 FOR UPDATE", (run_id,))
                current = cursor.fetchone()
                if not current:
                    raise LifecycleConflictError("create the lifecycle at exploration before advancing")
                if canonical_hash(current["state_json"]) != current["state_hash"]:
                    raise LifecycleConflictError("current lifecycle state hash mismatch")
                source_hash = self._source_hash(dict(run))
                if source_hash != current["source_run_hash"]:
                    raise LifecycleConflictError("research Run output changed; create a new research Run")
                normalized = validate_stage_gate(current["stage"], target_stage, gate)
                approval = self._approval(cursor, run_id, gate, target_stage)
                report_hash = normalized.get("reportHash") if target_stage == "decision-support" else None
                if report_hash and report_hash != source_hash:
                    raise LifecycleConflictError("decision-support report hash is not the current Run output hash")
                revision = int(current["revision"]) + 1
                transition_id = "engineering-gate-" + secrets.token_hex(12)
                gate_hash = canonical_hash(normalized)
                state = deepcopy(current["state_json"])
                history = list(state.get("history", []))
                history.append({"revision": revision, "fromStage": current["stage"], "toStage": target_stage,
                                "gateHash": gate_hash, "approvalId": approval["approval_id"]})
                state.update({"stage": target_stage, "lastGateHash": gate_hash, "history": history,
                              "truthVerified": False, "engineeringCertification": False,
                              "manufacturingStatus": "proposed-human-review"})
                cursor.execute("""INSERT INTO future_battlefield_lifecycle_transition
                    (transition_id,run_id,revision,from_stage,to_stage,gate_json,gate_hash,approval_id,actor_id)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (transition_id, run_id, revision, current["stage"], target_stage, Json(normalized), gate_hash,
                     approval["approval_id"], identity["principal_id"]))
                cursor.execute("""INSERT INTO future_battlefield_lifecycle
                    (run_id,revision,stage,source_run_id,source_run_hash,state_json,state_hash,created_by)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (run_id, revision, target_stage, run_id, source_hash, Json(state), canonical_hash(state), identity["principal_id"]))
                if target_stage == "engineering-input":
                    for row in normalized["verificationMatrix"]:
                        row_hash = canonical_hash(row)
                        cursor.execute("""INSERT INTO future_battlefield_verification_requirement
                            (requirement_id,run_id,lifecycle_revision,dimension,metric,unit,operating_conditions,
                             input_basis,verification_method,test_procedure,acceptance_criteria,configuration_version,
                             result_evidence,review_status,row_json,row_hash,created_by)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (row["requirementId"], run_id, revision, row["dimension"], row["metric"], row["unit"],
                             Json(row["operatingConditions"]), Json(row["inputBasis"]), row["verificationMethod"],
                             row["testProcedure"], row["acceptanceCriteria"], row["configurationVersion"],
                             Json(row["resultEvidence"]), row["reviewStatus"], Json(row), row_hash, identity["principal_id"]))
                return self._get(cursor, run_id)

    def _get(self, cursor, run_id):
        cursor.execute("SELECT * FROM future_battlefield_lifecycle WHERE run_id=%s ORDER BY revision DESC", (run_id,))
        rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            raise LookupError("engineering lifecycle not found")
        for row in rows:
            if canonical_hash(row["state_json"]) != row["state_hash"]:
                raise LifecycleConflictError("lifecycle state hash mismatch")
        current = rows[0]
        cursor.execute("SELECT * FROM future_battlefield_lifecycle_transition WHERE run_id=%s ORDER BY revision", (run_id,))
        transitions = [dict(row) for row in cursor.fetchall()]
        for transition in transitions:
            if canonical_hash(transition["gate_json"]) != transition["gate_hash"]:
                raise LifecycleConflictError("lifecycle gate hash mismatch")
        cursor.execute("SELECT * FROM future_battlefield_verification_requirement WHERE run_id=%s ORDER BY lifecycle_revision,requirement_id", (run_id,))
        requirements = [dict(row) for row in cursor.fetchall()]
        return {"runId": run_id, "stage": current["stage"], "revision": current["revision"],
                "sourceRunId": current["source_run_id"], "sourceRunHash": current["source_run_hash"],
                "state": current["state_json"], "stateHash": current["state_hash"],
                "history": rows, "transitions": transitions, "verificationRequirements": requirements,
                "truthVerified": False, "engineeringCertification": False,
                "manufacturingStatus": "proposed-human-review"}


def create_engineering_lifecycle_router(connection_factory, *, auth=None):
    auth = auth or AdminAuth(connection_factory)
    service = EngineeringLifecycleService(connection_factory)
    router = APIRouter(prefix="/api/agent-admin/runs", tags=["engineering-lifecycle"])

    def access(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "cross-origin lifecycle operation rejected")
        bearer = request.headers.get("authorization", "")
        token = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, "authenticated local operator required")
        return actor, token

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except (LifecycleValidationError, LifecycleConflictError, ValueError, KeyError) as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception:
            raise HTTPException(409, "engineering lifecycle operation could not be applied") from None

    @router.post("/{run_id}/engineering-lifecycle", status_code=201)
    def create(run_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(service.create, run_id, actor, token)

    @router.get("/{run_id}/engineering-lifecycle")
    def describe(run_id: str, authenticated=Depends(access)):
        actor, token = authenticated
        with service.connection_factory() as con:
            with con.cursor() as cursor:
                service._authorize(cursor, actor, token)
                return invoke(service._get, cursor, run_id)

    @router.post("/{run_id}/engineering-lifecycle/advance", status_code=201)
    def advance(run_id: str, body: dict, authenticated=Depends(access)):
        actor, token = authenticated
        if not isinstance(body, dict) or set(body) != {"targetStage", "gate"} or not isinstance(body["gate"], dict):
            raise HTTPException(400, "targetStage and gate are required")
        return invoke(service.advance, run_id, actor, token, body["targetStage"], body["gate"])

    return router


__all__ = ["EngineeringLifecycleService", "create_engineering_lifecycle_router"]
