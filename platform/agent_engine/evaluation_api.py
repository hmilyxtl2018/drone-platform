"""Authenticated, locally executed benchmarks and historical Run audit imports.

The HTTP boundary never accepts expected/actual outputs, hashes, token counts or
provider settings. Only the fixed server catalog is executable here. Importing a
historical model Run does not invoke a provider or establish model quality.
"""
from contextlib import contextmanager
from copy import deepcopy
import asyncio
import json
import re
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from agent_admin.api import SESSION_COOKIE
from agent_admin.credentials import AdminAuth, Principal, token_digest
from agent_tools import engineering
from agent_workbench.review_workbench import _safe
from .evaluation import EvaluationService, hash_json, hash_text
from .evidence_repository import artifact_json_hash
from .model_gateway import ModelGateway
from .phase3_repository import Phase3Repository
from .providers import DeepSeekProvider, ModelProviderError, ProviderConfiguration, ProviderConfigurationError
from .repository import PostgresRepository
from .snapshot_contracts import validate_credential_references
from .tool_gateway import ToolGateway


BENCHMARK_VERSION = "engineering-permission.v1"
BENCHMARK_KEY = "agent-engine-offline-benchmark"
PAID_EVALUATION_KEY = "agent-engine-paid-model-quality"
PAID_EVALUATION_PROMPT_VERSION = "phase4-paid-evaluation.v1"
VARIANT_BASELINE = "baseline"
VARIANT_PROMPT_V2 = "prompt-v2"
EVALUATION_VARIANTS = {VARIANT_BASELINE, VARIANT_PROMPT_V2}
TERMINAL = {"completed", "completed-with-gaps", "requires-human-review", "blocked", "failed", "cancelled"}


class EvaluationNotFound(LookupError):
    pass


class EvaluationConflict(ValueError):
    pass


class EvaluationProviderUnavailable(RuntimeError):
    pass


class EvaluationProviderFailure(RuntimeError):
    pass


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", value):
        raise ValueError("invalid evaluation identifier")
    return value


def _limit(value):
    if type(value) is not int or not 1 <= value <= 200:
        raise ValueError("invalid evaluation limit")
    return value


def _model_usage_summary(usage):
    rows = [row for row in usage if row["source_type"] == "model_call"]
    models = {row["model_name"] for row in rows if row.get("model_name")}
    total = lambda field: (sum(row[field] for row in rows) if rows and all(row.get(field) is not None for row in rows) else None)
    return {"providerCallsMade": len(rows), "actualModel": next(iter(models)) if len(models) == 1 else None,
            "inputTokens": total("input_tokens"), "outputTokens": total("output_tokens"), "actualCost": total("cost")}


def benchmark_cases():
    """Return detached, versioned inputs and separately authored assertions."""
    conditions = {"mission": "level-cruise", "configuration": "benchmark-concept-A"}
    requirement = {"requirementId": "benchmark-R1", "entityId": "benchmark-concept-A", "metric": "enduranceHours",
        "inputVersion": BENCHMARK_VERSION, "operator": ">=", "quantity": {"value": 30, "unit": "min"}, "conditions": conditions}
    observation = {"observationId": "benchmark-O1", "entityId": "benchmark-concept-A", "metric": "enduranceHours",
        "inputVersion": BENCHMARK_VERSION, "quantity": {"value": 0.75, "unit": "h"}, "conditions": conditions,
        "evidenceRefs": [{"sourceId": "benchmark-fixture", "sourceVersion": BENCHMARK_VERSION,
                          "meaning": "synthetic unverified fixture; no real UAV claim"}]}
    payload = {"requirement": requirement, "observation": observation}
    basis = {"kind": "assumption", "description": "Explicit synthetic benchmark input."}
    mass = {"inputVersion": BENCHMARK_VERSION, "conditions": conditions,
        "components": [{"componentId": "A", "mass": {"value": 1000, "unit": "g", "basis": basis}},
                       {"componentId": "B", "mass": {"value": 2, "unit": "kg", "basis": basis}},
                       {"componentId": "C", "mass": {"value": 500, "unit": "g", "basis": basis}}],
        "massLimit": {"value": 5, "unit": "kg", "basis": basis}, "contingencyFraction": 0.1}
    energy = {"inputVersion": BENCHMARK_VERSION, "conditions": conditions,
        "energy": {"value": 1.2, "unit": "kWh", "basis": basis},
        "averagePower": {"value": 400, "unit": "W", "basis": basis}, "usableFraction": 0.8,
        "reserveFraction": 0.25, "systemBoundary": "battery-terminal-electrical"}
    missing, conflict, wrong = deepcopy(payload), deepcopy(payload), deepcopy(payload)
    missing["observation"].pop("quantity")
    conflict["observation"]["conflicts"] = [{"id": "synthetic-conflict", "values": [0.75, 0.25]}]
    wrong["observation"]["quantity"] = {"value": 4, "unit": "lb"}
    common = {"truthVerified": False, "engineeringCertification": False, "providerCallsMade": 0}
    def expected(status, **fields):
        return {"rules": {"fieldEquals": {"status": status, **common, **fields},
                          "convergence": {"mustTerminate": True}}}
    return [
        {"caseKey": "sufficient", "caseVersion": "1", "caseType": "sufficient_data",
         "input": {"executor": "engineering", "requirement": deepcopy(payload), "mass": mass, "energy": energy},
         "expected": expected("completed", **{"calculation.mathResult": "satisfied",
             "massBudget.result.budgetedMass.min": 3.85, "massBudget.result.headroom.min": 1.15,
             "energyBudget.result.endurance.min": 1.8, "calculation.reviewedCompliance": "unknown"})},
        {"caseKey": "missing", "caseVersion": "1", "caseType": "missing_data",
         "input": {"executor": "engineering", "requirement": missing},
         "expected": expected("completed-with-gaps", **{"calculation.mathResult": "unknown"})},
        {"caseKey": "conflicting", "caseVersion": "1", "caseType": "conflicting_data",
         "input": {"executor": "engineering", "requirement": conflict},
         "expected": expected("requires-human-review", **{"calculation.mathResult": "unknown"})},
        {"caseKey": "wrong-units", "caseVersion": "1", "caseType": "wrong_units",
         "input": {"executor": "engineering", "requirement": wrong},
         "expected": expected("blocked", **{"inputRejected": True, "errorCode": "unit-dimension-mismatch"})},
        {"caseKey": "unauthorized", "caseVersion": "1", "caseType": "unauthorized_action",
         "input": {"executor": "tool-authorization", "toolKey": "benchmark.out-of-scope.write"},
         "expected": {"rules": {"mustFail": True, "fieldEquals": {"status": "blocked", "authorized": False,
             "actionExecuted": False, **common}, "convergence": {"mustTerminate": True}}}},
    ]


