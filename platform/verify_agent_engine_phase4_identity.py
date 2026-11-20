"""Verify the local identity UI with temporary fixtures and no provider calls.

The bootstrap token is passed only through a pipe. Exact fixture identities are
disabled and their tokens revoked on exit; immutable audit history is retained.
"""
from pathlib import Path
import os
import secrets
import subprocess

from agent_admin.credentials import AdminAuth
from agent_engine.postgres import connection_factory


def main():
    factory = connection_factory()
    fixture_name = 'P4 automated identity UI ' + secrets.token_hex(8)
    issued = AdminAuth(factory).issue_token('P4 automated UI administrator', ['admin'], 1)
    try:
        node = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        result = subprocess.run([str(node), 'platform/verify_agent_engine_phase4_identity.mjs'],
                                input=issued['token'], text=True, check=False,
                                env={**os.environ, 'AGENT_IDENTITY_PROBE_NAME': fixture_name})
        return result.returncode
    finally:
        with factory() as con:
            with con.cursor() as cursor:
                cursor.execute('SELECT principal_id FROM agent_admin_identity WHERE principal_id=%s OR display_name=%s',
                               (issued['principalId'], fixture_name))
                fixture_ids = [row['principal_id'] for row in cursor.fetchall()]
                cursor.execute('UPDATE agent_admin_token SET revoked_at=coalesce(revoked_at,now()) WHERE principal_id=ANY(%s)',
                               (fixture_ids,))
                cursor.execute('UPDATE agent_admin_identity SET enabled=false,revision=revision+1 WHERE principal_id=ANY(%s)',
                               (fixture_ids,))


if __name__ == '__main__':
    raise SystemExit(main())
