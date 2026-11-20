"""Loopback-only console access without a browser-managed login credential."""
from datetime import datetime, timedelta, timezone
import asyncio
import logging
import ipaddress
import secrets
from threading import Lock

from psycopg2.extras import Json
from starlette.responses import JSONResponse

from .credentials import ROLES, token_digest


class LocalOperatorCredential:
    """Keep service-side authorization compatible with transactional audits."""

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory
        self._lock = Lock()
        self._token = None
        self._expires_at = None
        self._refresh_task = None

    def cached_token(self):
        """Request path: use a prepared identity, never create/rotate one."""
        with self._lock:
            if self._token and datetime.now(timezone.utc) < self._expires_at:
                return self._token
        raise RuntimeError('local identity is not ready')

    async def start(self):
        from starlette.concurrency import run_in_threadpool
        try:
            await run_in_threadpool(self.token)
        except Exception:
            logging.getLogger(__name__).warning('local identity preparation unavailable')
        async def refresh():
            while True:
                await asyncio.sleep(30)
                try:
                    await run_in_threadpool(self.token)
                except Exception:
                    logging.getLogger(__name__).warning('local identity refresh unavailable')
        self._refresh_task = asyncio.create_task(refresh())

    async def stop(self):
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    def token(self):
        with self._lock:
            now = datetime.now(timezone.utc)
            if self._token and now < self._expires_at - timedelta(hours=1):
                return self._token
            token = "aat_" + secrets.token_urlsafe(40)
            expires_at = now + timedelta(days=1)
            with self.connection_factory() as con:
                with con.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO agent_admin_identity(principal_id,display_name,roles_json,kind) "
                        "VALUES(%s,%s,%s,'human') ON CONFLICT(principal_id) DO NOTHING",
                        ("human-local-desktop", "Local operator", Json(sorted(ROLES))),
                    )
                    cursor.execute(
                        "SELECT enabled,kind,roles_json FROM agent_admin_identity WHERE principal_id=%s",
                        ("human-local-desktop",),
                    )
                    identity = cursor.fetchone()
                    if not identity or not identity['enabled'] or identity['kind'] != 'human' or 'admin' not in identity['roles_json']:
                        raise PermissionError("local operator is disabled or lacks administrator access")
                    cursor.execute(
                        "INSERT INTO agent_admin_token(token_hash,principal_id,expires_at) VALUES(%s,%s,%s)",
                        (token_digest(token), "human-local-desktop", expires_at),
                    )
                    if self._token:
                        cursor.execute("UPDATE agent_admin_token SET revoked_at=now() WHERE token_hash=%s",
                                       (token_digest(self._token),))
            self._token, self._expires_at = token, expires_at
            return token


class LocalAccessMiddleware:
    def __init__(self, app, credential):
        self.app, self.credential = app, credential

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not (path.startswith('/api/agent-admin') or path.startswith('/api/dsh/')):
            return await self.app(scope, receive, send)

        from starlette.requests import Request
        from starlette.concurrency import run_in_threadpool
        request = Request(scope)
        try:
            is_loopback = ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            is_loopback = False
        host = request.url.hostname
        origin = request.headers.get('origin')
        expected_origin = str(request.base_url).rstrip('/')
        if (not is_loopback or host not in {'127.0.0.1', 'localhost', '::1'}
                or (origin is not None and origin.rstrip('/') != expected_origin)
                or request.headers.get('sec-fetch-site') == 'cross-site'):
            response = JSONResponse({'detail': 'local console access requires a same-origin loopback request'}, status_code=403)
            return await response(scope, receive, send)
        # Explicit credentials must reach normal authentication unchanged. A
        # rejected/expired or non-human identity cannot become a local human.
        # Credential-free browser requests retain the existing local boundary.
        if request.headers.get('authorization') is not None or 'agent_admin_session' in request.cookies:
            scope = dict(scope)
            scope['agent_admin_auth_mode'] = 'explicit'
            return await self.app(scope, receive, send)
        try:
            token = await run_in_threadpool(self.credential.cached_token)
        except Exception:
            response = JSONResponse({'detail': 'local operator access unavailable'}, status_code=503)
            return await response(scope, receive, send)

        # The browser never receives this credential. Existing route and SQL
        # authorization still verify the operator and preserve audit attribution.
        scope = dict(scope)
        scope['headers'] = [(name, value) for name, value in scope['headers'] if name.lower() != b'authorization']
        scope['headers'].append((b'authorization', ('Bearer ' + token).encode('ascii')))
        scope['agent_admin_auth_mode'] = 'local'
        await self.app(scope, receive, send)
