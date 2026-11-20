"""Authenticated read-only entry points, backed by one VerificationService."""
from fastapi import APIRouter, Depends, HTTPException, Request
from agent_admin.credentials import AdminAuth
from .verification.contracts import VerificationError


def verification_response(service, body, actor, channel):
    try:
        return service.check(body, actor, channel=channel)
    except VerificationError as exc:
        raise HTTPException(exc.status, {'code': exc.code, 'message': '核验未能完成；请按错误码检查访问或服务状态。'}) from None


def create_verification_router(factory, service):
    router = APIRouter(prefix='/api/agent-admin/verifications', tags=['verification'])
    auth = AdminAuth(factory)

    def principal(request: Request):
        header = request.headers.get('authorization', '')
        actor = auth.authenticate(header[7:] if header.startswith('Bearer ') else request.cookies.get('agent_admin_session'))
        if actor is None:
            verification_response(service, {}, None, 'admin')
        return actor

    @router.post('/check')
    def check(body: dict, actor=Depends(principal)):
        return verification_response(service, body, actor, 'admin')

    return router
