"""Run the real engineering browser verification with a temporary local identity."""
from pathlib import Path
import subprocess

from agent_admin.credentials import AdminAuth
from agent_engine.postgres import connection_factory


def main():
    auth = AdminAuth(connection_factory())
    issued = auth.issue_token("Phase 4 engineering browser (temporary test identity)", ["admin", "operator", "reviewer"], 1)
    try:
        node = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        result = subprocess.run(
            [str(node), "platform/verify_agent_engine_phase4_engineering_browser.mjs"],
            input=issued["token"], text=True, check=False,
        )
        return result.returncode
    finally:
        with connection_factory()() as con:
            with con.cursor() as cursor:
                cursor.execute("UPDATE agent_admin_token SET revoked_at=coalesce(revoked_at,now()) WHERE principal_id=%s", (issued["principalId"],))
                cursor.execute("UPDATE agent_admin_identity SET enabled=false WHERE principal_id=%s", (issued["principalId"],))


if __name__ == "__main__":
    raise SystemExit(main())
