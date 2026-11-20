"""Human control surface. Approval/release operations are never model tools."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from agent_admin.credentials import AdminAuth
from agent_admin.config_publication import ConfigConflictError, ConfigValidationError
from .research_governance import ResearchGovernance
from .task_intent import revoke


def create_research_governance_router(engine, queue):
    factory = engine.base_connection_factory
    auth, service = AdminAuth(factory), ResearchGovernance(engine,queue)
    router = APIRouter(prefix='/api/agent-admin/research-governance',tags=['research-governance'])

    def access(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        origin = request.headers.get('origin')
        if request.method != 'GET' and origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403,'cross-origin governance operation rejected')
        bearer = request.headers.get('authorization','')
        token = bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get('agent_admin_session')
        actor = auth.authenticate(token)
        if not actor or actor.kind != 'human':
            raise HTTPException(401,'human operator required')
        if request.method != 'GET' and not actor.permits('admin'):
            raise HTTPException(403,'human administrator required')
        return actor,token

    def invoke(method,*args,**kwargs):
        try:
            return method(*args,**kwargs)
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404,str(exc)) from None
        except ConfigValidationError as exc:
            raise HTTPException(400,{'message':'candidate validation failed','errors':exc.errors}) from None
        except (ValueError,KeyError,ConfigConflictError) as exc:
            raise HTTPException(409,str(exc)) from None

    def fields(body,allowed):
        if set(body)-set(allowed):
            raise HTTPException(400,'unsupported governance fields')

    @router.get('/runs/{run_id}')
    def collaboration(run_id: str, identity=Depends(access)):
        return invoke(service.collaboration,run_id)

    @router.get('/runs/{run_id}/improvements')
    def improvements(run_id: str, identity=Depends(access)):
        return invoke(service.proposals,run_id)

    @router.post('/runs/{run_id}/revoke')
    def revoke_intent(run_id: str, body: dict, identity=Depends(access)):
        fields(body,['reason'])
        return invoke(revoke,factory,run_id,identity[0],body.get('reason'))

    @router.post('/runs/{run_id}/improvements',status_code=201)
    def propose(run_id: str, body: dict, identity=Depends(access)):
        fields(body,['instructions','kind'])
        return invoke(service.propose,run_id,actor=identity[0],actor_token=identity[1],**body)

    @router.post('/proposals/{proposal_id}/candidate-run',status_code=202)
    def candidate(proposal_id: str, body: dict, identity=Depends(access)):
        fields(body,[])
        return invoke(service.start_candidate,proposal_id,actor=identity[0],actor_token=identity[1])

    @router.post('/proposals/{proposal_id}/evaluate')
    def evaluate(proposal_id: str, body: dict, identity=Depends(access)):
        fields(body,[])
        return invoke(service.evaluate,proposal_id,actor=identity[0],actor_token=identity[1])

    @router.post('/proposals/{proposal_id}/decision')
    def decide(proposal_id: str, body: dict, identity=Depends(access)):
        fields(body,['decision','reason'])
        return invoke(service.decide,proposal_id,actor=identity[0],actor_token=identity[1],
                      decision=body.get('decision'),reason=body.get('reason'))

    @router.post('/proposals/{proposal_id}/publish')
    def publish(proposal_id: str, body: dict, identity=Depends(access)):
        fields(body,['reason','idempotencyKey'])
        with factory() as con:
            with con.cursor() as c:
                p,draft = invoke(service.proposal,proposal_id,c)
        return invoke(service.config.publish_draft,identity[0],p['draft_id'],actor_token=identity[1],
            expected_revision=p['draft_revision'],idempotency_key=body.get('idempotencyKey'),reason=body.get('reason'))

    @router.post('/publications/{publication_id}/rollback-draft',status_code=201)
    def rollback(publication_id: str, body: dict, identity=Depends(access)):
        fields(body,['reason'])
        return invoke(service.rollback,publication_id,actor=identity[0],actor_token=identity[1],reason=body.get('reason'))

    return router
