"""Verify a killed worker and real second-process recovery in a unique pool.

No provider is invoked. The verifier preserves its fixture Run and immutable
evidence in PostgreSQL, never stops the normal API/worker, and removes only its
own temporary signal files and child processes.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

import psycopg2

from agent_engine.bootstrap import build_engine
from agent_engine.evidence_repository import artifact_json_hash
from agent_engine.operations import OperationsService
from agent_engine.postgres import connection_factory
from agent_engine.worker import Lease, Worker, WorkerQueue, worker_context
from agent_tools.fixtures import X200_CAPABILITY_INPUT


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "verify-restart-"


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _child(args):
    if not args.pool.startswith(PREFIX) or not args.run_id or not args.signal:
        raise ValueError("restart child requires an owned pool, Run and signal")
    engine = build_engine(args.config, args.database)
    base = engine.base_connection_factory
    queue = WorkerQueue(base, args.pool)
    row = queue.get(args.run_id)
    if not row or row["pool_key"] != args.pool:
        raise ValueError("restart child Run is outside the owned pool")
    with base() as con:
        with con.cursor() as cursor:
            cursor.execute("SELECT count(*) AS n FROM agent_run_queue WHERE pool_key=%s", (args.pool,))
            if cursor.fetchone()["n"] != 1:
                raise ValueError("restart pool must contain exactly its verifier Run")
    owner = args.pool + ("-crash" if args.child == "crash" else "-recovered")
    operations = OperationsService(base)
    operations.record_worker_heartbeat(owner, args.pool)
    if args.child == "crash":
        original = engine.gateway.handlers["uav.query_flight_logs"]

        async def interrupted_tool(payload, ctx):
            # Earlier fixed steps are committed by the real StepExecutor. This
            # tool is currently running when the OS terminates this process.
            checkpoint = {"purpose": "real-process-restart-verification", "fixture": "X200",
                          "runId": ctx.run_id, "retained": "must-survive-worker-termination"}
            artifact_id = engine.repo.add_artifact(ctx.run_id, "restart-checkpoint", checkpoint)
            reservation_id = engine.repo.reserve_budget(ctx.run_id, {"tool_calls": 1},
                                                         operation_key="restart-checkpoint-attempt")
            engine.repo.append_event(ctx.run_id, "verification.process-checkpoint", detail={
                "artifactId": artifact_id, "reservationId": reservation_id, "workerId": owner})
            _write(args.signal, {"ready": True, "runId": ctx.run_id, "poolKey": args.pool,
                               "artifactId": artifact_id, "artifactHash": artifact_json_hash(checkpoint),
                               "reservationId": reservation_id})
            await asyncio.Event().wait()
            return await original(payload, ctx)

        engine.gateway.handlers["uav.query_flight_logs"] = interrupted_tool
    worker = Worker(queue, engine.execute, prepare=engine.recovery.recover, owner_id=owner,
                    lease_seconds=4, heartbeat_seconds=0.5, concurrency=1)

    async def run():
        async def pulse():
            while True:
                operations.record_worker_heartbeat(owner, args.pool, active_runs=len(worker.active_run_ids))
                await asyncio.sleep(0.5)
        pulse_task = asyncio.create_task(pulse())
        try:
            claimed = await worker.run_once()
            if not claimed:
                raise RuntimeError("restart verifier worker did not claim its Run")
            if args.child == "recover":
                final = engine.repo.get_run(args.run_id)
                if final["status"] != "completed" or queue.get(args.run_id)["status"] != "done":
                    raise RuntimeError("restart verifier recovered Run did not complete")
                _write(args.signal, {"completed": True, "runId": args.run_id,
                                     "status": final["status"], "poolKey": args.pool})
        finally:
            pulse_task.cancel()
            await asyncio.gather(pulse_task, return_exceptions=True)
            operations.stop_worker(owner, args.pool)
    asyncio.run(run())


def _query(factory, statement, args=()):
    with factory() as con:
        with con.cursor() as cursor:
            cursor.execute(statement, args)
            return [dict(row) for row in cursor.fetchall()]


def _profile(factory, run_id):
    row = _query(factory, "SELECT profile_json,profile_hash FROM agent_run_profile_snapshot WHERE run_id=%s", (run_id,))[0]
    if artifact_json_hash(row["profile_json"]) != row["profile_hash"]:
        raise RuntimeError("restart-profile-hash-invalid")
    return row


def _budget(engine, run_id):
    view = engine.repo.get_budget(run_id)
    reservations = _query(engine.base_connection_factory,
        "SELECT reservation_id,operation_key,charges_json FROM agent_budget_reservation WHERE run_id=%s ORDER BY reservation_id",
        (run_id,))
    return {"view": view, "reservations": reservations}


def _launch(args, pool, run_id, mode, signal):
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--child", mode, "--pool", pool,
               "--run-id", run_id, "--signal", str(signal), "--database", args.database]
    if args.config:
        command.extend(["--config", str(Path(args.config).resolve())])
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)


def _wait_signal(process, path, *, deadline_seconds=45):
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass  # The writer has not completed this small file yet.
        if process.poll() is not None:
            raise RuntimeError("restart-child-ended-before-signal")
        time.sleep(0.1)
    raise RuntimeError("restart-child-signal-timeout")


def _verify(args):
    report = {"passed": False, "mode": "actual-process-kill-and-restart", "fixtureOnly": True,
              "paidCalls": 0, "liveSourceWrites": 0, "normalServicesStopped": False}
    children = []
    pool = PREFIX + secrets.token_hex(10)
    engine = build_engine(args.config, args.database)
    base = engine.base_connection_factory
    queue = WorkerQueue(base, pool)
    operations = OperationsService(base)
    run_id = None
    try:
        queue.configure_pool(1)
        payload = deepcopy(X200_CAPABILITY_INPUT)
        payload["verificationPurpose"] = "actual worker process restart; fixture only"
        run_id = engine.create_run("uav-capability-analyst", payload)
        # A fixed fixture Run does not call providers. A real persistent budget
        # account proves recovery cannot reset an already charged reservation.
        engine.repo.initialize_budget(run_id, {"tool_calls": 10})
        queue.enqueue(run_id)
        report.update({"runId": run_id, "poolKey": pool, "evidencePreserved": True})
        profile_before = _profile(base, run_id)
        with tempfile.TemporaryDirectory(prefix="agent-engine-restart-") as temporary:
            signal = Path(temporary) / "checkpoint.json"
            crashed = _launch(args, pool, run_id, "crash", signal)
            children.append(crashed)
            checkpoint = _wait_signal(crashed, signal)
            before = queue.get(run_id)
            if before["status"] != "leased" or before["generation"] < 1:
                raise RuntimeError("restart-worker-had-no-active-lease")
            initial_steps = engine.repo.list_steps(run_id)
            succeeded_before = [row["step_key"] for row in initial_steps if row["status"] == "succeeded"]
            if not {"parse-requirement", "query-baseline"}.issubset(succeeded_before):
                raise RuntimeError("restart-checkpoint-did-not-retain-earlier-steps")
            if not any(row["step_key"] == "collect-evidence" and row["status"] == "running" for row in initial_steps):
                raise RuntimeError("restart-tool-was-not-interrupted-during-running-step")
            budget_before = _budget(engine, run_id)
            if budget_before["view"]["used"] != {"tool_calls": 1}:
                raise RuntimeError("restart-checkpoint-budget-was-not-charged")
            old = Lease(run_id, pool, before["owner_id"], before["generation"], before["attempt"])
            crashed.kill()
            crashed.wait(timeout=15)
            if crashed.returncode is None:
                raise RuntimeError("restart-worker-termination-not-confirmed")
            report["terminatedProcess"] = {"pid": crashed.pid, "returnCode": crashed.returncode,
                                           "generation": before["generation"], "leaseUntil": before["lease_until"]}
            # Observe actual elapsed database time. Never forge/shorten a lease.
            deadline = time.monotonic() + 20
            expired = False
            while time.monotonic() < deadline:
                row = _query(base, "SELECT lease_until<=clock_timestamp() AS expired,status,generation FROM agent_run_queue WHERE run_id=%s",
                             (run_id,))[0]
                if row["status"] != "leased" or row["generation"] != old.generation:
                    raise RuntimeError("restart-owned-lease-changed-before-recovery")
                if row["expired"]:
                    expired = True
                    break
                time.sleep(0.2)
            if not expired:
                raise RuntimeError("restart-lease-did-not-expire")
            operations.stop_worker(pool + "-crash", pool)
            recovery_signal = Path(temporary) / "recovered.json"
            recovered = _launch(args, pool, run_id, "recover", recovery_signal)
            children.append(recovered)
            _wait_signal(recovered, recovery_signal)
            recovered.wait(timeout=15)
            if recovered.returncode != 0:
                raise RuntimeError("restart-second-process-failed")
            report["recoveryProcess"] = {"pid": recovered.pid, "returnCode": recovered.returncode}
        final = engine.repo.get_run(run_id)
        final_queue = queue.get(run_id)
        steps = engine.repo.list_steps(run_id)
        events = engine.repo.list_events(run_id)
        artifact = next((item for item in engine.repo.list_artifacts(run_id) if item["artifact_id"] == checkpoint["artifactId"]), None)
        if not artifact or artifact["content_hash"] != checkpoint["artifactHash"] or artifact_json_hash(artifact["content_json"]) != checkpoint["artifactHash"]:
            raise RuntimeError("restart-checkpoint-artifact-changed")
        if _profile(base, run_id) != profile_before:
            raise RuntimeError("restart-frozen-profile-changed")
        budget_after = _budget(engine, run_id)
        if budget_after != budget_before:
            raise RuntimeError("restart-budget-or-reservation-changed")
        if final["status"] != "completed" or final_queue["status"] != "done" or final_queue["generation"] <= old.generation:
            raise RuntimeError("restart-final-state-invalid")
        for key in succeeded_before:
            if sum(row["step_key"] == key for row in steps) != 1:
                raise RuntimeError("restart-replayed-an-already-checkpointed-step")
        retries = [row for row in steps if row["step_key"] == "collect-evidence"]
        if [(row["attempt"], row["status"]) for row in retries] != [(0, "failed"), (1, "succeeded")]:
            raise RuntimeError("restart-interrupted-step-was-not-retried-safely")
        if not any(row["event_type"] == "run.recovered" for row in events):
            raise RuntimeError("restart-recovery-event-missing")
        stale_write_rejected = False
        with worker_context(old):
            try:
                engine.repo.set_output(run_id, {"invalid": "stale-worker-output"})
            except psycopg2.Error as exc:
                stale_write_rejected = exc.pgcode == "55000"
        if not stale_write_rejected or queue.heartbeat(old) or queue.finish(old):
            raise RuntimeError("restart-stale-lease-was-not-fenced")
        model_calls = len(engine.repo.list_model_calls(run_id))
        if model_calls:
            raise RuntimeError("restart-fixture-unexpected-model-calls")
        report.update({"passed": True, "status": final["status"], "queueStatus": final_queue["status"],
                       "generationBefore": old.generation, "generationAfter": final_queue["generation"],
                       "leaseExpiredByDatabaseClock": True, "checkpointedStepsNotReplayed": succeeded_before,
                       "interruptedStepAttempts": [{"attempt": row["attempt"], "status": row["status"]} for row in retries],
                       "profileHash": profile_before["profile_hash"], "profileUnchanged": True,
                       "checkpointArtifactId": artifact["artifact_id"], "checkpointArtifactHash": artifact["content_hash"],
                       "artifactUnchanged": True, "budgetBefore": budget_before, "budgetAfter": budget_after,
                       "budgetUnchanged": True, "staleLeaseWriteRejected": True, "modelCalls": model_calls,
                       "toolCalls": len(engine.repo.list_tool_calls(run_id)), "recoveredEvent": True,
                       "outputHash": artifact_json_hash(final["output_json"])})
    except Exception as exc:
        # Driver/provider exceptions may include credentials. Report type only.
        report["failureType"] = type(exc).__name__
        raise RuntimeError("restart verification failed; inspect owned Run and report") from None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=15)
        for suffix in ("-crash", "-recovered"):
            try:
                operations.stop_worker(pool + suffix, pool)
            except Exception:
                report["workerMetadataCleanupFailed"] = True
        report["helperProcessesStopped"] = all(child.poll() is not None for child in children)
        report["temporarySignalsRemoved"] = True
        output = (ROOT / args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        _write(output, report)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--database", default="ontology_agent_admin")
    parser.add_argument("--output", default="docs/verification/phase4/restart-recovery.json")
    parser.add_argument("--child", choices=("crash", "recover"), help=argparse.SUPPRESS)
    parser.add_argument("--pool", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    parser.add_argument("--signal", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        _child(args)
    else:
        _verify(args)


if __name__ == "__main__":
    main()
