"""Create a short-lived test identity and run the live UI acceptance driver.

The issued token stays in a process pipe, is never printed, and is revoked on
exit. Automated request-evidence is not a human approval of source truth.
"""
from pathlib import Path
import subprocess
from agent_admin.credentials import AdminAuth
from agent_engine.postgres import connection_factory


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--followup-ui', action='store_true', help='worker must be stopped; create and cancel an unexecuted follow-up')
    args = parser.parse_args()
    auth = AdminAuth(connection_factory())
    issued = auth.issue_token("POC automated acceptance (test only)", ["operator", "reviewer"], 1)
    try:
        node = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        driver = 'platform/verify_agent_engine_followup_ui.mjs' if args.followup_ui else 'platform/verify_agent_engine_browser.mjs'
        result = subprocess.run([str(node), driver],
                                input=issued["token"], text=True, check=False)
        return result.returncode
    finally:
        with connection_factory()() as con:
            with con.cursor() as c:
                c.execute("UPDATE agent_admin_token SET revoked_at=now() WHERE principal_id=%s", (issued["principalId"],))
                c.execute("UPDATE agent_admin_identity SET enabled=false WHERE principal_id=%s", (issued["principalId"],))


if __name__ == "__main__":
    raise SystemExit(main())
