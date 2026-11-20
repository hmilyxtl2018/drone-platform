"""Build a disposable full-schema database and exercise P4-03 in a real browser."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.request import Request, urlopen

import psycopg2
from psycopg2 import sql

from agent_admin.credentials import AdminAuth
from agent_engine.bootstrap import build_engine
from agent_engine.postgres import connection_factory
from agent_engine.worker import Lease, worker_context
from configure_agent_engine_phase3 import configure
from migrate_agent_engine import migrate
from agent_tools.evidence import content_hash


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [
    f"platform/migrations/{name}"
    for name in [
        "032_agent_engine_phase3_foundation_postgres.sql",
        "033_agent_engine_worker_postgres.sql",
        "034_agent_engine_evidence_postgres.sql",
        "035_agent_engine_admin_review_postgres.sql",
        "036_agent_engine_handoff_postgres.sql",
        "037_agent_engine_review_postgres.sql",
        "038_agent_engine_legacy_link_postgres.sql",
        "039_agent_engine_followup_postgres.sql",
        "040_agent_admin_identity_management_postgres.sql",
        "041_agent_admin_config_publication_postgres.sql",
        "042_agent_workbench_workspace_postgres.sql",
        "043_agent_engine_engineering_tools_postgres.sql",
        "044_agent_engine_knowledge_publication_postgres.sql",
        "045_agent_engine_traceability_postgres.sql",
        "046_agent_engine_evaluation_postgres.sql",
        "047_agent_engine_operations_postgres.sql",
        "048_agent_engine_restore_function_path_postgres.sql",
        "049_agent_engine_manual_calculation_postgres.sql",
    ]
]


def wait_for_api(process, url):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("isolated API exited before becoming ready")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("isolated API did not become ready")


def http_json(url, token, *, method="GET", body=None):
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=payload, method=method,
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except Exception as error:
        status = getattr(error, "code", None)
        raw = error.read().decode("utf-8") if hasattr(error, "read") else str(error)
        try:
            value = json.loads(raw)
        except Exception:
            value = {"error": raw}
        return status, value


def lease_fixture_run(factory, run_id, owner, generation=1):
    with factory() as con:
        with con.cursor() as cursor:
            cursor.execute("UPDATE agent_run_queue SET status='leased',owner_id=%s,generation=%s,attempt=1,"
                           "lease_until=clock_timestamp() + interval '10 minutes',heartbeat_at=clock_timestamp() "
                           "WHERE run_id=%s RETURNING run_id", (owner, generation, run_id))
            if not cursor.fetchone():
                raise RuntimeError("fixture Run queue row is missing")
    return Lease(run_id, "default", owner, generation, 1)


def main():
    database = "agent_engine_verify_" + secrets.token_hex(8)
    config = json.loads((ROOT / "db.local.json").read_text(encoding="utf-8"))
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "platform")
    environment["PGPASSWORD"] = config.get("password", "")
    args = ["-h", str(config.get("host", "127.0.0.1")), "-p", str(config.get("port", 5432)),
            "-U", config["user"], "-d", database]
    port = 18191 + secrets.randbelow(1000)
    stop_file = ROOT / ".local" / ("phase4-workspace-stop-" + secrets.token_hex(6))
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    created = False
    process = None
    report = {"passed": False, "scope": "disposable PostgreSQL database; real Agent Admin API and browser; synthetic Run admitted and idempotency verified; worker execution, source reads, and provider calls are excluded",
              "paidCalls": 0, "liveSourceWrites": 0, "migrations": None}
    try:
        with closing(psycopg2.connect(**{**config, "dbname": "postgres"})) as con:
            con.autocommit = True
            with con.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                created = True
        result = subprocess.run([r"C:\Program Files\PostgreSQL\17\bin\psql.exe", *args,
                                 "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "-f",
                                 str(ROOT / "db/ontology-agent-admin-postgres.sql")],
                                env=environment, capture_output=True)
        if result.returncode:
            raise RuntimeError("isolated base schema restore failed")
        report["migrations"] = migrate(MIGRATIONS, database=database, repeats=2)
        report["configuration"] = configure(database)
        factory = connection_factory(database=database)
        issued = AdminAuth(factory).issue_token("Phase 4 isolated workspace browser test", ["admin", "operator", "reviewer"], 1)
        fixture_engine = build_engine(database=database)
        fixture_profile = fixture_engine.loader.load("uav-capability-analyst")
        evaluation_source_run = fixture_engine.repo.create_run(fixture_profile, {"entityId": "synthetic-evaluation-fixture"})
        for status in ("queued", "planning", "running", "requires-human-review"):
            fixture_engine.repo.transition(evaluation_source_run, status)
        source_text = "Synthetic fixed benchmark fixture. No real UAV facts or measurements are represented."
        source_artifact = fixture_engine.repo.add_artifact(evaluation_source_run, "web-source-snapshot", {
            "contentKind": "markdown", "markdown": source_text, "contentHash": content_hash(source_text),
            "sourceUri": "https://synthetic.invalid/phase4-benchmark", "sourceVersion": "fixture-v1"})
        fixture_engine.repo.register_source_snapshot(evaluation_source_run, source_artifact)
        python = sys.executable
        process = subprocess.Popen([python, "platform/run_agent_engine_api.py", "--database", database,
                                    "--port", str(port), "--stop-file", str(stop_file)],
                                   cwd=ROOT, env=environment, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        base_url = f"http://127.0.0.1:{port}"
        wait_for_api(process, base_url + "/health")
        environment["AGENT_ADMIN_TEST_BASE_URL"] = base_url
        environment["AGENT_EVALUATION_TEST_SOURCE_RUN"] = evaluation_source_run
        node = Path.home() / ".cache/runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        if not node.exists():
            node = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        browser_result = subprocess.run([str(node), "platform/verify_agent_engine_phase4_workspaces.mjs"],
                                        cwd=ROOT, env=environment, input=issued["token"], text=True,
                                        capture_output=True, check=False)
        if browser_result.returncode:
            raise RuntimeError("isolated workspace browser flow failed; inspect workspace-console.json")
        browser_report_path = ROOT / "docs/verification/phase4/workspace-console.json"
        browser_report = json.loads(browser_report_path.read_text(encoding="utf-8"))
        workspace_id = browser_report["workspaceId"]
        run_id = browser_report["admission"]["runId"]
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT w.current_revision,count(r.revision) AS revision_count,"
                               "array_agg(r.content_hash ORDER BY r.revision) AS hashes "
                               "FROM agent_research_workspace w JOIN agent_workspace_revision r USING(workspace_id) "
                               "WHERE w.workspace_id=%s GROUP BY w.current_revision", (workspace_id,))
                row = cursor.fetchone()
                cursor.execute("SELECT count(*) AS runs FROM agent_workspace_run WHERE workspace_id=%s", (workspace_id,))
                bindings = cursor.fetchone()["runs"]
                cursor.execute("SELECT b.revision,b.revision_hash,w.current_revision,r.input_json,r.status,q.status AS queue_status "
                               "FROM agent_workspace_run b JOIN agent_research_workspace w USING(workspace_id) "
                               "JOIN agent_run r USING(run_id) JOIN agent_run_queue q USING(run_id) "
                               "WHERE b.workspace_id=%s AND b.run_id=%s", (workspace_id, run_id))
                binding_row = cursor.fetchone()
                cursor.execute("SELECT count(*) AS queued FROM agent_run_queue WHERE run_id=%s", (run_id,))
                queued = cursor.fetchone()["queued"]
                cursor.execute("SELECT count(*) AS calls FROM agent_model_call WHERE run_id=%s", (run_id,))
                calls = cursor.fetchone()["calls"]
                cursor.execute("SELECT count(*) AS calls FROM agent_tool_call WHERE run_id=%s", (run_id,))
                tool_calls = cursor.fetchone()["calls"]
                engineering_ids = [browser_report.get("engineeringMass", {}).get("runId"),
                                   browser_report.get("engineeringMissing", {}).get("runId")]
                engineering_ids = [item for item in engineering_ids if item]
                cursor.execute("SELECT count(*) AS n FROM agent_engineering_run WHERE run_id=ANY(%s) AND parent_run_id=%s",
                               (engineering_ids, evaluation_source_run))
                engineering_children = cursor.fetchone()["n"]
                cursor.execute("SELECT count(*) AS n FROM agent_tool_call WHERE run_id=ANY(%s)", (engineering_ids,))
                engineering_tool_calls = cursor.fetchone()["n"]
                cursor.execute("SELECT count(*) AS n FROM agent_run_artifact WHERE run_id=ANY(%s) AND artifact_type='engineering-calculation'",
                               (engineering_ids,))
                engineering_artifacts = cursor.fetchone()["n"]
                cursor.execute("SELECT count(*) AS n FROM agent_model_call WHERE run_id=ANY(%s)", (engineering_ids,))
                engineering_model_calls = cursor.fetchone()["n"]
        report.update(browser=browser_report, evaluationSourceRunId=evaluation_source_run, persisted={"currentRevision": row["current_revision"],
                       "revisionCount": row["revision_count"], "revisionHashes": row["hashes"],
                       "runBindings": bindings, "queueRows": queued, "modelCallRows": calls,
                       "toolCallRows": tool_calls, "runId": run_id,
                       "bindingRevision": binding_row["revision"] if binding_row else None,
                       "boundRevisionHash": binding_row["revision_hash"] if binding_row else None,
                       "queuedRunStatus": binding_row["status"] if binding_row else None,
                       "queueStatus": binding_row["queue_status"] if binding_row else None,
                       "inputRevision": binding_row["input_json"].get("workspaceRevision") if binding_row else None,
                       "inputRevisionHash": binding_row["input_json"].get("workspaceRevisionHash") if binding_row else None,
                       "engineeringChildRuns": engineering_children, "engineeringToolCalls": engineering_tool_calls,
                       "engineeringArtifacts": engineering_artifacts, "engineeringModelCalls": engineering_model_calls},
                       database=database, port=port)

        # Complete the admitted first Run with an immutable synthetic report,
        # then exercise the real workspace HTTP successor path. This fixture
        # deliberately performs no provider call and reads no UAV SQLite data.
        first_lease = lease_fixture_run(factory, run_id, "phase4-followup-first")
        with worker_context(first_lease):
            fixture_engine.repo.transition(run_id, "queued")
            fixture_engine.repo.transition(run_id, "planning")
            fixture_engine.repo.transition(run_id, "running")
            # Charge one synthetic parent resource before creating the successor.
            # This stays inside the owned fixture and proves that a follow-up does
            # not accidentally reuse the predecessor's used counter.
            fixture_engine.repo.reserve_budget(run_id, {"tool_calls": 1},
                                               "phase4-parent-synthetic-charge")
            fixture_engine.repo.transition(run_id, "completed", output={
                "status": "completed", "summary": "synthetic first workspace result",
                "inputRevision": 2, "citations": []})
        status, workspace = http_json(base_url + "/api/agent-admin/workspaces/" + workspace_id,
                                      issued["token"])
        if status != 200:
            raise RuntimeError("workspace could not be reloaded for successor revision")
        successor_content = workspace["revision"]["content_json"]
        successor_content["question"] = "The revised question adds a follow-up material comparison."
        successor_content["materials"] = [{"title": item["title"], "text": item["text"]}
                                           for item in successor_content.get("materials", [])] + [{
            "title": "Synthetic follow-up note",
            "text": "Additional unverified fixture material for revision three."}]
        status, successor = http_json(base_url + "/api/agent-admin/workspaces/" + workspace_id,
                                      issued["token"], method="PUT",
                                      body={"expectedRevision": 2, "content": successor_content})
        if status != 200 or successor.get("revision") != 3:
            raise RuntimeError("workspace successor revision was not saved")
        followup_body = {
            "revision": 3, "idempotencyKey": "phase4-followup-real-http",
            "options": {"collaborate": False, "includeDesign": False,
                         "budgetLimits": {"model_calls": 0, "search_calls": 0,
                                           "scrape_calls": 0}},
            "previousRunId": run_id,
            "reason": "Compare the revised workspace material with the completed first Run.",
        }
        status, followup = http_json(base_url + "/api/agent-admin/workspaces/" + workspace_id + "/runs",
                                     issued["token"], method="POST", body=followup_body)
        if status != 201:
            raise RuntimeError("workspace successor Run admission failed: " + json.dumps(followup, ensure_ascii=False))
        followup_id = followup["runId"]
        replay_status, replay = http_json(base_url + "/api/agent-admin/workspaces/" + workspace_id + "/runs",
                                          issued["token"], method="POST", body=followup_body)
        followup_lease = lease_fixture_run(factory, followup_id, "phase4-followup-successor")
        with worker_context(followup_lease):
            fixture_engine.repo.transition(followup_id, "queued")
            fixture_engine.repo.transition(followup_id, "planning")
            fixture_engine.repo.transition(followup_id, "running")
            fixture_engine.repo.transition(followup_id, "completed", output={
                "status": "completed", "summary": "synthetic revised workspace result",
                "inputRevision": 3, "citations": []})
        # Read the persisted budget rows after the successor has been admitted
        # and completed.  These assertions are deliberately independent of the
        # browser report: the successor must own its own root, use the limits
        # requested in this admission, and start with zero consumption even
        # though its predecessor has already consumed one synthetic unit.
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute(
                    "SELECT m.run_id,m.root_run_id,m.parent_run_id,b.limits_json,b.used_json "
                    "FROM agent_run_budget_member m JOIN agent_run_budget b USING(root_run_id) "
                    "WHERE m.run_id IN (%s,%s) ORDER BY m.run_id", (run_id, followup_id))
                budget_rows = {row["run_id"]: dict(row) for row in cursor.fetchall()}
                cursor.execute("SELECT input_json FROM agent_run WHERE run_id=%s", (followup_id,))
                successor_input = cursor.fetchone()["input_json"]
                cursor.execute("SELECT profile_json FROM agent_run_profile_snapshot WHERE run_id=%s",
                               (followup_id,))
                successor_profile = cursor.fetchone()["profile_json"]
                cursor.execute("SELECT count(*) AS n FROM agent_budget_reservation WHERE run_id=%s",
                               (followup_id,))
                successor_reservations = cursor.fetchone()["n"]
                cursor.execute("SELECT count(*) AS n FROM agent_budget_reservation WHERE run_id=%s",
                               (run_id,))
                predecessor_reservations = cursor.fetchone()["n"]
        successor_budget = budget_rows[followup_id]
        predecessor_budget = budget_rows[run_id]
        requested_limits = successor_input.get("budgetLimits", {})
        effective_limits = successor_budget["limits_json"]
        configured_limits = successor_profile["resourcePolicy"]["budgetLimits"]
        requested_fit = all(effective_limits.get(key) == value
                            for key, value in requested_limits.items())
        configured_bound = all(effective_limits.get(key, 0) <= configured_limits.get(key, 0)
                               for key in effective_limits)
        budget_evidence = {
            "scope": "owned disposable PostgreSQL fixture; synthetic workspace Runs; no provider or UAV source calls",
            "passed": bool(
                successor_budget["root_run_id"] == followup_id
                and successor_budget["parent_run_id"] is None
                and successor_budget["used_json"] == {}
                and requested_fit and configured_bound
                and predecessor_budget["root_run_id"] == run_id
                and predecessor_budget["used_json"].get("tool_calls") == 1
                and predecessor_reservations == 1
                and successor_reservations == 0
            ),
            "predecessor": {
                "runId": run_id,
                "rootRunId": predecessor_budget["root_run_id"],
                "parentRunId": predecessor_budget["parent_run_id"],
                "limits": predecessor_budget["limits_json"],
                "used": predecessor_budget["used_json"],
                "reservationCount": predecessor_reservations,
            },
            "successor": {
                "runId": followup_id,
                "rootRunId": successor_budget["root_run_id"],
                "parentRunId": successor_budget["parent_run_id"],
                "requestedLimits": requested_limits,
                "effectiveLimits": effective_limits,
                "configuredLimits": configured_limits,
                "used": successor_budget["used_json"],
                "reservationCount": successor_reservations,
                "initialUsedIsZero": successor_budget["used_json"] == {},
                "requestedLimitsMatchEffective": requested_fit,
                "effectiveLimitsWithinConfigured": configured_bound,
            },
            "independentRoot": successor_budget["root_run_id"] != predecessor_budget["root_run_id"],
            "successorInputBudgetLimits": requested_limits,
        }
        report["budgetIsolation"] = budget_evidence
        if not budget_evidence["passed"]:
            raise RuntimeError("successor budget ownership or isolation assertions failed")
        compare_status, comparison = http_json(base_url + "/api/agent-admin/workspaces/" + workspace_id +
                                                "/compare?leftRunId=" + run_id + "&rightRunId=" + followup_id,
                                                issued["token"])
        report["followup"] = {
            "firstRunId": run_id, "successorRunId": followup_id,
            "successorRevision": successor["revision"], "successorRevisionHash": successor["contentHash"],
            "admissionStatus": status, "replayStatus": replay_status,
            "replayRunId": replay.get("runId"), "replayCreated": replay.get("created"),
            "sameRunOnReplay": replay.get("runId") == followup_id and replay.get("created") is False,
            "compareStatus": compare_status,
            "changedInputFields": comparison.get("changedInputFields", []) if comparison else [],
            "outputChanged": comparison.get("outputChanged") if comparison else None,
            "firstReportArtifacts": comparison.get("left", {}).get("reportArtifacts", []) if comparison else [],
            "successorReportArtifacts": comparison.get("right", {}).get("reportArtifacts", []) if comparison else [],
            "historicalComparisonMeaning": comparison.get("comparison") if comparison else None,
        }
        if (replay_status != 201 or not report["followup"]["sameRunOnReplay"] or compare_status != 200
                or not {"question", "materials"}.issubset(set(report["followup"]["changedInputFields"]))
                or report["followup"]["outputChanged"] is not True
                or not report["followup"]["firstReportArtifacts"]
                or not report["followup"]["successorReportArtifacts"]):
            raise RuntimeError("workspace successor comparison or idempotency checks failed")
        report["persisted"]["followupRunId"] = followup_id
        report["persisted"]["followupRevision"] = successor["revision"]
        report["persisted"]["followupCompareStatus"] = compare_status
        report["persisted"]["followupChangedInputFields"] = report["followup"]["changedInputFields"]
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute("UPDATE agent_admin_token SET revoked_at=coalesce(revoked_at,now()) WHERE principal_id=%s", (issued["principalId"],))
                cursor.execute("UPDATE agent_admin_identity SET enabled=false WHERE principal_id=%s", (issued["principalId"],))
        report["temporaryIdentityRevoked"] = True
        report["passed"] = bool(browser_report["passed"] and row["current_revision"] == 2
                                and row["revision_count"] == 2 and len(set(row["hashes"])) == 2
                                and bindings == 1 and queued == 1 and calls == 0 and tool_calls == 0
                                and binding_row is not None and binding_row["revision"] == 2
                                and binding_row["revision_hash"] == row["hashes"][-1]
                                and binding_row["input_json"].get("workspaceRevision") == 2
                                and binding_row["input_json"].get("workspaceRevisionHash") == row["hashes"][-1]
                                and engineering_children == 2 and engineering_tool_calls == 2
                                and engineering_artifacts == 2 and engineering_model_calls == 0
                                and browser_report["engineeringMass"]["runId"] in engineering_ids
                                and browser_report["engineeringMissing"]["runId"] in engineering_ids
                                and report.get("followup", {}).get("sameRunOnReplay") is True
                                and report.get("followup", {}).get("compareStatus") == 200
                                and {"question", "materials"}.issubset(set(report.get("followup", {}).get("changedInputFields", [])))
                                and report.get("followup", {}).get("outputChanged") is True
                                and bool(report.get("followup", {}).get("firstReportArtifacts"))
                                and bool(report.get("followup", {}).get("successorReportArtifacts")))
        if not report["passed"]:
            raise RuntimeError("isolated workspace persistence checks failed")
    finally:
        if process is not None:
            stop_file.write_text("stop", encoding="utf-8")
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        stop_file.unlink(missing_ok=True)
        if created:
            with closing(psycopg2.connect(**{**config, "dbname": "postgres"})) as con:
                con.autocommit = True
                with con.cursor() as cursor:
                    cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
            report["temporaryDatabaseDropped"] = True
        path = ROOT / "docs/verification/phase4/workspace-console.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if "browser" not in report and path.exists():
            try:
                report["browser"] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        report["database"] = database
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        budget_path = ROOT / "docs/verification/phase4/workspace-budget-isolation.json"
        budget_path.write_text(json.dumps(report.get("budgetIsolation", {
            "passed": False,
            "scope": "owned disposable PostgreSQL fixture",
            "error": "budget assertions did not run"
        }), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "temporaryDatabaseDropped": report.get("temporaryDatabaseDropped"),
                      "paidCalls": report["paidCalls"], "liveSourceWrites": report["liveSourceWrites"],
                      "runAdmission": report.get("persisted"), "engineering": report.get("persisted", {})}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
