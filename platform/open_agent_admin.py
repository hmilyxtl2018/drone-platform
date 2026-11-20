"""Issue a local operator credential to the Windows clipboard and open Admin.

Run this interactively as the person reviewing the POC. The bearer token is
never printed, put in a URL, saved in a file or passed on a command line.
"""
import argparse
import os
import subprocess
import webbrowser

from agent_admin.credentials import AdminAuth
from agent_engine.postgres import connection_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default=os.environ.get('USERNAME', 'Local reviewer'))
    parser.add_argument('--run-id')
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('This local clipboard launcher requires Windows.')
    issued = AdminAuth(connection_factory()).issue_token(args.name, ['admin', 'operator', 'reviewer'], 1)
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 'Set-Clipboard -Value ([Console]::In.ReadToEnd())'],
                                input=issued['token'], text=True, capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Unable to copy the local access token to the clipboard.')
        from urllib.parse import quote
        url = 'http://127.0.0.1:8090/agent-admin.html'
        if args.run_id:
            url += '#run=' + quote(args.run_id, safe='')
        webbrowser.open(url)
        print('Local login token copied. Paste it into the console login field; it expires in one day.')
    except BaseException:
        with connection_factory()() as con:
            with con.cursor() as c:
                c.execute('UPDATE agent_admin_token SET revoked_at=now() WHERE principal_id=%s', (issued['principalId'],))
                c.execute('UPDATE agent_admin_identity SET enabled=false WHERE principal_id=%s', (issued['principalId'],))
        raise


if __name__ == '__main__':
    main()
