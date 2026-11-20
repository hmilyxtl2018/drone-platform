"""Local health and immutable, deduplicated operational observations.

Only server-owned metadata is read. This module performs no provider calls,
does not inspect connection strings, and does not release uncertain budgets or
change queue ownership. Recovery remains the fenced worker's responsibility.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

from psycopg2.extras import Json

from .repository import new_id


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")
_TERMINAL = {"completed", "completed-with-gaps", "requires-human-review", "blocked", "failed", "cancelled"}
_COST = {"status": "unknown", "amount": None, "currency": None,
         "reason": "price-table-unconfigured"}


def _identifier(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or not _IDENTIFIER.fullmatch(value):
        raise ValueError("invalid " + name)
    return value


def _seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("worker freshness must be positive finite seconds")
    return float(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _observation(kind, severity, subject, reason, details):
    fingerprint = _hash({"reason": reason, "details": details})
    return {"alert_kind": kind, "severity": severity, "subject_key": subject, "reason_code": reason,
            "details_json": details, "fingerprint": fingerprint,
            "deduplication_key": _hash({"kind": kind, "subject": subject, "fingerprint": fingerprint})}


class OperationsService:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def record_worker_heartbeat(self, worker_id: str, pool_key: str = "default", *,
                                status: str = "running", active_runs: int = 0) -> dict:
        worker_id = _identifier(worker_id, "worker id", 160)
        pool_key = _identifier(pool_key, "worker pool", 120)
        if status not in {"running", "stopped"}:
            raise ValueError("worker status must be running or stopped")
        if type(active_runs) is not int or not 0 <= active_runs <= 128 or (status == "stopped" and active_runs):
            raise ValueError("invalid active worker run count")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO agent_worker_status(worker_id,pool_key,status,active_runs,stopped_at) "
                    "VALUES(%s,%s,%s,%s,CASE WHEN %s='stopped' THEN clock_timestamp() ELSE NULL END) "
                    "ON CONFLICT(pool_key,worker_id) DO UPDATE SET status=EXCLUDED.status,"
                    "active_runs=EXCLUDED.active_runs,heartbeat_at=clock_timestamp(),"
                    "started_at=CASE WHEN agent_worker_status.status='stopped' AND EXCLUDED.status='running' "
                    "THEN clock_timestamp() ELSE agent_worker_status.started_at END,"
                    "stopped_at=CASE WHEN EXCLUDED.status='stopped' THEN clock_timestamp() ELSE NULL END RETURNING *",
                    (worker_id, pool_key, status, active_runs, status))
                return dict(cursor.fetchone())

    def stop_worker(self, worker_id: str, pool_key: str = "default") -> dict:
        return self.record_worker_heartbeat(worker_id, pool_key, status="stopped")

    def _snapshot(self, cursor, pool_key, stale_seconds):
        cursor.execute("SELECT clock_timestamp() AS checked_at")
        checked = cursor.fetchone()["checked_at"]
        cursor.execute("SELECT pool_key,max_concurrency FROM agent_worker_pool "
                       "WHERE (%s::text IS NULL OR pool_key=%s) ORDER BY pool_key", (pool_key, pool_key))
        pools = [dict(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM agent_worker_status WHERE (%s::text IS NULL OR pool_key=%s) "
                       "ORDER BY pool_key,worker_id", (pool_key, pool_key))
        workers, alerts = [], []
        for row in cursor.fetchall():
            worker = dict(row)
            age = max(0.0, (checked - worker["heartbeat_at"]).total_seconds())
            fresh = worker["status"] == "running" and age <= stale_seconds
            worker["fresh"] = fresh
            worker["age_seconds"] = age
            workers.append(worker)
            if worker["status"] == "running" and not fresh:
                alerts.append(_observation("worker-stale", "warning", worker["pool_key"] + ":" + worker["worker_id"],
                    "worker-heartbeat-expired", {"poolKey": worker["pool_key"], "workerId": worker["worker_id"],
                    "lastHeartbeatAt": _time(worker["heartbeat_at"])}))
        for pool in pools:
            registered = [row for row in workers if row["pool_key"] == pool["pool_key"]]
            pool["fresh_workers"] = sum(row["fresh"] for row in registered)
            if not pool["fresh_workers"]:
                state = [{"worker": row["worker_id"], "status": row["status"],
                          "heartbeat": _time(row["heartbeat_at"])} for row in registered]
                alerts.append(_observation("worker-unavailable", "error", pool["pool_key"], "no-fresh-worker",
                    {"poolKey": pool["pool_key"], "workerStateHash": _hash(state)}))
        cursor.execute("SELECT q.* FROM agent_run_queue q WHERE (%s::text IS NULL OR q.pool_key=%s) "
                       "ORDER BY q.run_id", (pool_key, pool_key))
        queue_counts = {}
        for row in cursor.fetchall():
            queue_counts[row["status"]] = queue_counts.get(row["status"], 0) + 1
            detail = {"poolKey": row["pool_key"], "runId": row["run_id"],
                      "generation": row["generation"], "attempt": row["attempt"]}
            if row["status"] == "failed":
                # Raw queue errors are never returned, even if a different
                # database client bypassed WorkerQueue's machine-code validation.
                detail["errorFingerprint"] = _hash(row.get("last_error_code"))
                alerts.append(_observation("queue-failed", "error", row["run_id"],
                                           "queue-execution-failed", detail))
            elif row["status"] == "leased" and row["lease_until"] <= checked:
                detail["leaseUntil"] = _time(row["lease_until"])
                alerts.append(_observation("lease-expired", "error", row["run_id"],
                                           "lease-expired-before-finish", detail))
        # A failed root is terminal, but its queued/leased/paused children can
        # still need shared budget. Preserve that distinction without changing
        # either the queue or the charged usage.
        # A finished root may still own queued/paused children. Its status alone
        # must not suppress an exhausted shared budget for those children.
        cursor.execute("SELECT b.root_run_id,b.limits_json,b.used_json,r.status,"
                       "EXISTS(SELECT 1 FROM agent_run_budget_member m JOIN agent_run_queue q USING(run_id) "
                       "WHERE m.root_run_id=b.root_run_id AND q.status IN ('queued','leased','paused')) AS outstanding "
                       "FROM agent_run_budget b JOIN agent_run r ON r.run_id=b.root_run_id "
                       "WHERE (%s::text IS NULL OR EXISTS(SELECT 1 FROM agent_run_budget_member m "
                       "JOIN agent_run_queue q USING(run_id) WHERE m.root_run_id=b.root_run_id AND q.pool_key=%s)) "
                       "ORDER BY b.root_run_id", (pool_key, pool_key))
        budget_roots = exhausted_roots = overrun_roots = 0
        for row in cursor.fetchall():
            budget_roots += 1
            exhausted = overrun = False
            for resource, used in sorted(row["used_json"].items()):
                limit = row["limits_json"].get(resource, 0)
                detail = {"runId": row["root_run_id"], "resource": resource, "used": used, "limit": limit}
                if used > limit:
                    overrun = True
                    alerts.append(_observation("budget-overrun", "error", row["root_run_id"] + ":" + resource,
                                               "actual-usage-over-budget", detail))
                # A disabled, unused resource (0/0) is not an exhausted one.
                # Zero is an unavailable resource, not consumed allowance.
                elif used > 0 and used == limit and (row["outstanding"] or row["status"] not in _TERMINAL):
                    exhausted = True
                    alerts.append(_observation("budget-exhausted", "warning", row["root_run_id"] + ":" + resource,
                                               "shared-budget-exhausted", detail))
            exhausted_roots += exhausted
            overrun_roots += overrun
        return {"status": "degraded" if alerts or not pools else "healthy", "checkedAt": _time(checked),
                "database": {"status": "healthy"},
                "workers": {"status": "healthy" if pools and all(row["fresh_workers"] for row in pools) else "unavailable",
                            "staleAfterSeconds": stale_seconds, "pools": pools, "instances": workers},
                "queue": {"counts": queue_counts},
                "budgets": {"roots": budget_roots, "exhaustedRoots": exhausted_roots, "overrunRoots": overrun_roots,
                            "accounting": "reserved-and-settled; uncertain calls remain charged"},
                "cost": dict(_COST), "alerts": alerts}

    @staticmethod
    def _persist(cursor, observations):
        result = []
        for row in observations:
            cursor.execute("INSERT INTO agent_operations_alert(alert_id,alert_kind,severity,subject_key,reason_code,"
                           "details_json,fingerprint,deduplication_key) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
                           "ON CONFLICT(deduplication_key) DO NOTHING RETURNING *",
                           (new_id("ops-alert"), row["alert_kind"], row["severity"], row["subject_key"], row["reason_code"],
                            Json(row["details_json"]), row["fingerprint"], row["deduplication_key"]))
            saved = cursor.fetchone()
            if not saved:
                cursor.execute("SELECT * FROM agent_operations_alert WHERE deduplication_key=%s", (row["deduplication_key"],))
                saved = cursor.fetchone()
            result.append(dict(saved))
        return result

    def health(self, *, pool_key: str | None = None, worker_stale_seconds: float = 30,
               persist_alerts: bool = False) -> dict:
        if pool_key is not None:
            _identifier(pool_key, "worker pool", 120)
        stale_seconds = _seconds(worker_stale_seconds)
        if type(persist_alerts) is not bool:
            raise ValueError("persist_alerts must be boolean")
        connected = False
        try:
            with self.connection_factory() as con:
                connected = True
                with con.cursor() as cursor:
                    result = self._snapshot(cursor, pool_key, stale_seconds)
                    if persist_alerts:
                        result["alerts"] = self._persist(cursor, result["alerts"])
                    return result
        except Exception:
            # Never serialize an exception: drivers may include DSNs, passwords,
            # server errors or source data. A failed probe is not a clean state.
            return {"status": "unavailable", "database": {"status": "error",
                    "reason": "operations-query-failed" if connected else "database-unavailable"},
                    "workers": {"status": "unknown"}, "queue": {"status": "unknown"},
                    "budgets": {"status": "unknown"}, "cost": dict(_COST), "alerts": []}

    def scan_alerts(self, *, pool_key: str | None = None, worker_stale_seconds: float = 30) -> list[dict]:
        result = self.health(pool_key=pool_key, worker_stale_seconds=worker_stale_seconds, persist_alerts=True)
        if result["status"] == "unavailable":
            raise RuntimeError(result["database"]["reason"])
        return result["alerts"]

    def list_alerts(self, *, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("alert limit must be between 1 and 1000")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_operations_alert ORDER BY observed_at DESC,alert_id LIMIT %s", (limit,))
                return [dict(row) for row in cursor.fetchall()]