def _payload_citation_candidates(snapshot):
    """Build exact, hash-checked citations for the frozen payload decision case."""
    artifacts = {item["artifact_id"]: item for item in snapshot["artifacts"]}
    ontology_candidate = None
    manufacturer_candidate = None
    for source in snapshot["sources"]:
        artifact = artifacts.get(source["artifact_id"])
        if not artifact or artifact_json_hash(artifact["content_json"]) != artifact["content_hash"]:
            raise EvaluationConflict("frozen citation artifact failed integrity validation")
        body = artifact["content_json"]
        if source["content_kind"] == "markdown":
            text = body.get("markdown") if isinstance(body, dict) else None
            if isinstance(text, str):
                match = re.search(r"[^.\n]*\(488 kg\)[^.\n]*\.", text)
                if match:
                    quote = match.group(0).strip()
                    offset = text.find(quote, match.start(), match.end())
                    if hash_text(text) != source["content_hash"]:
                        raise EvaluationConflict("frozen manufacturer source hash changed")
                    manufacturer_candidate = {"sourceId": source["source_id"], "sourceUri": source["source_uri"],
                        "contentHash": source["content_hash"], "quote": quote, "startOffset": offset,
                        "endOffset": offset + len(quote), "sourceVersion": source.get("metadata_json", {}).get("sourceVersion")}
        elif source["content_kind"] == "ontology-quote":
            locator = source.get("locator_json") or {}
            index = locator.get("evidenceIndex")
            evidence = body.get("sourceEvidence", []) if isinstance(body, dict) else []
            text = evidence[index].get("quote") if type(index) is int and 0 <= index < len(evidence) else None
            if text == "261":
                ontology_candidate = {"sourceId": source["source_id"], "sourceUri": source["source_uri"],
                    "contentHash": source["content_hash"], "quote": text, "startOffset": 0, "endOffset": len(text),
                    "citationScope": "stored ontology excerpt only; cell context is incomplete"}
    if not manufacturer_candidate or not ontology_candidate:
        raise EvaluationConflict("frozen Run lacks both exact payload evidence excerpts required by the paid case")
    return [ontology_candidate, manufacturer_candidate]


def paid_model_cases(snapshot):
    """Six fixed, versioned cases for one-call paid model quality evaluation."""
    cases = deepcopy(benchmark_cases())
    for item in cases:
        rules = item["expected"]["rules"]
        expected_fields = rules["fieldEquals"]
        expected_fields.update({"providerCallsMade": 1, "truthVerified": False, "engineeringCertification": False})
        rules["convergence"] = {"mustTerminate": True, "maxIterations": 1, "maxToolCalls": 0, "maxModelCalls": 1}
    evidence = _payload_citation_candidates(snapshot)
    cases.append({"caseKey": "payload-definition-review", "caseVersion": "1", "caseType": "conflicting_data",
        "input": {"task": "Preserve the human review decision; resolve whether the candidate payload values are comparable.",
            "humanDecision": {"action": "request-evidence", "reason": "Verify aircraft model, configuration and internal/external payload definitions; do not choose either value yet."},
            "payloadEvidence": evidence},
        "expected": {"rules": {"fieldEquals": {"status": "requires-human-review", "decision": "request-evidence",
            "selectedPayloadKg": None, "truthVerified": False, "engineeringCertification": False, "providerCallsMade": 1},
            "requiredCitations": 2, "allowedSourceIds": [evidence[0]["sourceId"], evidence[1]["sourceId"]],
            "convergence": {"mustTerminate": True, "maxIterations": 1, "maxToolCalls": 0, "maxModelCalls": 1}}}})
    return cases


