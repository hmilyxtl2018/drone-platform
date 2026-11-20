"""Durable worker queue, renewable leases, and runtime-write fencing.

Integration contract:
* Construct the engine using ``fenced_connection_factory(base_factory)``. Every
  repository connection used by execute/recovery/model/tools must use it.
* Construct WorkerQueue using the base (unwrapped) factory. Queue ownership is
  controlled here, independently of the executing run context.
* Pass engine.execute to Worker, optionally engine.recovery.recover as prepare.
* API pause/cancel: revoke via queue.request_pause/request_cancel FIRST, then
  call engine.pause/cancel in control_context(run_id). Resume the queue only
  after approvals permit continuation. Those are trusted authenticated controls.
* API writes to managed runs (approval decisions etc.) need control_context.
* New run-owned tables must attach SQL agent_worker_fence_guard; queue tokens
  without guarding output/state/artifacts/budget do not provide fencing.

Queue limits apply across processes within one configured pool. They do not
replace per-Run tool or provider budgets. An uncertain in-flight request keeps
its budget reservation if lease loss prevents recording late usage.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
import math
import re
import secrets
import threading
from typing import Any, Awaitable, Callable


_CURRENT_LEASE: ContextVar["Lease | None"] = ContextVar("agent_worker_lease", default=None)
_CONTROL_RUN: ContextVar[str | None] = ContextVar("agent_worker_control_run", default=None)


class LeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    run_id: str
    pool_key: str
    owner_id: str
    generation: int
    attempt: int


@contextmanager
def worker_context(lease: Lease):
    token = _CURRENT_LEASE.set(lease)
    try:
        yield
    finally:
        _CURRENT_LEASE.reset(token)


@contextmanager
def control_context(run_id: str):
    """Trusted service controls only; never accept arbitrary client context."""
    token = _CONTROL_RUN.set(run_id)
    try:
        yield
    finally:
        _CONTROL_RUN.reset(token)


def fenced_connection_factory(base_factory):
    """Apply transaction-local fencing settings on each repository connection.

    Must wrap a fresh-connection factory. Connection pooling needs the same
    LOCAL settings reapplied per transaction; setting them once is insufficient.
    """
    def connect():
        con = base_factory()
        lease, control = _CURRENT_LEASE.get(), _CONTROL_RUN.get()
        try:
            with con.cursor() as cursor:
                for key, value in (
                    ("agent_engine.worker_run_id", lease.run_id if lease else ""),
                    ("agent_engine.worker_owner", lease.owner_id if lease else ""),
                    ("agent_engine.worker_generation", str(lease.generation) if lease else ""),
                    ("agent_engine.control_run_id", control or ""),
                ):
                    cursor.execute("SELECT set_config(%s,%s,true)", (key, value))
            return con
        except BaseException:
            con.close()
            raise
    return connect


def _positive_seconds(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be positive finite seconds")
    return float(value)


class WorkerQueue:
    def __init__(self, connection_factory, pool_key="default"):
        self.connection_factory = connection_factory
        if not isinstance(pool_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", pool_key):
            raise ValueError("invalid worker pool key")
        self.pool_key = pool_key

    def configure_pool(self, max_concurrency=2):
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or not 1 <= max_concurrency <= 128:
            raise ValueError("worker concurrency must be between 1 and 128")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("INSERT INTO agent_worker_pool(pool_key,max_concurrency) VALUES(%s,%s) "
                               "ON CONFLICT(pool_key) DO NOTHING", (self.pool_key, max_concurrency))
                cursor.execute("SELECT max_concurrency FROM agent_worker_pool WHERE pool_key=%s", (self.pool_key,))
                if cursor.fetchone()["max_concurrency"] != max_concurrency:
                    raise ValueError("pool concurrency differs from persisted configuration")

    def enqueue(self, run_id: str, priority: int = 0):
        if isinstance(priority, bool) or not isinstance(priority, int) or not -(2**31) <= priority < 2**31:
            raise ValueError("invalid queue priority")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT status FROM agent_run WHERE run_id=%s", (run_id,))
                run = cursor.fetchone()
                if not run:
                    raise ValueError("run not found")
                if run["status"] in {"completed", "completed-with-gaps", "requires-human-review", "blocked", "cancelled"}:
                    raise ValueError("cannot enqueue a terminal run")
                cursor.execute("INSERT INTO agent_run_queue(run_id,pool_key,priority) VALUES(%s,%s,%s) "
                               "ON CONFLICT(run_id) DO NOTHING", (run_id, self.pool_key, priority))
                cursor.execute("SELECT * FROM agent_run_queue WHERE run_id=%s", (run_id,))
                row = dict(cursor.fetchone())
                if row["pool_key"] != self.pool_key:
                    raise ValueError("run already belongs to a different worker pool")
                return row

    def get(self, run_id):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_run_queue WHERE run_id=%s", (run_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

    def claim(self, owner_id: str, lease_seconds: float = 30) -> Lease | None:
        seconds = _positive_seconds(lease_seconds, "lease")
        if not isinstance(owner_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", owner_id):
            raise ValueError("invalid worker owner")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                # Pool-row lock serializes only admission, never task execution.
                # Without this lock, concurrent claim transactions can both see
                # a free capacity slot and exceed the configured process limit.
                cursor.execute("SELECT max_concurrency FROM agent_worker_pool WHERE pool_key=%s FOR UPDATE",
                               (self.pool_key,))
                pool = cursor.fetchone()
                if not pool:
                    raise ValueError("worker pool is not configured")
                cursor.execute("SELECT count(*) AS active FROM agent_run_queue WHERE pool_key=%s "
                               "AND status='leased' AND lease_until>clock_timestamp()", (self.pool_key,))
                if cursor.fetchone()["active"] >= pool["max_concurrency"]:
                    return None
                cursor.execute("SELECT run_id,generation,attempt FROM agent_run_queue WHERE pool_key=%s "
                               "AND ((status='queued' AND available_at<=clock_timestamp()) "
                               "OR (status='leased' AND lease_until<=clock_timestamp())) "
                               "ORDER BY priority DESC,created_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1",
                               (self.pool_key,))
                row = cursor.fetchone()
                if not row:
                    return None
                generation, attempt = row["generation"]+1, row["attempt"]+1
                cursor.execute("UPDATE agent_run_queue SET status='leased',owner_id=%s,generation=%s,attempt=%s,"
                               "lease_until=clock_timestamp()+(%s*interval '1 second'),heartbeat_at=clock_timestamp(),"
                               "updated_at=now() WHERE run_id=%s",
                               (owner_id, generation, attempt, seconds, row["run_id"]))
                return Lease(row["run_id"], self.pool_key, owner_id, generation, attempt)

    def heartbeat(self, lease: Lease, lease_seconds: float = 30) -> bool:
        seconds = _positive_seconds(lease_seconds, "lease")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("UPDATE agent_run_queue SET lease_until=clock_timestamp()+(%s*interval '1 second'),"
                               "heartbeat_at=clock_timestamp(),updated_at=now() WHERE run_id=%s AND pool_key=%s "
                               "AND status='leased' AND owner_id=%s AND generation=%s AND lease_until>clock_timestamp()",
                               (seconds, lease.run_id, lease.pool_key, lease.owner_id, lease.generation))
                return cursor.rowcount == 1

    def finish(self, lease: Lease, status="done", error_code: str | None = None,
               retry_delay: float = 0) -> bool:
        if status not in {"done", "failed", "paused", "cancelled", "queued"}:
            raise ValueError("invalid queue completion status")
        if error_code is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", error_code):
            raise ValueError("queue errors must be codes, not raw provider messages")
        if not isinstance(retry_delay, (int, float)) or not math.isfinite(retry_delay) or retry_delay < 0:
            raise ValueError("invalid retry delay")
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("UPDATE agent_run_queue SET status=%s,owner_id=NULL,lease_until=NULL,"
                               "last_error_code=%s,available_at=clock_timestamp()+(%s*interval '1 second'),updated_at=now() "
                               "WHERE run_id=%s AND pool_key=%s AND status='leased' AND owner_id=%s AND generation=%s "
                               "AND lease_until>clock_timestamp()",
                               (status, error_code, retry_delay, lease.run_id, lease.pool_key,
                                lease.owner_id, lease.generation))
                return cursor.rowcount == 1

    def _control(self, run_id, status):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("UPDATE agent_run_queue SET status=%s,generation=generation+1,owner_id=NULL,"
                               "lease_until=NULL,updated_at=now() WHERE run_id=%s AND pool_key=%s "
                               "AND status IN ('queued','leased','paused') RETURNING run_id",
                               (status, run_id, self.pool_key))
                if not cursor.fetchone():
                    raise ValueError("queue run is missing or already finished")
        return self.get(run_id)

    def request_pause(self, run_id):
        return self._control(run_id, "paused")

    def request_cancel(self, run_id):
        return self._control(run_id, "cancelled")

    def resume(self, run_id):
        with self.connection_factory() as con:
            with con.cursor() as cursor:
                cursor.execute("UPDATE agent_run_queue SET status='queued',available_at=clock_timestamp(),"
                               "last_error_code=NULL,updated_at=now() WHERE run_id=%s AND pool_key=%s "
                               "AND status IN ('paused','failed') RETURNING run_id", (run_id, self.pool_key))
                if not cursor.fetchone():
                    raise ValueError("queue run is not paused or failed")
        return self.get(run_id)


class Worker:
    def __init__(self, queue: WorkerQueue, execute: Callable[[str], Awaitable[dict]], *,
                 prepare: Callable[[str], Any] | None = None, owner_id: str | None = None,
                 lease_seconds: float = 30, heartbeat_seconds: float = 5,
                 concurrency: int = 2, poll_seconds: float = 0.5):
        self.queue, self.execute, self.prepare = queue, execute, prepare
        self.owner_id = owner_id or "worker-"+secrets.token_hex(12)
        self.lease_seconds = _positive_seconds(lease_seconds, "lease")
        self.heartbeat_seconds = _positive_seconds(heartbeat_seconds, "heartbeat")
        if self.heartbeat_seconds >= self.lease_seconds / 2:
            raise ValueError("heartbeat interval must be less than half the lease")
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 128:
            raise ValueError("invalid local worker concurrency")
        self.concurrency = concurrency
        self.poll_seconds = _positive_seconds(poll_seconds, "poll")
        self._stopped = asyncio.Event()
        self.active_run_ids: set[str] = set()

    def stop(self):
        self._stopped.set()

    async def _heartbeat(self, lease, stopped):
        # Synchronous source parsing/DB work must not starve lease renewal on
        # the execution event loop. This thread owns no runtime-write authority.
        def renew():
            while not stopped.wait(self.heartbeat_seconds):
                if not self.queue.heartbeat(lease,self.lease_seconds):
                    raise LeaseLostError('worker-lease-lost')
        await asyncio.to_thread(renew)

    async def run_lease(self, lease: Lease):
        self.active_run_ids.add(lease.run_id)
        async def execute():
            with worker_context(lease):
                if self.prepare:
                    await asyncio.to_thread(self.prepare, lease.run_id)
                return await self.execute(lease.run_id)

        heartbeat_stop = threading.Event()
        heartbeat = asyncio.create_task(self._heartbeat(lease, heartbeat_stop))
        execution = asyncio.create_task(execute())
        try:
            done, _ = await asyncio.wait((execution, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                # Revoke stale in-flight Python work. SQL triggers reject late
                # writes even when cancellation cannot stop an external request.
                execution.cancel()
                with suppress(BaseException):
                    await execution
                heartbeat.result()
            result = await execution
            status = result.get("status") if isinstance(result, dict) else None
            target = "paused" if status == "waiting-human" else "cancelled" if status == "cancelled" else \
                     "failed" if status == "failed" else "done"
            await asyncio.to_thread(self.queue.finish, lease, target)
            return result
        except LeaseLostError:
            return {"runId": lease.run_id, "status": "lease-lost"}
        except asyncio.CancelledError:
            execution.cancel()
            with suppress(BaseException):
                await execution
            # Lease expires after shutdown; no false success or fresh budget.
            raise
        except Exception as error:
            # An old process may still hold the Engine advisory execution lock
            # while cancellation unwinds. Requeue with bounded delay rather than
            # double-running; all other errors retain failed state for inspection.
            busy = isinstance(error, RuntimeError) and str(error) == "run already has an active worker"
            await asyncio.to_thread(self.queue.finish, lease, "queued" if busy else "failed",
                                    "execution-lock-busy" if busy else "worker-execution-failed", 1 if busy else 0)
            return {"runId": lease.run_id, "status": "queued" if busy else "failed",
                    "errorCode": "execution-lock-busy" if busy else "worker-execution-failed"}
        finally:
            self.active_run_ids.discard(lease.run_id)
            heartbeat_stop.set()
            heartbeat.cancel()
            with suppress(BaseException):
                await heartbeat

    async def run_once(self) -> bool:
        lease = await asyncio.to_thread(self.queue.claim, self.owner_id, self.lease_seconds)
        if not lease:
            return False
        await self.run_lease(lease)
        return True

    async def run_forever(self):
        tasks: set[asyncio.Task] = set()
        try:
            while not self._stopped.is_set():
                tasks = {task for task in tasks if not task.done()}
                while len(tasks) < self.concurrency and not self._stopped.is_set():
                    lease = await asyncio.to_thread(self.queue.claim, self.owner_id, self.lease_seconds)
                    if not lease:
                        break
                    task = asyncio.create_task(self.run_lease(lease))
                    tasks.add(task)
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
