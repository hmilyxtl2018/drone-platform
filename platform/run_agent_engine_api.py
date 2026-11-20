"""Local PostgreSQL Admin API; start the durable worker separately."""
import argparse
import asyncio
from pathlib import Path

from agent_engine.bootstrap import build_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--database", default="ontology_agent_admin",
                        help="PostgreSQL database; use an isolated verification database for browser acceptance")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--auth-mode", choices=("local", "token"), default="local",
                        help="Local console opens directly; token mode requires an explicit login")
    parser.add_argument("--stop-file", help="Local service-manager stop marker")
    args = parser.parse_args()
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(
        build_app(args.config, args.database, local_access=args.auth_mode == "local"),
        host="127.0.0.1", port=args.port, log_level="warning", proxy_headers=False))
    async def run():
        async def stop_watch():
            while not server.should_exit:
                if args.stop_file and Path(args.stop_file).exists():
                    server.should_exit = True
                    break
                await asyncio.sleep(0.5)
        watch = asyncio.create_task(stop_watch())
        try:
            await server.serve()
        finally:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
    asyncio.run(run())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("agent-api-failed; check database and local port availability") from None
