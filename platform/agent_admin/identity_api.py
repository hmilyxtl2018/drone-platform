"""Authenticated identity administration; issuance responses are never cached."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .api import SESSION_COOKIE
from .credentials import AdminAuth
from .identity_management import (
    IdentityConflictError, IdentityManagementService, IdentityNotFoundError,
)


def create_identity_router(connection_factory, *, auth=None, service=None):
    auth = auth or AdminAuth(connection_factory)
    service = service or IdentityManagementService(connection_factory)

    def access(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                raise HTTPException(403, 'cross-origin identity operation rejected')
        bearer = request.headers.get('authorization', '')
        token = bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, 'authenticated local administrator required')
        if not actor.permits('admin'):
            raise HTTPException(403, 'admin role required')
        return actor, token

    router = APIRouter(prefix='/api/agent-admin', tags=['identity-management'])

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except IdentityNotFoundError:
            raise HTTPException(404, 'identity or token not found') from None
        except IdentityConflictError as exc:
            raise HTTPException(409, str(exc)) from None
        except PermissionError:
            raise HTTPException(403, 'current administrator credential required') from None
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'invalid identity management request') from None
        except Exception:
            # Database exception text may contain request data or configuration.
            raise HTTPException(409, 'identity operation could not be applied') from None

    @router.get('/identities')
    def identities(limit: int = 200, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(service.list_identities, actor, actor_token=token, limit=limit)

    @router.post('/identities', status_code=201)
    def issue(body: dict, authenticated=Depends(access)):
        if set(body) - {'displayName', 'roles', 'expiresDays', 'reason'} \
                or not {'displayName', 'roles', 'reason'}.issubset(body):
            raise HTTPException(400, 'invalid identity creation fields')
        actor, token = authenticated
        return invoke(service.issue_identity, actor, body['displayName'], body['roles'],
                      actor_token=token, expires_days=body.get('expiresDays', 1), reason=body['reason'])

    @router.patch('/identities/{principal_id}')
    def update(principal_id: str, body: dict, authenticated=Depends(access)):
        if set(body) != {'expectedRevision', 'reason', 'patch'}:
            raise HTTPException(400, 'revision, reason and identity patch required')
        actor, token = authenticated
        return invoke(service.update_identity, actor, principal_id, body['patch'], actor_token=token,
                      expected_revision=body['expectedRevision'], reason=body['reason'])

    @router.get('/identity-tokens')
    def tokens(principalId: str | None = None, limit: int = 200, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(service.list_tokens, actor, principalId, actor_token=token, limit=limit)

    @router.post('/identity-tokens/{token_id}/revoke')
    def revoke(token_id: str, body: dict, authenticated=Depends(access)):
        if set(body) != {'reason'}:
            raise HTTPException(400, 'revocation reason required')
        actor, token = authenticated
        return invoke(service.revoke_token, actor, token_id, actor_token=token, reason=body['reason'])

    @router.get('/identity-audit')
    def audit(principalId: str | None = None, after: int = 0, limit: int = 200, authenticated=Depends(access)):
        actor, token = authenticated
        return invoke(service.list_audit, actor, principalId, actor_token=token, after=after, limit=limit)

    return router
