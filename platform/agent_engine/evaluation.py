"""Versioned, reproducible Agent Engine evaluation.

The evaluator is deliberately provider agnostic.  It stores frozen cases and
output references, computes deterministic checks locally, and imports usage
only from rows already written by the runtime audit tables.  A paid-model
runner can submit its real output through :meth:`record_case_result`; this
module never calls a model or a web provider.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Iterable

from psycopg2.extras import Json

from .repository import new_id


CASE_TYPES = {
    "unlabelled": "unlabelled",
    "out_of_scope": "out_of_scope",
    "sufficient": "sufficient_data",
    "sufficient_data": "sufficient_data",
    "sufficient-data": "sufficient_data",
    "missing": "missing_data",
    "missing_data": "missing_data",
    "missing-data": "missing_data",
    "conflicting": "conflicting_data",
    "conflicting_data": "conflicting_data",
    "conflicting-data": "conflicting_data",
    "wrong_units": "wrong_units",
    "wrong-units": "wrong_units",
    "unauthorized": "unauthorized_action",
    "unauthorized_action": "unauthorized_action",
    "unauthorized-action": "unauthorized_action",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row_hash(value: dict[str, Any]) -> str:
    return hash_json({key: value[key] for key in sorted(value) if key not in {"created_at", "updated_at"}})


def _path_get(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for part in path.split(".") if isinstance(path, str) else tuple(path):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and str(part).isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False, None
    return True, current


def _matches_subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _matches_subset(actual[key], item) for key, item in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and actual == expected
    return actual == expected


@dataclass(frozen=True)
class Check:
    status: str
    passed: bool | None
    details: dict[str, Any]


class EvaluationService:
    """Persistence and deterministic checks for P4-09 evaluation runs."""

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @staticmethod
    def _one(cursor) -> dict[str, Any] | None:
        row = cursor.fetchone()
        return dict(row) if row else None

    @staticmethod
    def _rows(cursor) -> list[dict[str, Any]]:
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _validate_hash(value: str, label: str) -> str:
        if not isinstance(value, str) or not HEX64.fullmatch(value):
            raise ValueError(f"{label} must be a lowercase SHA-256 hash")
        # Repeated-character digests are placeholders commonly sent by callers;
        # accepting one would make a profile or prompt snapshot unverifiable.
        if len(set(value)) == 1:
            raise ValueError(f"{label} cannot be a placeholder hash")
        return value

    def _table_exists(self, cursor, table: str) -> bool:
        cursor.execute("SELECT to_regclass(%s) AS relation", (table,))
        return bool(cursor.fetchone()["relation"])

    def _dataset(self, cursor, dataset_id: str) -> dict[str, Any]:
        cursor.execute("SELECT * FROM agent_evaluation_dataset WHERE dataset_id=%s", (dataset_id,))
        row = self._one(cursor)
        if not row:
            raise ValueError("evaluation dataset not found")
        payload = _json(row["dataset_json"])
        if hash_json(payload) != row["dataset_hash"]:
            raise ValueError("evaluation dataset hash mismatch")
        return row

    def _case(self, cursor, case_id: str) -> dict[str, Any]:
        cursor.execute("SELECT * FROM agent_evaluation_case WHERE case_id=%s", (case_id,))
        row = self._one(cursor)
        if not row:
            raise ValueError("evaluation case not found")
        input_json, expected_json = _json(row["input_json"]), _json(row["expected_json"])
        if hash_json(input_json) != row["input_hash"] or hash_json(expected_json) != row["expected_hash"]:
            raise ValueError("evaluation case input or expected hash mismatch")
        identity = {"caseKey": row["case_key"], "caseVersion": row["case_version"],
                    "caseType": row["case_type"], "input": input_json, "expected": expected_json}
        if hash_json(identity) != row["case_hash"]:
            raise ValueError("evaluation case hash mismatch")
        row["input_json"], row["expected_json"] = input_json, expected_json
        return row

    def _run(self, cursor, evaluation_run_id: str) -> dict[str, Any]:
        cursor.execute("SELECT * FROM agent_evaluation_run WHERE evaluation_run_id=%s", (evaluation_run_id,))
        row = self._one(cursor)
        if not row:
            raise ValueError("evaluation run not found")
        source_versions = _json(row["source_versions_json"])
        if hash_json(source_versions) != row["source_versions_hash"]:
            raise ValueError("evaluation source version snapshot hash mismatch")
        self._dataset(cursor, row["dataset_id"])
        return row

    def register_dataset(self, dataset_key: str, dataset_version: str, cases: Iterable[dict[str, Any]], *, description: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Register an immutable case set and return its frozen identity."""
        if not isinstance(dataset_key, str) or not dataset_key.strip() or not isinstance(dataset_version, str) or not dataset_version.strip():
            raise ValueError("dataset key and version are required")
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for supplied in cases:
            item = dict(supplied)
            case_key = item.get("caseKey", item.get("case_key"))
            case_version = str(item.get("caseVersion", item.get("case_version", "1")))
            case_type = CASE_TYPES.get(str(item.get("caseType", item.get("case_type", ""))).lower())
            if not case_key or not case_type:
                raise ValueError("each case needs caseKey and a supported caseType")
            if (case_key, case_version) in seen:
                raise ValueError(f"duplicate evaluation case: {case_key}/{case_version}")
            seen.add((case_key, case_version))
            input_json = deepcopy(item.get("input", item.get("inputJson", item.get("input_json", {}))))
            expected_json = deepcopy(item.get("expected", item.get("expectedJson", item.get("expected_json", {}))))
            if not isinstance(input_json, dict) or not isinstance(expected_json, dict):
                raise ValueError("case input and expected contract must be JSON objects")
            identity = {"caseKey": case_key, "caseVersion": case_version, "caseType": case_type,
                        "input": input_json, "expected": expected_json}
            normalized.append({**identity, "inputHash": hash_json(input_json), "expectedHash": hash_json(expected_json),
                               "caseHash": hash_json(identity)})
        if not normalized:
            raise ValueError("an evaluation dataset must contain at least one case")
        payload = {"datasetKey": dataset_key, "datasetVersion": dataset_version, "description": description,
                   "metadata": deepcopy(metadata or {}), "cases": normalized}
        dataset_hash = hash_json(payload)
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_evaluation_dataset WHERE dataset_key=%s AND dataset_version=%s",
                               (dataset_key, dataset_version))
                existing = self._one(cursor)
                if existing:
                    if existing["dataset_hash"] != dataset_hash:
                        raise ValueError("dataset version already exists with a different hash")
                    return self.dataset(existing["dataset_id"])
                dataset_id = new_id("evaluation-dataset")
                # dataset_json deliberately excludes generated database IDs.
                cursor.execute("""INSERT INTO agent_evaluation_dataset
                    (dataset_id,dataset_key,dataset_version,description,dataset_json,dataset_hash)
                    VALUES(%s,%s,%s,%s,%s,%s)""",
                    (dataset_id, dataset_key, dataset_version, description, Json(payload), dataset_hash))
                for item in normalized:
                    cursor.execute("""INSERT INTO agent_evaluation_case
                        (case_id,dataset_id,case_key,case_version,case_type,input_json,expected_json,
                         input_hash,expected_hash,case_hash)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (new_id("evaluation-case"), dataset_id, item["caseKey"], item["caseVersion"], item["caseType"],
                         Json(item["input"]), Json(item["expected"]), item["inputHash"], item["expectedHash"], item["caseHash"]))
        return self.dataset(dataset_id)

    # Friendly aliases used by the API layer and task package.
    create_dataset = register_dataset
    register_evaluation_dataset = register_dataset
    create_evaluation_dataset = register_dataset

    def dataset(self, dataset_id: str) -> dict[str, Any]:
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                row = self._dataset(cursor, dataset_id)
                cursor.execute("SELECT * FROM agent_evaluation_case WHERE dataset_id=%s ORDER BY case_key,case_version", (dataset_id,))
                cases = self._rows(cursor)
        return {**row, "dataset_json": _json(row["dataset_json"]), "cases": [{**item, "input_json": _json(item["input_json"]), "expected_json": _json(item["expected_json"])} for item in cases]}

    def create_run(self, dataset_id: str, *, profile_agent_key: str = "unknown-agent", profile_version: str = "unknown", profile_hash: str | None = None,
                   profile_snapshot: dict[str, Any] | None = None, prompt_version: str = "unknown", prompt: str | None = None,
                   prompt_hash: str | None = None, case_ids: Iterable[str] | None = None, source_versions: dict[str, Any] | None = None,
                   execution_mode: str = "deterministic", source_run_id: str | None = None, provider_key: str | None = None,
                   model_name: str | None = None) -> dict[str, Any]:
        if execution_mode not in {"deterministic", "paid_model"}:
            raise ValueError("execution_mode must be deterministic or paid_model")
        if profile_snapshot is not None:
            snapshot = deepcopy(profile_snapshot)
            profile_agent_key = snapshot.get("agentKey", snapshot.get("agent_key", profile_agent_key))
            profile_version = str(snapshot.get("version", profile_snapshot.get("profileVersion", profile_version)))
            profile_hash = snapshot.get("profileHash", profile_hash) or hash_json(snapshot)
        profile_hash = profile_hash or hash_json({"agentKey": profile_agent_key, "version": profile_version})
        prompt_hash = prompt_hash or hash_text(prompt or "")
        self._validate_hash(profile_hash, "profile_hash")
        self._validate_hash(prompt_hash, "prompt_hash")
        source_versions = deepcopy(source_versions or {})
        source_versions_hash = hash_json(source_versions)
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                dataset = self._dataset(cursor, dataset_id)
                cursor.execute("SELECT case_id,case_hash FROM agent_evaluation_case WHERE dataset_id=%s ORDER BY case_key,case_version", (dataset_id,))
                available = self._rows(cursor)
                if case_ids is None:
                    selected = available
                else:
                    wanted = list(case_ids)
                    by_id = {row["case_id"]: row for row in available}
                    if set(wanted) != set(by_id).intersection(wanted) or len(wanted) != len(set(wanted)):
                        raise ValueError("case selection contains an absent, foreign, or duplicate case")
                    selected = [by_id[item] for item in wanted]
                if not selected:
                    raise ValueError("evaluation run must include at least one case")
                case_set_hash = hash_json({"datasetHash": dataset["dataset_hash"], "cases": sorted((row["case_id"], row["case_hash"]) for row in selected)})
                run_id = new_id("evaluation-run")
                cursor.execute("""INSERT INTO agent_evaluation_run
                    (evaluation_run_id,dataset_id,dataset_version,dataset_hash,case_set_hash,
                     profile_agent_key,profile_version,profile_hash,prompt_version,prompt_hash,
                     source_versions_json,source_versions_hash,execution_mode,source_run_id,provider_key,model_name)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (run_id, dataset_id, dataset["dataset_json"]["datasetVersion"], dataset["dataset_hash"], case_set_hash,
                     profile_agent_key, profile_version, profile_hash, prompt_version, prompt_hash, Json(source_versions), source_versions_hash,
                     execution_mode, source_run_id, provider_key, model_name))
                for selected_case in selected:
                    cursor.execute("INSERT INTO agent_evaluation_run_case(evaluation_run_id,case_id,case_hash) VALUES(%s,%s,%s)",
                                   (run_id, selected_case["case_id"], selected_case["case_hash"]))
        return self.run(run_id)

    create_evaluation_run = create_run

    def run(self, evaluation_run_id: str) -> dict[str, Any]:
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                row = self._run(cursor, evaluation_run_id)
                cursor.execute("SELECT * FROM agent_evaluation_case_result WHERE evaluation_run_id=%s ORDER BY created_at,result_id", (evaluation_run_id,))
                results = self._rows(cursor)
                cursor.execute("SELECT * FROM agent_evaluation_metric WHERE evaluation_run_id=%s ORDER BY created_at,metric_id", (evaluation_run_id,))
                metrics = self._rows(cursor)
                cursor.execute("SELECT * FROM agent_evaluation_usage WHERE evaluation_run_id=%s ORDER BY created_at,usage_id", (evaluation_run_id,))
                usage = self._rows(cursor)
                self._validate_usage_sources(cursor, usage)
        for item in (row, *results, *metrics, *usage):
            for key in list(item):
                if key.endswith("_json"):
                    item[key] = _json(item[key])
        return {**row, "results": results, "metrics": metrics, "usage": usage}

    def _validate_usage_sources(self, cursor, usage_rows: list[dict[str, Any]]) -> None:
        """Reject a persisted usage reference if its audit row was changed."""
        for usage in usage_rows:
            table = "agent_model_call" if usage["source_type"] == "model_call" else "agent_tool_call" if usage["source_type"] == "tool_call" else None
            if not table or not self._table_exists(cursor, table):
                continue
            cursor.execute(f"SELECT * FROM {table} WHERE call_id=%s", (usage["source_id"],))
            source = self._one(cursor)
            if not source or _row_hash(source) != usage["source_hash"]:
                raise ValueError("evaluation usage source hash mismatch")

    def _expected_output(self, expected: dict[str, Any]) -> Any:
        return expected.get("output", expected.get("expectedOutput", expected.get("expected_output")))

    def _correctness(self, case_type: str, expected: dict[str, Any], actual: dict[str, Any]) -> Check:
        rules = expected.get("rules", expected)
        details: dict[str, Any] = {}
        failures: list[str] = []
        expected_output = self._expected_output(expected)
        candidate = actual.get("output", actual) if expected_output is not None else actual
        if expected_output is not None and not _matches_subset(candidate, expected_output):
            failures.append("expected-output-mismatch")
        for path in rules.get("requiredFields", rules.get("required_fields", [])) or []:
            present, value = _path_get(actual, path)
            if not present or value is None:
                failures.append(f"missing-required-field:{path}")
        for path in rules.get("forbiddenFields", rules.get("forbidden_fields", [])) or []:
            present, _ = _path_get(actual, path)
            if present:
                failures.append(f"forbidden-field:{path}")
        for path, value in (rules.get("fieldEquals", rules.get("field_equals", {})) or {}).items():
            present, actual_value = _path_get(actual, path)
            if not present or actual_value != value:
                failures.append(f"field-mismatch:{path}")
        expected_status = rules.get("expectedStatus", rules.get("expected_status", rules.get("terminalStatus")))
        if expected_status is not None and actual.get("status") != expected_status:
            failures.append("status-mismatch")
        if rules.get("mustFail") is True and actual.get("status") in {"completed", "success", "succeeded"}:
            failures.append("expected-failure-was-success")
        if case_type == "unauthorized_action":
            action_allowed = actual.get("authorized", actual.get("actionAllowed", False))
            executed = actual.get("actionExecuted", actual.get("sideEffect", False))
            if action_allowed is True or executed is True or actual.get("status") in {"completed", "success", "succeeded"}:
                failures.append("unauthorized-action-executed")
            details["unauthorizedActionRejected"] = not failures
        if not expected and not failures:
            return Check("indeterminate", None, {"reason": "empty expected contract"})
        details["failures"] = failures
        return Check("failed" if failures else "passed", not bool(failures), details)

    def _source_text(self, cursor, run_id: str, source_id: str) -> tuple[str | None, dict[str, Any] | None]:
        if not self._table_exists(cursor, "agent_source_snapshot") or not self._table_exists(cursor, "agent_run_artifact"):
            return None, None
        cursor.execute("""SELECT s.*,a.content_json,a.content_hash AS persisted_artifact_hash
            FROM agent_source_snapshot s JOIN agent_run_artifact a ON a.artifact_id=s.artifact_id
            WHERE s.source_id=%s AND s.run_id=%s""", (source_id, run_id))
        row = self._one(cursor)
        if not row:
            return None, None
        body = _json(row["content_json"])
        if row.get("content_kind") == "markdown":
            text = body.get("markdown") if isinstance(body, dict) else None
        else:
            index = _json(row.get("locator_json") or {}).get("evidenceIndex")
            evidence = body.get("sourceEvidence", []) if isinstance(body, dict) else []
            text = evidence[index].get("quote") if isinstance(index, int) and index < len(evidence) else None
        if not isinstance(text, str) or hash_text(text) != row.get("content_hash") or row.get("persisted_artifact_hash") != row.get("artifact_hash"):
            raise ValueError("evaluation citation source hash mismatch")
        return text, row

    def _citations(self, cursor, run: dict[str, Any], case: dict[str, Any], actual: dict[str, Any]) -> Check:
        expected = case["expected_json"]
        rules = expected.get("rules", expected)
        required = rules.get("requiredCitations", rules.get("required_citations", expected.get("requiredCitations", 0)))
        required = 1 if required is True else int(required or 0)
        citations = actual.get("citations", actual.get("evidence", [])) or []
        if not required and not citations:
            return Check("not-required", None, {"count": 0})
        failures: list[str] = []
        allowed = set(rules.get("allowedSourceIds", rules.get("allowed_source_ids", [])) or [])
        for index, citation in enumerate(citations):
            if not isinstance(citation, dict):
                failures.append(f"citation-{index}-not-object")
                continue
            source_id = citation.get("sourceId", citation.get("source_id"))
            if not source_id or (allowed and source_id not in allowed):
                failures.append(f"citation-{index}-source-not-allowed")
                continue
            text, source = self._source_text(cursor, run.get("source_run_id"), source_id) if run.get("source_run_id") else (None, None)
            if source is None:
                failures.append(f"citation-{index}-source-unavailable")
                continue
            if citation.get("contentHash") and citation["contentHash"] != source["content_hash"]:
                failures.append(f"citation-{index}-content-hash")
            quote = citation.get("quote")
            start, end = citation.get("startOffset"), citation.get("endOffset")
            if not isinstance(quote, str) or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or text[start:end] != quote:
                failures.append(f"citation-{index}-offset")
        if len(citations) < required:
            failures.append("insufficient-citations")
        if not citations and required:
            failures.append("citations-required")
        return Check("failed" if failures else "passed", not bool(failures), {"count": len(citations), "required": required, "failures": failures})

    def _convergence(self, case: dict[str, Any], actual: dict[str, Any]) -> Check:
        expected = case["expected_json"]
        rules = expected.get("convergence", expected.get("rules", {}).get("convergence", {})) or {}
        if not rules:
            return Check("not-required", None, {})
        failures: list[str] = []
        if rules.get("mustTerminate", rules.get("must_terminate", False)) and actual.get("terminated", actual.get("status") in {"completed", "completed-with-gaps", "requires-human-review", "blocked", "failed"}) is not True:
            failures.append("not-terminated")
        for key in ("maxIterations", "maxToolCalls", "maxModelCalls"):
            if key in rules and isinstance(actual.get(key), (int, float)) and actual[key] > rules[key]:
                failures.append(f"{key}-exceeded")
        terminal = rules.get("terminalStatuses", rules.get("terminal_statuses"))
        if terminal and actual.get("status") not in terminal:
            failures.append("non-terminal-status")
        return Check("failed" if failures else "passed", not bool(failures), {"failures": failures})

    def record_case_result(self, evaluation_run_id: str, case_id: str, actual_output: dict[str, Any], *, actual_references: dict[str, Any] | None = None, usage_run_id: str | None = None) -> dict[str, Any]:
        if not isinstance(actual_output, dict):
            raise ValueError("actual output must be a JSON object")
        actual_output = deepcopy(actual_output)
        actual_hash = hash_json(actual_output)
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                run = self._run(cursor, evaluation_run_id)
                case = self._case(cursor, case_id)
                if case["dataset_id"] != run["dataset_id"]:
                    raise ValueError("case belongs to a different evaluation dataset")
                cursor.execute("SELECT case_hash FROM agent_evaluation_run_case WHERE evaluation_run_id=%s AND case_id=%s", (evaluation_run_id, case_id))
                selected_case = cursor.fetchone()
                if not selected_case or selected_case["case_hash"] != case["case_hash"]:
                    raise ValueError("case is not part of the frozen evaluation run")
                cursor.execute("SELECT result_id FROM agent_evaluation_case_result WHERE evaluation_run_id=%s AND case_id=%s", (evaluation_run_id, case_id))
                if cursor.fetchone():
                    raise ValueError("evaluation case result already exists")
                correctness = self._correctness(case["case_type"], case["expected_json"], actual_output)
                citation = self._citations(cursor, run, case, actual_output)
                convergence = self._convergence(case, actual_output)
                if case["case_type"] == "unauthorized_action":
                    rejected = actual_output.get("authorized", actual_output.get("actionAllowed", False)) is not True
                    executed = actual_output.get("actionExecuted", actual_output.get("sideEffect", False)) is True
                    successful = actual_output.get("status") in {"completed", "success", "succeeded"}
                    authorization = Check("passed" if rejected and not executed and not successful else "failed",
                                           rejected and not executed and not successful,
                                           {"actionRejected": rejected and not executed and not successful})
                else:
                    authorization = Check("not-applicable", None, {})
                details = {"correctness": correctness.details, "citation": citation.details, "convergence": convergence.details, "authorization": authorization.details}
                statuses = {"deterministicStatus": correctness.status, "deterministicCorrect": correctness.passed,
                            "citationStatus": citation.status, "convergenceStatus": convergence.status, "authorizationStatus": authorization.status,
                            "caseHash": case["case_hash"], "actualOutputHash": actual_hash}
                result_identity = {**statuses, "details": details, "references": actual_references or {}}
                result_hash = hash_json(result_identity)
                result_id = new_id("evaluation-result")
                cursor.execute("""INSERT INTO agent_evaluation_case_result
                    (result_id,evaluation_run_id,case_id,case_hash,actual_output_json,actual_output_hash,
                     actual_references_json,deterministic_status,deterministic_correct,citation_status,
                     convergence_status,authorization_status,details_json,result_hash)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (result_id, evaluation_run_id, case_id, case["case_hash"], Json(actual_output), actual_hash, Json(actual_references or {}),
                     correctness.status, bool(correctness.passed), citation.status, convergence.status, authorization.status, Json(details), result_hash))
                metric_rows = [("correctness", "deterministic_correctness", correctness.passed, correctness.status, correctness.details),
                               ("citation", "citation_validity", citation.passed, citation.status, citation.details),
                               ("convergence", "convergence_termination", convergence.passed, convergence.status, convergence.details)]
                if case["case_type"] == "unauthorized_action":
                    metric_rows.append(("authorization", "out_of_scope_action_rejected", authorization.passed, authorization.status, authorization.details))
                for kind, key, value, status, metric_details in metric_rows:
                    metric_identity = {"run": evaluation_run_id, "result": result_id, "kind": kind, "key": key,
                                       "value": value, "status": status, "details": metric_details}
                    cursor.execute("""INSERT INTO agent_evaluation_metric
                        (metric_id,evaluation_run_id,result_id,metric_kind,metric_key,metric_value,metric_status,details_json,metric_hash)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (new_id("evaluation-metric"), evaluation_run_id, result_id, kind, key,
                         None if value is None else (1 if value else 0), status, Json(metric_details), hash_json(metric_identity)))
        if usage_run_id:
            self.collect_usage(evaluation_run_id, usage_run_id, result_id=result_id)
        return self.result(evaluation_run_id, result_id)

    evaluate_case = record_case_result

    def result(self, evaluation_run_id: str, result_id: str) -> dict[str, Any]:
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                self._run(cursor, evaluation_run_id)
                cursor.execute("SELECT * FROM agent_evaluation_case_result WHERE evaluation_run_id=%s AND result_id=%s", (evaluation_run_id, result_id))
                result = self._one(cursor)
                if not result:
                    raise ValueError("evaluation result not found")
                cursor.execute("SELECT * FROM agent_evaluation_metric WHERE evaluation_run_id=%s AND result_id=%s ORDER BY metric_id", (evaluation_run_id, result_id))
                metrics = self._rows(cursor)
        for item in (result, *metrics):
            for key in list(item):
                if key.endswith("_json"):
                    item[key] = _json(item[key])
        return {**result, "metrics": metrics}

    def collect_usage(self, evaluation_run_id: str, source_run_id: str | None = None, *, result_id: str | None = None) -> dict[str, Any]:
        """Persist only usage present in model/tool audit rows.

        Missing rows, tokens, and prices remain unknown.  This method never
        fabricates a zero-cost or successful provider call.
        """
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                run = self._run(cursor, evaluation_run_id)
                source_run_id = source_run_id or run.get("source_run_id")
                if not source_run_id:
                    return {"modelCalls": 0, "toolCalls": 0, "inputTokens": None, "outputTokens": None, "cost": None, "sources": []}
                rows: list[tuple[str, dict[str, Any]]] = []
                # Research profiles share one budget across the root Run and
                # its collaboration children.  Model-call rows carry the
                # immutable root_run_id; the tool-call table deliberately does
                # not, so derive the same tree's run ids from the model audit
                # rows before collecting tool calls.  This keeps historical
                # usage complete without assuming a column the tool schema
                # does not expose.
                tree_run_ids = {source_run_id}
                if self._table_exists(cursor, "agent_model_call"):
                    cursor.execute("SELECT * FROM agent_model_call WHERE run_id=%s OR root_run_id=%s ORDER BY created_at,call_id",
                                   (source_run_id, source_run_id))
                    model_rows = self._rows(cursor)
                    rows.extend(("model_call", row) for row in model_rows)
                    tree_run_ids.update(row.get("run_id") for row in model_rows if row.get("run_id"))
                if self._table_exists(cursor, "agent_tool_call"):
                    cursor.execute("SELECT * FROM agent_tool_call WHERE run_id = ANY(%s) ORDER BY created_at,call_id",
                                   (list(tree_run_ids),))
                    rows.extend(("tool_call", row) for row in self._rows(cursor))
                inserted: list[str] = []
                for source_type, row in rows:
                    source_id = row.get("call_id") or row.get("id")
                    if not source_id:
                        continue
                    usage = {key: row.get(key) for key in ("status", "requested_model", "actual_model", "provider_key", "input_tokens", "output_tokens", "latency_ms", "retry_count", "http_status", "error_code") if key in row}
                    source_hash = _row_hash(row)
                    cursor.execute("SELECT source_hash FROM agent_evaluation_usage WHERE evaluation_run_id=%s AND source_type=%s AND source_id=%s",
                                   (evaluation_run_id, source_type, source_id))
                    previous = cursor.fetchone()
                    if previous and previous["source_hash"] != source_hash:
                        raise ValueError("evaluation usage source hash mismatch")
                    cursor.execute("""INSERT INTO agent_evaluation_usage
                        (usage_id,evaluation_run_id,result_id,source_type,source_id,provider_key,model_name,
                         input_tokens,output_tokens,latency_ms,cost,usage_json,source_hash)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(evaluation_run_id,source_type,source_id) DO NOTHING RETURNING usage_id""",
                        (new_id("evaluation-usage"), evaluation_run_id, result_id, source_type, source_id,
                         row.get("provider_key"), row.get("actual_model") or row.get("requested_model"), row.get("input_tokens"),
                         row.get("output_tokens"), row.get("latency_ms"), row.get("cost", row.get("cost_usd")), Json(usage), source_hash))
                    saved = cursor.fetchone()
                    if saved:
                        inserted.append(saved["usage_id"])
                cursor.execute("SELECT * FROM agent_evaluation_usage WHERE evaluation_run_id=%s ORDER BY created_at,usage_id", (evaluation_run_id,))
                usage_rows = self._rows(cursor)
                totals = {"modelCalls": sum(row["source_type"] == "model_call" for row in usage_rows),
                          "toolCalls": sum(row["source_type"] == "tool_call" for row in usage_rows),
                          "inputTokens": sum(row["input_tokens"] for row in usage_rows if row["input_tokens"] is not None) if any(row["input_tokens"] is not None for row in usage_rows) else None,
                          "outputTokens": sum(row["output_tokens"] for row in usage_rows if row["output_tokens"] is not None) if any(row["output_tokens"] is not None for row in usage_rows) else None,
                          "cost": sum(row["cost"] for row in usage_rows if row["cost"] is not None) if any(row["cost"] is not None for row in usage_rows) else None,
                          "sources": [row["source_id"] for row in usage_rows]}
                for key, value in (("model_calls", totals["modelCalls"]), ("tool_calls", totals["toolCalls"]),
                                   ("input_tokens", totals["inputTokens"]), ("output_tokens", totals["outputTokens"]),
                                   ("cost", totals["cost"])):
                    cursor.execute("SELECT metric_id FROM agent_evaluation_metric WHERE evaluation_run_id=%s AND result_id IS NULL AND metric_kind='usage' AND metric_key=%s", (evaluation_run_id, key))
                    if cursor.fetchone():
                        continue
                    details = {"sourceRunId": source_run_id, "actual": value is not None, "sourceIds": totals["sources"]}
                    identity = {"run": evaluation_run_id, "kind": "usage", "key": key, "value": value, "details": details}
                    cursor.execute("""INSERT INTO agent_evaluation_metric
                        (metric_id,evaluation_run_id,metric_kind,metric_key,metric_value,metric_status,details_json,metric_hash)
                        VALUES(%s,%s,'usage',%s,%s,%s,%s,%s)""",
                        (new_id("evaluation-metric"), evaluation_run_id, key, value,
                         "passed" if value is not None else "unknown", Json(details), hash_json(identity)))
        return totals

    usage = collect_usage

    def evaluate_run(self, evaluation_run_id: str, outputs: dict[str, dict[str, Any]], *, references: dict[str, dict[str, Any]] | None = None,
                     usage_run_id: str | None = None) -> dict[str, Any]:
        """Evaluate a frozen case set from caller-supplied, already-produced outputs.

        ``outputs`` is keyed by immutable case ID.  Missing or foreign IDs are
        rejected so a partial result cannot silently be presented as a complete
        benchmark.  The caller remains responsible for invoking a paid model;
        this method only evaluates the resulting JSON.
        """
        if not isinstance(outputs, dict) or not outputs:
            raise ValueError("outputs must contain at least one case result")
        frozen = self.run(evaluation_run_id)
        case_ids = {row["case_id"] for row in self._cases_for_run(evaluation_run_id)}
        if set(outputs) != case_ids:
            raise ValueError("outputs must contain exactly the frozen evaluation cases")
        result_ids = []
        for case_id, output in outputs.items():
            result = self.record_case_result(evaluation_run_id, case_id, output,
                                             actual_references=(references or {}).get(case_id),
                                             usage_run_id=usage_run_id)
            result_ids.append(result["result_id"])
        return {**self.run(evaluation_run_id), "resultIds": result_ids}

    def _cases_for_run(self, evaluation_run_id: str) -> list[dict[str, Any]]:
        with self.connection_factory() as connection:
            with connection.cursor() as cursor:
                run = self._run(cursor, evaluation_run_id)
                cursor.execute("""SELECT c.* FROM agent_evaluation_case c
                    JOIN agent_evaluation_run_case rc ON rc.case_id=c.case_id
                    WHERE rc.evaluation_run_id=%s ORDER BY c.case_key,c.case_version""", (evaluation_run_id,))
                rows = self._rows(cursor)
        return rows

    def compare(self, left_run_id: str, right_run_id: str) -> dict[str, Any]:
        left, right = self.run(left_run_id), self.run(right_run_id)
        for field in ("dataset_id", "dataset_version", "dataset_hash", "case_set_hash", "source_versions_hash"):
            if left[field] != right[field]:
                raise ValueError(f"cannot compare evaluation runs with different {field}")
        def score(item: dict[str, Any]) -> float | None:
            values = [row["deterministic_correct"] for row in item["results"]]
            return sum(values) / len(values) if values else None
        return {"dataset": {"id": left["dataset_id"], "version": left["dataset_version"], "hash": left["dataset_hash"]},
                "cases": len(left["results"]), "left": {"runId": left_run_id, "profileVersion": left["profile_version"], "promptVersion": left["prompt_version"], "correctness": score(left)},
                "right": {"runId": right_run_id, "profileVersion": right["profile_version"], "promptVersion": right["prompt_version"], "correctness": score(right)},
                "delta": None if score(left) is None or score(right) is None else score(right) - score(left)}

    compare_runs = compare


AgentEvaluationService = EvaluationService

__all__ = ["CASE_TYPES", "AgentEvaluationService", "EvaluationService", "hash_json", "hash_text"]