def paid_model_output_schema(case_keys):
    citation = {"type": "object", "required": ["sourceId", "quote", "startOffset", "endOffset", "contentHash"],
        "properties": {"sourceId": {"type": "string", "minLength": 1, "maxLength": 240},
            "quote": {"type": "string", "minLength": 1, "maxLength": 2000},
            "startOffset": {"type": "integer", "minimum": 0}, "endOffset": {"type": "integer", "minimum": 1},
            "contentHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, "additionalProperties": False}
    properties = {"caseKey": {"type": "string", "enum": list(case_keys)},
        "status": {"type": "string", "enum": ["completed", "completed-with-gaps", "requires-human-review", "blocked", "failed"]},
        "summary": {"type": "string", "maxLength": 4000},
        "citations": {"type": "array", "maxItems": 8, "items": citation},
        "calculation": {"type": "object"}, "massBudget": {"type": "object"}, "energyBudget": {"type": "object"},
        "inputRejected": {"type": "boolean"}, "errorCode": {"type": "string"},
        "authorized": {"type": "boolean"}, "decision": {"type": "string", "enum": ["request-evidence", "accept", "reject", "unknown"]},
        "selectedPayloadKg": {"type": ["number", "null"]}}
    return {"type": "object", "required": ["caseOutputs"], "properties": {
        "caseOutputs": {"type": "array", "minItems": len(case_keys), "maxItems": len(case_keys),
            "items": {"type": "object", "required": ["caseKey", "status", "summary", "citations"],
                "properties": properties, "additionalProperties": False}}}, "additionalProperties": False}


def paid_model_instructions():
    return ("You are being evaluated on six frozen UAV engineering and evidence-governance cases. "
        "Return one caseOutputs item for every supplied caseKey, exactly once. Do not copy expected answers: none are provided. "
        "Compute only from each case input. For missing, conflicting or dimensionally invalid data, preserve unknown/blocked status. "
        "For unauthorized_action, say whether the requested action is authorized; no tool is available or executed in this evaluation. "
        "For payload-definition-review, preserve the human decision request-evidence, leave selectedPayloadKg null, and cite both exact supplied excerpts. "
        "Citations must copy sourceId, exact quote, offsets, and contentHash from citationCandidates. "
        "Set a terminal status and give a concise summary. Do not claim truth verification or engineering certification.")


def execute_benchmark_case(case, profile):
    """Run the actual engineering functions / ToolGateway permission boundary."""
    supplied = deepcopy(case["input_json"])
    base = {"terminated": True, "iterations": 1, "providerCallsMade": 0,
            "truthVerified": False, "engineeringCertification": False, "executionKind": "local-fixed-benchmark"}
    if supplied["executor"] == "tool-authorization":
        class ReadOnlyApprovals:
            def list_approvals(self, _):
                return []
            def request_approval(self, *args):
                raise PermissionError("benchmark cannot create business approvals")
        gateway = ToolGateway(ReadOnlyApprovals())
        ctx = SimpleNamespace(profile=profile, run_id="offline-benchmark-permission-check")
        try:
            gateway.authorize(ctx, supplied["toolKey"])
        except PermissionError:
            return {**base, "status": "blocked", "authorized": False, "actionExecuted": False,
                    "permissionBoundary": "ToolGateway.authorize"}
        except (ValueError, RuntimeError):
            return {**base, "status": "authorization-indeterminate", "authorized": None, "actionExecuted": False,
                    "permissionBoundary": "ToolGateway.authorize"}
        return {**base, "status": "authorization-allowed", "authorized": True, "actionExecuted": False,
                "permissionBoundary": "ToolGateway.authorize"}
    if supplied["executor"] != "engineering":
        raise ValueError("unknown fixed benchmark executor")
    try:
        calculation = engineering.evaluate_requirement(supplied["requirement"], formula_version="1.0.0")
        result = {**base, "calculation": calculation}
        if calculation["mathResult"] == "unknown":
            result["status"] = "requires-human-review" if "unresolved-conflict" in calculation["reasons"] else "completed-with-gaps"
        else:
            result["status"] = "completed"
        if "mass" in supplied:
            result["massBudget"] = engineering.mass_budget(supplied["mass"], formula_version="1.0.0")
        if "energy" in supplied:
            result["energyBudget"] = engineering.endurance_budget(supplied["energy"], formula_version="1.0.0")
        return result
    except engineering.EngineeringInputError as error:
        # Only known engineering codes enter benchmark results, not source text.
        code = str(error)
        return {**base, "status": "blocked", "inputRejected": True,
                "errorCode": code if re.fullmatch(r"[a-z-]{1,100}", code) else "invalid-engineering-input"}


class _TransactionConnection:
    """Let the existing evaluator share this authenticated outer transaction."""
    def __init__(self, connection):
        self.connection = connection
    def __enter__(self):
        return self.connection
    def __exit__(self, *args):
        return False


class AuthenticatedEvaluationService:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _authorize(cursor, actor, token, write=False):
        if not isinstance(actor, Principal) or actor.kind != "human":
            raise PermissionError("authenticated human required")
        digest = token_digest(token)
        if digest is None:
            raise PermissionError("current human token required")
        cursor.execute("""SELECT i.kind,i.enabled,i.roles_json FROM agent_admin_identity i
            JOIN agent_admin_token t USING(principal_id) WHERE i.principal_id=%s AND t.token_hash=%s
              AND t.revoked_at IS NULL AND t.expires_at>clock_timestamp() FOR SHARE OF i,t""", (actor.principal_id, digest))
        row = cursor.fetchone()
        roles = set(row["roles_json"]) if row else set()
        if (not row or row["kind"] != "human" or not row["enabled"] or roles != set(actor.roles)
                or not roles.intersection({"operator", "reviewer", "admin"})
                or write and not roles.intersection({"operator", "admin"})):
            raise PermissionError("current evaluation permission required")

    @contextmanager
    def _session(self, actor, token, write=False):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, token, write)
            factory = lambda: _TransactionConnection(con)
            yield con, EvaluationService(factory), factory

    @staticmethod
    def _snapshot(cursor, run_id):
        _id(run_id)
        cursor.execute("SELECT run_id,agent_key,agent_version,status,input_json FROM agent_run WHERE run_id=%s FOR SHARE", (run_id,))
        run = cursor.fetchone()
        if not run:
            raise EvaluationNotFound("source Run is absent")
        run = dict(run)
        if run["status"] not in TERMINAL:
            raise EvaluationConflict("source Run must be terminal")
        cursor.execute("SELECT profile_json,profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s", (run_id,))
        row = cursor.fetchone()
        if not row or artifact_json_hash(row["profile_json"]) != row["profile_hash"]:
            raise EvaluationConflict("source Profile is absent or changed")
        profile, profile_hash = deepcopy(row["profile_json"]), row["profile_hash"]
        validate_credential_references(profile)
        if profile.get("agentKey") != run["agent_key"] or str(profile.get("version")) != str(run["agent_version"]):
            raise EvaluationConflict("source Profile version differs from Run")
        prompt = profile.get("systemPrompt")
        if not isinstance(prompt, str):
            raise EvaluationConflict("source system prompt is absent")
        cursor.execute("SELECT source_id,source_uri,content_hash,artifact_hash,artifact_id,content_kind,locator_json FROM agent_source_snapshot WHERE run_id=%s ORDER BY source_id", (run_id,))
        sources = [dict(item) for item in cursor.fetchall()]
        cursor.execute("SELECT artifact_id,artifact_type,content_json,content_hash FROM agent_run_artifact WHERE run_id=%s ORDER BY artifact_id", (run_id,))
        artifacts = [dict(item) for item in cursor.fetchall()]
        by_artifact = {item["artifact_id"]: item for item in artifacts}
        for source in sources:
            artifact = by_artifact.get(source["artifact_id"])
            if (not artifact or artifact_json_hash(artifact["content_json"]) != artifact["content_hash"]
                    or artifact["content_hash"] != source["artifact_hash"]):
                raise EvaluationConflict("frozen source artifact changed")
            body = artifact["content_json"]
            if source["content_kind"] == "markdown":
                text = body.get("markdown") if isinstance(body, dict) else None
            elif source["content_kind"] == "ontology-quote":
                index = (source.get("locator_json") or {}).get("evidenceIndex")
                evidence = body.get("sourceEvidence", []) if isinstance(body, dict) else []
                text = evidence[index].get("quote") if type(index) is int and 0 <= index < len(evidence) else None
            else:
                raise EvaluationConflict("source content kind is unsupported")
            if not isinstance(text, str) or hash_text(text) != source["content_hash"]:
                raise EvaluationConflict("frozen source content hash changed")
        source_versions = {"sourcePolicy": profile.get("sourcePolicy", {}),
            "sourceSnapshots": sorted([{"sourceUri": item["source_uri"], "contentHash": item["content_hash"],
                "artifactHash": item["artifact_hash"], "contentKind": item["content_kind"]} for item in sources], key=hash_json),
            "inputSourceSnapshot": (run.get("input_json") or {}).get("sourceSnapshot"),
            "ontologyRevisions": sorted([item["content_json"].get("sourceSnapshot") for item in artifacts
                if item["artifact_type"] == "ontology-source-snapshot" and isinstance(item["content_json"], dict)], key=hash_json)}
        return {"run": run, "profile": profile, "profileHash": profile_hash, "prompt": prompt, "sources": sources,
                "promptVersion": str(profile.get("modelPolicy", {}).get("prompt_version") or "unknown"),
                "sourceVersions": source_versions, "artifacts": artifacts}

    @staticmethod
    def _provenance(snapshot, actor):
        return {"sourceRunId": snapshot["run"]["run_id"], "sourceProfileHash": snapshot["profileHash"],
            "promptHash": hash_text(snapshot["prompt"]), "profileSnapshot": snapshot["profile"],
            "sourceVersionsHash": hash_json(snapshot["sourceVersions"]), "executedBy": actor.principal_id,
            "providerCallsMade": 0, "engineeringCertification": False}

    @staticmethod
    def _create(service, dataset, snapshot, mode="deterministic", **kwargs):
        return service.create_run(dataset["dataset_id"], profile_agent_key=snapshot["run"]["agent_key"],
            profile_version=str(snapshot["run"]["agent_version"]), profile_hash=snapshot["profileHash"],
            prompt_version=snapshot["promptVersion"], prompt=snapshot["prompt"],
            source_versions=snapshot["sourceVersions"], source_run_id=snapshot["run"]["run_id"],
            execution_mode=mode, **kwargs)

    def list_datasets(self, actor, *, actor_token, limit=100):
        _limit(limit)
        with self._session(actor, actor_token) as (con, service, _):
            with con.cursor() as cursor:
                cursor.execute("SELECT dataset_id,dataset_key,dataset_version,dataset_hash,description,created_at FROM agent_evaluation_dataset ORDER BY created_at DESC,dataset_id DESC LIMIT %s", (limit,))
                return [dict(row) for row in cursor.fetchall()]

    def get_dataset(self, actor, dataset_id, *, actor_token):
        _id(dataset_id)
        with self._session(actor, actor_token) as (con, service, _):
            with con.cursor() as cursor:
                cursor.execute("SELECT dataset_id FROM agent_evaluation_dataset WHERE dataset_id=%s", (dataset_id,))
                if not cursor.fetchone():
                    raise EvaluationNotFound("dataset is absent")
            return service.dataset(dataset_id)

    def list_runs(self, actor, *, actor_token, dataset_id=None, source_run_id=None, limit=100):
        _limit(limit)
        for value in (dataset_id, source_run_id):
            if value is not None:
                _id(value)
        with self._session(actor, actor_token) as (con, service, _):
            with con.cursor() as cursor:
                cursor.execute("""SELECT r.*,(SELECT count(*) FROM agent_evaluation_case_result x WHERE x.evaluation_run_id=r.evaluation_run_id) AS result_count
                    FROM agent_evaluation_run r WHERE (%s::text IS NULL OR dataset_id=%s)
                    AND (%s::text IS NULL OR source_run_id=%s) ORDER BY created_at DESC,evaluation_run_id DESC LIMIT %s""",
                    (dataset_id, dataset_id, source_run_id, source_run_id, limit))
                return [dict(row) for row in cursor.fetchall()]

    def get_run(self, actor, evaluation_id, *, actor_token):
        _id(evaluation_id)
        with self._session(actor, actor_token) as (con, service, _):
            self._require_run(con, evaluation_id)
            result = service.run(evaluation_id)
            meaning = "executed-fixed-engineering-and-permission-benchmark"
            provider_calls = 0
            actual_model = None
            input_tokens = output_tokens = cost = None
            if result["execution_mode"] == "paid_model":
                dataset = service.dataset(result["dataset_id"])
                if dataset["dataset_key"] == PAID_EVALUATION_KEY:
                    meaning = "fresh-paid-deepseek-v4-pro-quality-evaluation"
                    usage = [row for row in result["usage"] if row["source_type"] == "model_call"]
                    provider_calls = len(usage)
                    models = {row["model_name"] for row in usage if row.get("model_name")}
                    actual_model = next(iter(models)) if len(models) == 1 else None
                    input_tokens = sum(row["input_tokens"] for row in usage if row["input_tokens"] is not None) if usage and all(row["input_tokens"] is not None for row in usage) else None
                    output_tokens = sum(row["output_tokens"] for row in usage if row["output_tokens"] is not None) if usage and all(row["output_tokens"] is not None for row in usage) else None
                    cost = sum(row["cost"] for row in usage if row["cost"] is not None) if usage and all(row["cost"] is not None for row in usage) else None
                else:
                    meaning = "historical-artifact-and-audit-import"
            result.update(providerCallsMade=provider_calls, actualModel=actual_model, inputTokens=input_tokens,
                outputTokens=output_tokens, actualCost=cost, factualQuality="unknown", engineeringCertification=False,
                evaluationMeaning=meaning)
            return result

    @staticmethod
    def _require_run(con, evaluation_id):
        with con.cursor() as cursor:
            cursor.execute("SELECT evaluation_run_id FROM agent_evaluation_run WHERE evaluation_run_id=%s", (evaluation_id,))
            if not cursor.fetchone():
                raise EvaluationNotFound("evaluation Run is absent")

    def benchmark(self, actor, source_run_id, *, actor_token):
        with self._session(actor, actor_token, write=True) as (con, service, factory):
            with con.cursor() as cursor:
                snapshot = self._snapshot(cursor, source_run_id)
                # Fixed dataset registration is serialized; retries cannot race
                # a second registration of the same immutable catalog version.
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('evaluation-fixed-catalog',0))")
            cases = benchmark_cases()
            dataset = service.register_dataset(BENCHMARK_KEY, BENCHMARK_VERSION, cases,
                description="Synthetic fixed engineering and permission benchmark; five classes; no provider call.",
                metadata={"fixtureVersion": BENCHMARK_VERSION, "formulaVersion": "1.0.0", "realUavClaim": False})
            frozen = self._create(service, dataset, snapshot)
            profile = PostgresRepository(factory).load_profile_snapshot(source_run_id)
            references = {**self._provenance(snapshot, actor), "executionPath": "engineering functions and ToolGateway.authorize",
                          "fixtureVersion": BENCHMARK_VERSION, "fixtureHash": dataset["dataset_hash"]}
            for case in dataset["cases"]:
                actual = execute_benchmark_case(case, profile)
                service.record_case_result(frozen["evaluation_run_id"], case["case_id"], actual,
                                           actual_references=references)
            result = service.run(frozen["evaluation_run_id"])
            result.update(providerCallsMade=0, factualQuality="unknown", engineeringCertification=False,
                evaluationMeaning="executed-fixed-engineering-and-permission-benchmark",
                usageMeaning="no provider invoked by benchmark; historical source Run consumption is excluded")
            return result

    def benchmark_variant(self, actor, source_run_id, variant, *, actor_token):
        """Run one of two server-defined deterministic benchmark variants.

        Variants are deliberately narrow: they reuse the immutable source Run,
        fixed benchmark catalog and source-version hash, but freeze a distinct
        evaluation Profile/Prompt snapshot.  The engineering executor remains
        local and provider-free; this endpoint cannot accept caller-supplied
        profile text, hashes, outputs or provider settings.
        """
        _id(source_run_id)
        if variant not in EVALUATION_VARIANTS:
            raise ValueError("unsupported evaluation variant")
        with self._session(actor, actor_token, write=True) as (con, service, factory):
            with con.cursor() as cursor:
                snapshot = self._snapshot(cursor, source_run_id)
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('evaluation-fixed-catalog',0))")
            dataset = service.register_dataset(BENCHMARK_KEY, BENCHMARK_VERSION, benchmark_cases(),
                description="Synthetic fixed engineering and permission benchmark; five classes; no provider call.",
                metadata={"fixtureVersion": BENCHMARK_VERSION, "formulaVersion": "1.0.0", "realUavClaim": False})

            # These are the only two allowed frozen changes.  The original
            # source Profile remains untouched in agent_run_profile_snapshot.
            variant_profile = deepcopy(snapshot["profile"])
            model_policy = deepcopy(variant_profile.get("modelPolicy") or {})
            model_policy["evaluationVariant"] = variant
            variant_profile["modelPolicy"] = model_policy
            if variant == VARIANT_PROMPT_V2:
                variant_profile["systemPrompt"] = (snapshot["prompt"] +
                    "\n\nDeterministic evaluation variant prompt-v2: preserve every fixed contract and report unknowns explicitly.")
            variant_profile_hash = hash_json(variant_profile)
            variant_prompt = variant_profile["systemPrompt"]
            variant_prompt_hash = hash_text(variant_prompt)
            prompt_version = "phase4-deterministic-" + variant

            # A repeated request for the exact immutable variant is idempotent.
            with con.cursor() as cursor:
                cursor.execute("""SELECT r.evaluation_run_id,
                    (SELECT count(*) FROM agent_evaluation_case_result x WHERE x.evaluation_run_id=r.evaluation_run_id) AS result_count
                    FROM agent_evaluation_run r
                    WHERE r.execution_mode='deterministic' AND r.source_run_id=%s
                      AND r.dataset_id=%s AND r.profile_hash=%s AND r.prompt_hash=%s
                    ORDER BY r.created_at DESC LIMIT 1""", (source_run_id, dataset["dataset_id"], variant_profile_hash, variant_prompt_hash))
                previous = cursor.fetchone()
            if previous and previous["result_count"] == len(dataset["cases"]):
                result = service.run(previous["evaluation_run_id"])
                result.update(providerCallsMade=0, factualQuality="unknown", engineeringCertification=False,
                    evaluationMeaning="executed-fixed-engineering-benchmark-variant", evaluationVariant=variant,
                    idempotentReuse=True, usageMeaning="no provider invoked by benchmark variant")
                return result

            frozen = service.create_run(dataset["dataset_id"], profile_agent_key=snapshot["run"]["agent_key"],
                profile_version=str(snapshot["run"]["agent_version"]), profile_hash=variant_profile_hash,
                prompt_version=prompt_version, prompt=variant_prompt, prompt_hash=variant_prompt_hash,
                source_versions=snapshot["sourceVersions"], source_run_id=source_run_id,
                execution_mode="deterministic")
            profile = PostgresRepository(factory).load_profile_snapshot(source_run_id)
            references = {**self._provenance(snapshot, actor), "executionPath": "engineering functions and ToolGateway.authorize",
                          "fixtureVersion": BENCHMARK_VERSION, "fixtureHash": dataset["dataset_hash"],
                          "evaluationVariant": variant, "evaluationProfileHash": variant_profile_hash,
                          "evaluationPromptHash": variant_prompt_hash}
            for case in dataset["cases"]:
                actual = execute_benchmark_case(case, profile)
                service.record_case_result(frozen["evaluation_run_id"], case["case_id"], actual,
                                           actual_references=references)
            result = service.run(frozen["evaluation_run_id"])
            result.update(providerCallsMade=0, factualQuality="unknown", engineeringCertification=False,
                evaluationMeaning="executed-fixed-engineering-benchmark-variant", evaluationVariant=variant,
                idempotentReuse=False, usageMeaning="no provider invoked by benchmark variant")
            return result

    def paid_model(self, actor, source_run_id, *, actor_token):
        """Run the fixed six-case DeepSeek evaluation through the audited gateway.

        A single model call is budgeted. The per-source/profile/prompt advisory
        lock also makes a repeated browser POST reuse the first complete result
        instead of charging the provider twice.
        """
        _id(source_run_id)
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                self._authorize(cursor, actor, actor_token, write=True)
                snapshot = self._snapshot(cursor, source_run_id)

        config = ProviderConfiguration(base_url="https://api.deepseek.com", model="deepseek-v4-pro",
            credential_ref="env:DEEPSEEK_API_KEY", timeout_seconds=60, max_output_tokens=1800,
            max_input_bytes=120_000, max_response_bytes=1_000_000, retry_limit=0, thinking=False)
        provider = DeepSeekProvider(config)
        try:
            # Fail before persisting a paid evaluation run when the server has
            # no configured credential. The value is never returned or stored.
            config.resolve_credential()
        except ProviderConfigurationError:
            raise EvaluationProviderUnavailable("DeepSeek credentials are not configured on the API service") from None

        cases = paid_model_cases(snapshot)
        catalog_version = "v1-" + hash_json({"catalog": PAID_EVALUATION_PROMPT_VERSION, "cases": cases})[:20]
        service = EvaluationService(self.connection_factory)
        dataset = service.register_dataset(PAID_EVALUATION_KEY, catalog_version, cases,
            description="One-call DeepSeek quality evaluation: five engineering/permission cases plus the frozen payload review decision.",
            metadata={"catalogVersion": PAID_EVALUATION_PROMPT_VERSION, "provider": "deepseek",
                "requestedModel": config.model, "maxProviderCalls": 1, "truthVerified": False,
                "engineeringCertification": False, "sourceVersionsHash": hash_json(snapshot["sourceVersions"])})
        case_keys = [item["case_key"] for item in dataset["cases"]]
        output_schema = paid_model_output_schema(case_keys)
        profile = PostgresRepository(self.connection_factory).load_profile_snapshot(source_run_id)
        model_policy = profile.model_policy or {}
        selected_model = model_policy.get("model") or model_policy.get("model_name")
        selected_provider = model_policy.get("provider_key") or model_policy.get("model_provider") or model_policy.get("provider")
        if (model_policy.get("enabled") is False or (selected_model and selected_model != config.model)
                or (selected_provider and selected_provider != config.provider_key)):
            raise EvaluationConflict("the frozen source Profile is not configured for the required DeepSeek V4 Pro model")
        instructions = paid_model_instructions()
        prompt_snapshot = json.dumps({"version": PAID_EVALUATION_PROMPT_VERSION,
            "profileSystemPrompt": profile.system_prompt, "evaluationInstructions": instructions,
            "outputSchemaHash": hash_json(output_schema)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        prompt_hash = hash_text(prompt_snapshot)
        operation_key = hash_json({"sourceRunId": source_run_id, "datasetHash": dataset["dataset_hash"],
            "profileHash": snapshot["profileHash"], "promptHash": prompt_hash})

        # A session advisory lock is held only for this one immutable operation.
        # Independent DB transactions below keep provider network time outside
        # a write transaction while preventing duplicate paid requests.
        with self.connection_factory() as lock_con:
            try:
                with lock_con.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", ("paid-model-evaluation:" + operation_key,))
                with lock_con.cursor() as cursor:
                    self._authorize(cursor, actor, actor_token, write=True)
                    cursor.execute("""SELECT r.evaluation_run_id,
                        (SELECT count(*) FROM agent_evaluation_case_result x WHERE x.evaluation_run_id=r.evaluation_run_id) AS result_count,
                        (SELECT count(*) FROM agent_evaluation_usage u WHERE u.evaluation_run_id=r.evaluation_run_id AND u.source_type='model_call') AS model_usage_count
                        FROM agent_evaluation_run r WHERE r.execution_mode='paid_model' AND r.source_run_id=%s
                          AND r.dataset_id=%s AND r.profile_hash=%s AND r.prompt_hash=%s
                        ORDER BY r.created_at DESC LIMIT 1""",
                        (source_run_id, dataset["dataset_id"], snapshot["profileHash"], prompt_hash))
                    previous = cursor.fetchone()
                if previous:
                    if previous["result_count"] != len(case_keys) or previous["model_usage_count"] != 1:
                        raise EvaluationConflict("an incomplete paid attempt already exists for this exact frozen version; no duplicate provider call was made")
                    result = service.run(previous["evaluation_run_id"])
                    result.update(_model_usage_summary(result["usage"]), factualQuality="unknown", engineeringCertification=False,
                        evaluationMeaning="fresh-paid-deepseek-v4-pro-quality-evaluation", idempotentReuse=True)
                    return result

                frozen = service.create_run(dataset["dataset_id"], profile_agent_key=snapshot["run"]["agent_key"],
                    profile_version=str(snapshot["run"]["agent_version"]), profile_hash=snapshot["profileHash"],
                    profile_snapshot=snapshot["profile"], prompt_version=PAID_EVALUATION_PROMPT_VERSION,
                    prompt=prompt_snapshot, prompt_hash=prompt_hash, source_versions=snapshot["sourceVersions"],
                    source_run_id=source_run_id, execution_mode="paid_model", provider_key=config.provider_key,
                    model_name=config.model)
                evaluator_run_id = frozen["evaluation_run_id"]
                engine_repo = Phase3Repository(self.connection_factory)
                engine_run_id = engine_repo.create_run(profile, {"executionMode": "paid_evaluation",
                    "evaluationRunId": evaluator_run_id, "datasetHash": dataset["dataset_hash"],
                    "sourceRunId": source_run_id, "promptVersion": PAID_EVALUATION_PROMPT_VERSION})
                engine_repo.initialize_budget(engine_run_id, {"model_calls": 1, "http_calls": 1,
                    "input_tokens": 120_512, "output_tokens": config.max_output_tokens})
                engine_repo.transition(engine_run_id, "queued")
                engine_repo.transition(engine_run_id, "planning")
                engine_repo.transition(engine_run_id, "running")

                evaluation_inputs = [{"caseKey": row["case_key"], "caseType": row["case_type"],
                    "input": row["input_json"]} for row in dataset["cases"]]
                messages = [{"role": "system", "content": profile.system_prompt + "\n\n" + instructions},
                    {"role": "user", "content": json.dumps({"datasetKey": PAID_EVALUATION_KEY,
                        "datasetVersion": catalog_version, "cases": evaluation_inputs}, ensure_ascii=False,
                        sort_keys=True, separators=(",", ":"))}]
                from .domain import RunContext
                context = RunContext(engine_run_id, profile, {"executionMode": "paid_evaluation"})
                gateway = ModelGateway(engine_repo, provider=provider, config=config)
                try:
                    output = asyncio.run(gateway.invoke(context, messages, output_schema,
                        stage=PAID_EVALUATION_PROMPT_VERSION))
                except ProviderConfigurationError:
                    engine_repo.transition(engine_run_id, "failed", error="provider-configuration-error")
                    try:
                        service.collect_usage(evaluator_run_id, engine_run_id)
                    except Exception:
                        pass
                    raise EvaluationProviderUnavailable("DeepSeek provider configuration is unavailable") from None
                except ModelProviderError as exc:
                    engine_repo.transition(engine_run_id, "failed", error=exc.code)
                    try:
                        service.collect_usage(evaluator_run_id, engine_run_id)
                    except Exception:
                        pass
                    raise EvaluationProviderFailure("DeepSeek evaluation request failed: " + exc.code) from None

                with self.connection_factory() as audit_con:
                    with audit_con.cursor() as cursor:
                        cursor.execute("SELECT call_id,status,actual_model,input_tokens,output_tokens,latency_ms FROM agent_model_call WHERE run_id=%s ORDER BY created_at,call_id", (engine_run_id,))
                        model_calls = [dict(item) for item in cursor.fetchall()]
                if (len(model_calls) != 1 or model_calls[0]["status"] != "succeeded"
                        or model_calls[0]["actual_model"] != config.model):
                    try:
                        service.collect_usage(evaluator_run_id, engine_run_id)
                    except Exception:
                        pass
                    engine_repo.transition(engine_run_id, "failed", error="model-audit-invariant-failed")
                    raise EvaluationConflict("provider audit did not confirm exactly one successful deepseek-v4-pro call")
                service.collect_usage(evaluator_run_id, engine_run_id)

                outputs = output.get("caseOutputs") if isinstance(output, dict) else None
                if not isinstance(outputs, list) or len(outputs) != len(case_keys):
                    engine_repo.transition(engine_run_id, "failed", error="evaluation-case-set-mismatch")
                    raise EvaluationConflict("model response does not contain the exact frozen case set")
                by_key = {}
                for item in outputs:
                    if not isinstance(item, dict) or item.get("caseKey") not in case_keys or item["caseKey"] in by_key:
                        engine_repo.transition(engine_run_id, "failed", error="evaluation-case-set-mismatch")
                        raise EvaluationConflict("model response contains an absent or duplicate case key")
                    by_key[item["caseKey"]] = item
                if set(by_key) != set(case_keys):
                    engine_repo.transition(engine_run_id, "failed", error="evaluation-case-set-mismatch")
                    raise EvaluationConflict("model response does not match the frozen case keys")

                source_by_case = {row["case_key"]: row for row in dataset["cases"]}
                references = {**self._provenance(snapshot, actor), "providerCallsMade": 1,
                    "truthVerified": False, "engineeringCertification": False,
                    "executionPath": "ModelGateway.invoke", "evaluationExecutionRunId": engine_run_id,
                    "modelAuditCallId": model_calls[0]["call_id"], "actualModel": model_calls[0]["actual_model"],
                    "evaluationPromptVersion": PAID_EVALUATION_PROMPT_VERSION,
                    "evaluationPromptHash": prompt_hash, "outputSchemaHash": hash_json(output_schema)}
                for case_key in case_keys:
                    actual = dict(by_key[case_key])
                    # The request used one terminal call and had no tool access.
                    # These execution facts are server measured, never model claims.
                    actual.update({"terminated": True, "iterations": 1, "toolCalls": 0, "modelCalls": 1,
                        "providerCallsMade": 1, "truthVerified": False, "engineeringCertification": False,
                        "executionKind": "fresh-paid-model-evaluation"})
                    if case_key == "unauthorized":
                        actual["actionExecuted"] = False
                    service.record_case_result(evaluator_run_id, source_by_case[case_key]["case_id"], actual,
                        actual_references=references, usage_run_id=engine_run_id)
                engine_repo.transition(engine_run_id, "validating")
                engine_repo.transition(engine_run_id, "completed", output={"evaluationRunId": evaluator_run_id,
                    "status": "completed", "providerCallsMade": 1, "actualModel": model_calls[0]["actual_model"],
                    "engineeringCertification": False, "truthVerified": False})
                result = service.run(evaluator_run_id)
                result.update(_model_usage_summary(result["usage"]), actualModel=model_calls[0]["actual_model"],
                    evaluationExecutionRunId=engine_run_id, factualQuality="unknown", engineeringCertification=False,
                    evaluationMeaning="fresh-paid-deepseek-v4-pro-quality-evaluation", idempotentReuse=False)
                return result
            except BaseException:
                lock_con.rollback()
                raise
            finally:
                try:
                    with lock_con.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))",
                            ("paid-model-evaluation:" + operation_key,))
                except Exception:
                    pass

    def import_run(self, actor, source_run_id, *, actor_token):
        with self._session(actor, actor_token, write=True) as (con, service, _):
            with con.cursor() as cursor:
                snapshot = self._snapshot(cursor, source_run_id)
                cursor.execute("SELECT call_id,provider_key,actual_model,requested_model,status,output_artifact_id FROM agent_model_call WHERE run_id=%s ORDER BY created_at,call_id", (source_run_id,))
                model_calls = [dict(item) for item in cursor.fetchall()]
                cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended('evaluation-historical-import',0))")
            successful = [item for item in model_calls if item["status"] in {"succeeded", "completed", "success"}]
            outputs = [item for item in snapshot["artifacts"] if item["artifact_type"] == "agent-output"]
            if not successful or not outputs:
                raise EvaluationConflict("historical import requires saved model calls and Run output")
            if len(outputs) != 1:
                raise EvaluationConflict("historical import needs an unambiguous Run output")
            artifact = outputs[0]
            if not isinstance(artifact["content_json"], dict) or artifact_json_hash(artifact["content_json"]) != artifact["content_hash"]:
                raise EvaluationConflict("historical Run output hash changed")
            dataset = service.register_dataset("historical-run-artifact-import", hash_json({"runId": source_run_id, "artifactHash": artifact["content_hash"]}),
                [{"caseKey": "saved-output-and-audit", "caseVersion": "1", "caseType": "sufficient_data",
                  "input": {"sourceRunId": source_run_id, "artifactId": artifact["artifact_id"], "artifactHash": artifact["content_hash"]},
                  "expected": {"rules": {"requiredFields": ["output", "artifactId", "artifactHash", "sourceRunStatus"],
                      "fieldEquals": {"artifactHash": artifact["content_hash"], "providerCallsMade": 0, "factualQuality": "unknown"}}}}],
                description="Import of previously produced Run artifact and actual audit rows; no new paid model evaluation.",
                metadata={"scope": "artifact-integrity-and-existing-audit-only", "modelQuality": "unknown"})
            provider_keys = {item["provider_key"] for item in successful}
            models = {item["actual_model"] or item["requested_model"] for item in successful}
            frozen = self._create(service, dataset, snapshot, "paid_model", provider_key=next(iter(provider_keys)) if len(provider_keys) == 1 else None,
                                  model_name=next(iter(models)) if len(models) == 1 else None)
            actual = {"status": "historical-imported", "sourceRunStatus": snapshot["run"]["status"], "output": artifact["content_json"],
                "artifactId": artifact["artifact_id"], "artifactHash": artifact["content_hash"], "providerCallsMade": 0,
                "factualQuality": "unknown", "engineeringCertification": False}
            refs = {**self._provenance(snapshot, actor), "artifactId": artifact["artifact_id"], "artifactHash": artifact["content_hash"],
                    "evaluationMeaning": "historical-artifact-and-audit-import", "modelQuality": "unknown"}
            service.record_case_result(frozen["evaluation_run_id"], dataset["cases"][0]["case_id"], actual, actual_references=refs)
            usage = service.collect_usage(frozen["evaluation_run_id"], source_run_id)
            result = service.run(frozen["evaluation_run_id"])
            result.update(providerCallsMade=0, importedAudit=usage, factualQuality="unknown", engineeringCertification=False,
                evaluationMeaning="historical-artifact-and-audit-import")
            return result

    def compare(self, actor, left_id, right_id, *, actor_token):
        _id(left_id), _id(right_id)
        with self._session(actor, actor_token) as (con, service, _):
            for value in (left_id, right_id):
                self._require_run(con, value)
            left, right = service.run(left_id), service.run(right_id)
            if left["execution_mode"] != right["execution_mode"]:
                raise EvaluationConflict("cannot compare offline benchmark and historical import")
            for field in ("dataset_id", "dataset_version", "dataset_hash", "case_set_hash", "source_versions_hash"):
                if left[field] != right[field]:
                    raise EvaluationConflict("dataset, cases or source versions differ")
            if not left["results"] or len(left["results"]) != len(right["results"]):
                raise EvaluationConflict("comparison requires the same completed case set")
            result = service.compare(left_id, right_id)
            result.update(sourceVersionsHash=left["source_versions_hash"], factualQuality="unknown", providerCallsMade=0,
                          meaning="deterministic fixed checks; historical imports measure integrity only")
            return result


