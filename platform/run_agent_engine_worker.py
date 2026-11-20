"""Run a durable local worker (migration 033 must already be installed)."""
from __future__ import annotations

import argparse
import asyncio
import signal
from contextlib import suppress
from pathlib import Path

from agent_engine.bootstrap import build_engine
from agent_engine.postgres import connection_factory
from agent_engine.worker import Worker, WorkerQueue, fenced_connection_factory
from agent_engine.execution_service import ExecutionService
from agent_engine.operations import OperationsService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="PostgreSQL config path; credentials never logged")
    parser.add_argument("--database", default="ontology_agent_admin")
    parser.add_argument("--pool", default="default")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--lease-seconds", type=float, default=30)
    parser.add_argument("--heartbeat-seconds", type=float, default=5)
    parser.add_argument("--once", action="store_true", help="Claim at most one task and exit")
    parser.add_argument("--stop-file", help="Local service-manager stop marker")
    args = parser.parse_args()
    base = connection_factory(args.config, args.database)
    queue = WorkerQueue(base, args.pool)
    queue.configure_pool(args.concurrency)
    engine = build_engine(args.config, args.database)
    # All engine components share this repository; replace its factory before
    # execute/recovery is called. A new repository must receive the same wrapper.
    engine.repo.connection_factory = fenced_connection_factory(base)
    execution = ExecutionService(engine, queue)
    execution.reconcile()
    worker = Worker(queue, execution.execute, prepare=engine.recovery.recover,
                    concurrency=args.concurrency, lease_seconds=args.lease_seconds,
                    heartbeat_seconds=args.heartbeat_seconds)
    operations = OperationsService(base)
    from agent_engine.evolution_controller import EvolutionController
    evolution = EvolutionController(engine,queue)
    from agent_engine.mission_controller import MissionController
    autopilot = MissionController(engine, queue)

    async def run():
        loop = asyncio.get_running_loop()
        def stop(*_):
            loop.call_soon_threadsafe(worker.stop)
        signal.signal(signal.SIGINT, stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop)
        async def pulse():
            scans = 0
            while not worker._stopped.is_set():
                if args.stop_file and Path(args.stop_file).exists():
                    worker.stop()
                    break
                await asyncio.to_thread(operations.record_worker_heartbeat, worker.owner_id, args.pool,
                                        active_runs=len(worker.active_run_ids))
                if scans % 3 == 0:
                    await asyncio.to_thread(operations.scan_alerts, pool_key=args.pool)
                await asyncio.to_thread(evolution.tick)
                await asyncio.to_thread(autopilot.tick)
                scans += 1
                await asyncio.sleep(min(args.heartbeat_seconds, 5))
        await asyncio.to_thread(operations.record_worker_heartbeat, worker.owner_id, args.pool)
        heartbeat = asyncio.create_task(pulse())
        work = asyncio.create_task(worker.run_once() if args.once else worker.run_forever())
        try:
            done, _ = await asyncio.wait((work, heartbeat), return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                # A failed status writer must not leave a worker silently alive.
                heartbeat.result()
                worker.stop()
            await work
        finally:
            heartbeat.cancel()
            work.cancel()
            await asyncio.gather(heartbeat, work, return_exceptions=True)
            with suppress(Exception):
                await asyncio.to_thread(operations.stop_worker, worker.owner_id, args.pool)
    asyncio.run(run())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("agent-worker-failed; check database and operations status") from None
