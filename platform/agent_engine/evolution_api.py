"""Human configuration/control; reflection itself runs without further clicks."""
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from agent_admin.credentials import AdminAuth
from .evolution_repository import EvolutionRepository


def create_evolution_router(engine):
    repo=EvolutionRepository(engine.base_connection_factory)
    auth=AdminAuth(engine.base_connection_factory)
    router=APIRouter(prefix='/api/agent-admin/evolution',tags=['research-evolution'])
    def access(request:Request,response:Response):
        response.headers['Cache-Control']='no-store'
        origin=request.headers.get('origin')
        if request.method!='GET' and origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
            raise HTTPException(403,'cross-origin evolution operation rejected')
        bearer=request.headers.get('authorization','')
        actor=auth.authenticate(bearer[7:] if bearer.startswith('Bearer ') else request.cookies.get('agent_admin_session'))
        if not actor or actor.kind!='human':raise HTTPException(401,'human operator required')
        return actor
    def invoke(method,*args):
        try:return method(*args)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    @router.get('/agents/{agent_key}/policy')
    def policy(agent_key:str,actor=Depends(access)):return repo.policy(agent_key)
    @router.put('/agents/{agent_key}/policy')
    def configure(agent_key:str,body:dict,actor=Depends(access)):return invoke(repo.configure,agent_key,body,actor)
    @router.get('/runs/{run_id}')
    def view(run_id:str,actor=Depends(access)):return repo.view(run_id)
    @router.post('/runs/{run_id}/trial',status_code=202)
    def trial(run_id:str,body:dict,actor=Depends(access)):
        if body:raise HTTPException(400,'no trial overrides are allowed')
        return invoke(repo.start_trial,run_id,actor)
    @router.post('/runs/{run_id}/cycles/{cycle_id}/stop')
    def stop(run_id:str,cycle_id:str,body:dict,actor=Depends(access)):
        if set(body)!={'reason'}:raise HTTPException(400,'reason required')
        return invoke(repo.stop,run_id,cycle_id,actor,body['reason'])
    return router
