"""Authenticated Profile draft, validation and explicit publication endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .api import SESSION_COOKIE
from .credentials import AdminAuth
from .config_publication import (ConfigPublicationService, ConfigConflictError,
                                 ConfigNotFoundError, ConfigValidationError)


def create_config_publication_router(connection_factory, *, auth=None, service=None):
    auth = auth or AdminAuth(connection_factory)
    service = service or ConfigPublicationService(connection_factory)
    router = APIRouter(prefix='/api/agent-admin', tags=['profile-publication'])

    def access(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
                raise HTTPException(403, 'cross-origin configuration operation rejected')
        bearer = request.headers.get('authorization', '')
        token = bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, 'authenticated administrator required')
        if not actor.permits('admin'):
            raise HTTPException(403, 'administrator required')
        return actor, token

    def read_access(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        bearer = request.headers.get('authorization', '')
        token = bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get(SESSION_COOKIE)
        actor = auth.authenticate(token)
        if actor is None:
            raise HTTPException(401, 'authenticated local operator required')
        return actor

    def invoke(method, identity, *args, **kwargs):
        actor, token = identity
        try:
            return method(actor, *args, actor_token=token, **kwargs)
        except ConfigNotFoundError:
            raise HTTPException(404, 'configuration or draft not found') from None
        except ConfigConflictError:
            raise HTTPException(409, 'configuration changed; reload and review the current draft') from None
        except ConfigValidationError as error:
            raise HTTPException(400, {'message': 'configuration validation failed', 'errors': error.errors}) from None
        except PermissionError:
            raise HTTPException(403, 'current administrator credential required') from None
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, 'invalid configuration request') from None
        except Exception:
            raise HTTPException(409, 'configuration operation could not be applied') from None

    def fields(body, required, optional=()):
        if not set(required).issubset(body) or set(body) - set(required) - set(optional):
            raise HTTPException(400, 'invalid configuration operation fields')

    @router.get('/config-profiles/{agent_key}')
    def export(agent_key: str, identity=Depends(access)):
        return invoke(service.export_profile, identity, agent_key)

    @router.get('/agent-profile-views/{agent_key}')
    def profile_view(agent_key: str, actor=Depends(read_access)):
        try:
            return service.read_profile_view(agent_key)
        except ConfigNotFoundError:
            raise HTTPException(404, 'Agent profile not found') from None
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, 'invalid Agent key') from None
        except Exception:
            raise HTTPException(409, 'Agent profile view could not be loaded') from None

    @router.get('/config-profiles/{agent_key}/drafts')
    def drafts(agent_key: str, identity=Depends(access)):
        return invoke(service.list_drafts, identity, agent_key)

    @router.post('/config-profiles/{agent_key}/drafts', status_code=201)
    def create(agent_key: str, body: dict, identity=Depends(access)):
        fields(body, ['reason'], ['document'])
        return invoke(service.create_draft, identity, agent_key, reason=body['reason'], document=body.get('document'))

    @router.get('/config-drafts/{draft_id}')
    def draft(draft_id: str, revision: int | None = None, identity=Depends(access)):
        return invoke(service.get_draft, identity, draft_id, revision=revision)

    @router.put('/config-drafts/{draft_id}')
    def save(draft_id: str, body: dict, identity=Depends(access)):
        fields(body, ['document', 'expectedRevision', 'reason'])
        return invoke(service.save_draft, identity, draft_id, body['document'],
                      expected_revision=body['expectedRevision'], reason=body['reason'])

    @router.post('/config-drafts/{draft_id}/validate')
    def validate(draft_id: str, body: dict, identity=Depends(access)):
        fields(body, [], ['revision'])
        return invoke(service.validate_draft, identity, draft_id, revision=body.get('revision'))

    @router.post('/config-drafts/{draft_id}/publish')
    def publish(draft_id: str, body: dict, identity=Depends(access)):
        fields(body, ['expectedRevision', 'idempotencyKey', 'reason'])
        return invoke(service.publish_draft, identity, draft_id, expected_revision=body['expectedRevision'],
                      idempotency_key=body['idempotencyKey'], reason=body['reason'])

    @router.get('/config-profiles/{agent_key}/publications')
    def publications(agent_key: str, identity=Depends(access)):
        return invoke(service.list_publications, identity, agent_key)

    @router.get('/config-publications/{publication_id}')
    def publication(publication_id: str, identity=Depends(access)):
        return invoke(service.get_publication, identity, publication_id)

    return router