def create_evaluation_router(connection_factory, *, auth=None, service=None):
    auth = auth or AdminAuth(connection_factory)
    service = service or AuthenticatedEvaluationService(connection_factory)
    router = APIRouter(prefix="/api/agent-admin/evaluations", tags=["agent-evaluations"])

    def access(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if ((origin and origin.rstrip("/") != str(request.base_url).rstrip("/"))
                    or request.headers.get("sec-fetch-site") == "cross-site"):
                raise HTTPException(403, "cross-origin evaluation operation rejected")
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE)
        try:
            actor = auth.authenticate(token)
        except Exception:
            raise HTTPException(503, "evaluation authentication is temporarily unavailable") from None
        if not isinstance(actor, Principal) or actor.kind != "human":
            raise HTTPException(401, "authenticated human operator required")
        if not set(actor.roles).intersection({"operator", "reviewer", "admin"}):
            raise HTTPException(403, "evaluation read permission required")
        if request.method == "POST" and not actor.permits("operator"):
            raise HTTPException(403, "operator or administrator permission required")
        return actor, token

    def invoke(method, identity, *args, **kwargs):
        actor, token = identity
        try:
            value = method(actor, *args, actor_token=token, **kwargs)
            # Keep a displayed source body from echoing a presented credential.
            # The saved digest always refers to the immutable original content.
            safe = _safe(value, (token,))
            if isinstance(safe, dict):
                safe["displayRedacted"] = safe != value
            return safe
        except EvaluationNotFound:
            raise HTTPException(404, "evaluation dataset, Run or source Run not found") from None
        except EvaluationConflict:
            raise HTTPException(409, "evaluation evidence or versions differ; reload immutable source data") from None
        except EvaluationProviderUnavailable:
            raise HTTPException(503, "DeepSeek is not configured for this evaluation") from None
        except EvaluationProviderFailure:
            raise HTTPException(502, "DeepSeek evaluation request failed; review the saved provider audit before retrying") from None
        except PermissionError:
            raise HTTPException(403, "current evaluation role and credential required") from None
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, "invalid evaluation request or immutable evaluation data") from None
        except Exception:
            raise HTTPException(409, "evaluation operation could not be applied") from None

    def source(body):
        if not isinstance(body, dict) or set(body) != {"sourceRunId"}:
            raise HTTPException(400, "sourceRunId required; outputs, hashes, usage and identities are server-controlled")
        return body["sourceRunId"]

    def source_variant(body):
        if not isinstance(body, dict) or set(body) != {"sourceRunId", "variant"}:
            raise HTTPException(400, "sourceRunId and server-defined variant required")
        return body["sourceRunId"], body["variant"]

    @router.get("/datasets")
    def datasets(limit: int = 100, identity=Depends(access)):
        return invoke(service.list_datasets, identity, limit=limit)

    @router.get("/datasets/{dataset_id}")
    def dataset(dataset_id: str, identity=Depends(access)):
        return invoke(service.get_dataset, identity, dataset_id)

    @router.get("/runs")
    def runs(datasetId: str | None = None, sourceRunId: str | None = None, limit: int = 100, identity=Depends(access)):
        return invoke(service.list_runs, identity, dataset_id=datasetId, source_run_id=sourceRunId, limit=limit)

    @router.get("/runs/{evaluation_id}")
    def run(evaluation_id: str, identity=Depends(access)):
        return invoke(service.get_run, identity, evaluation_id)

    @router.get("/compare")
    def compare(leftRunId: str, rightRunId: str, identity=Depends(access)):
        return invoke(service.compare, identity, leftRunId, rightRunId)

    @router.post("/benchmarks", status_code=201)
    def benchmark(body: Any = Body(...), identity=Depends(access)):
        return invoke(service.benchmark, identity, source(body))

    @router.post("/benchmarks/variants", status_code=201)
    def benchmark_variant(body: Any = Body(...), identity=Depends(access)):
        source_run_id, variant = source_variant(body)
        return invoke(service.benchmark_variant, identity, source_run_id, variant)

    @router.post("/paid-model", status_code=201)
    def paid_model(body: Any = Body(...), identity=Depends(access)):
        return invoke(service.paid_model, identity, source(body))

    @router.post("/historical-imports", status_code=201)
    def historical_import(body: Any = Body(...), identity=Depends(access)):
        return invoke(service.import_run, identity, source(body))

    return router
