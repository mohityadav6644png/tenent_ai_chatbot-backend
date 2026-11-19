"""
Auth Endpoints (app/api/v1/endpoints/auth.py)
Thin routing layer for login, logout, me, refresh, accept-invite, forgot/reset-password.
"""

from typing import Optional
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from app.api.deps import bearer_scheme, get_auth_service, get_tenant_context, verify_csrf
from app.api.v1.schemas.auth import (
    AcceptInviteRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    MessageResponse,
    RefreshTokenRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserResponse,
    VerifyEmailRequest,
)
from app.core.config import settings
from app.core.context import TenantContext
from app.modules.auth.service import (
    AuthService,
    authenticate_super_admin,
    register_user,
    resend_verification_email,
    verify_email,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=LoginResponse)
async def login(data: LoginRequest, request: Request, response: Response, service: AuthService = Depends(get_auth_service)):
    ip = request.client.host if request.client else "127.0.0.1"
    ua = request.headers.get("User-Agent", "unknown")
    user_profile, access_token, refresh_token, csrf_token = await service.login(email=data.email, password=data.password, ip=ip, user_agent=ua)
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=settings.COOKIE_SECURE, samesite="lax", max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/")
    response.set_cookie(key="session", value=access_token, httponly=True, secure=settings.COOKIE_SECURE, samesite="lax", max_age=settings.SESSION_TTL_MINUTES * 60, path="/")
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=False, secure=settings.COOKIE_SECURE, samesite="lax", max_age=settings.SESSION_TTL_MINUTES * 60, path="/")
    return {
        "user": user_profile,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/logout", status_code=status.HTTP_200_OK, dependencies=[Depends(verify_csrf)])
async def logout(
    response: Response,
    request: Request,
    session: Optional[str] = Cookie(None),
    access_token_cookie: Optional[str] = Cookie(None, alias="access_token"),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    service: AuthService = Depends(get_auth_service),
):
    token = None
    if auth_header and auth_header.credentials:
        token = auth_header.credentials
    elif access_token_cookie:
        token = access_token_cookie
    elif session:
        token = session

    if token:
        await service.logout(token)
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="session", path="/")
    response.delete_cookie(key="csrf_token", path="/")
    return {"message": "Logged out successfully", "success": True}


@router.get("/me", response_model=MeResponse)
async def get_me(ctx: TenantContext = Depends(get_tenant_context), service: AuthService = Depends(get_auth_service)):
    user = await service.repo.get_user_by_id(ctx.user_id)
    company_name = None
    if ctx.company_id:
        tenant = await service.repo.get_tenant_by_id(ctx.company_id)
        if tenant:
            company_name = tenant.get("name")
    user_name = ctx.name or (user.get("name") if user else "User")
    user_email = user.get("email", "") if user else ""
    user_profile = {
        "id": ctx.user_id,
        "name": user_name,
        "email": user_email,
        "role": ctx.role,
        "company_name": company_name,
        "is_verified": user.get("is_verified", False) if user else False,
    }
    return {
        "user": user_profile,
        "id": ctx.user_id,
        "username": (user.get("username") if user else None) or user_name,
        "name": user_name,
        "email": user_email,
        "role": ctx.role,
        "company_id": ctx.company_id,
        "company_name": company_name,
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(data: RefreshTokenRequest, service: AuthService = Depends(get_auth_service)):
    return await service.refresh(data.refresh_token)


@router.post("/accept-invite", response_model=MessageResponse)
async def accept_invite(data: AcceptInviteRequest, service: AuthService = Depends(get_auth_service)):
    result = await service.accept_invite(token=data.token, password=data.password)
    return {"message": result["message"], "success": True}


@router.post("/forgot-password", status_code=status.HTTP_200_OK, dependencies=[Depends(verify_csrf)])
async def forgot_password(data: ForgotPasswordRequest, service: AuthService = Depends(get_auth_service)):
    await service.forgot_password(email=data.email)
    return {"message": "If the email exists, a password reset link has been sent.", "success": True}


@router.post("/reset-password", response_model=MessageResponse, dependencies=[Depends(verify_csrf)])
async def reset_password(data: ResetPasswordRequest, service: AuthService = Depends(get_auth_service)):
    pwd = data.target_password
    result = await service.reset_password(token=data.token, new_password=pwd)
    return {"message": result["message"], "success": True}


# Retained legacy endpoints
@router.post("/super_login", response_model=TokenResponse)
async def super_admin_login(data: LoginRequest):
    return await authenticate_super_admin(data.email, data.password)


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: LoginRequest):
    return await register_user(username=data.email.split("@")[0], email=data.email, password=data.password)


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email_post(
    request: Request,
    token: Optional[str] = None,
):
    actual_token = token
    if not actual_token:
        try:
            body = await request.json()
            if isinstance(body, dict):
                actual_token = body.get("token")
        except Exception:
            pass
    if not actual_token:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Token is required.")
    return await verify_email(actual_token)


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(data: ResendVerificationRequest):
    return await resend_verification_email(data.email)
