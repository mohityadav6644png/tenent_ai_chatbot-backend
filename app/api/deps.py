"""
API Dependencies (app/api/deps.py)
Authentication, session validation, role checks, CSRF, and service token guards.
"""

from datetime import datetime, timezone
import secrets
from typing import Callable, Optional
from bson import ObjectId
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from app.container import get_auth_repo, get_auth_service
from app.core.config import settings
from app.core.context import TenantContext
from app.core.exceptions import (
    CsrfFailedError,
    ForbiddenRoleError,
    UnauthenticatedError,
)
from app.core.security import decode_token, hash_token
from app.infrastructure.db.mongodb import (
    database,
    revoked_tokens_collection,
    users_collection,
)

bearer_scheme = HTTPBearer(auto_error=False)


async def get_tenant_context(
    request: Request,
    session: Optional[str] = Cookie(None),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    access_token_cookie: Optional[str] = Cookie(None, alias="access_token"),
) -> TenantContext:
    """Validate JWT bearer token or session token, load user & tenant, enforce active status."""
    token = None
    if isinstance(auth_header, HTTPAuthorizationCredentials) and auth_header.credentials:
        token = auth_header.credentials
    elif isinstance(session, str) and session:
        token = session
    elif isinstance(access_token_cookie, str) and access_token_cookie:
        token = access_token_cookie

    if not token or not isinstance(token, str):
        raise UnauthenticatedError("No session or credentials provided.")

    # 1. Check if token is explicitly revoked
    try:
        if await revoked_tokens_collection.find_one({"token": token}):
            raise UnauthenticatedError("Token has been revoked.")
    except UnauthenticatedError:
        raise
    except Exception:
        pass

    # 2. Try JWT decoding first
    is_jwt = False
    payload = None
    try:
        payload = decode_token(token)
        is_jwt = True
    except jwt.ExpiredSignatureError:
        raise UnauthenticatedError("Token has expired.")
    except Exception:
        is_jwt = False

    repo = get_auth_repo()

    if is_jwt and payload:
        user_id = payload.get("sub")
        if not user_id:
            raise UnauthenticatedError("Token missing subject claim.")

        role = payload.get("role", "employee")
        cid = payload.get("company_id")
        name = payload.get("name")

        user = await repo.get_user_by_id(user_id)
        if user:
            if not user.get("is_active", True):
                raise UnauthenticatedError("User account is inactive or not found.")
            role = user.get("role") or role
            cid = user.get("company_id") or cid
            name = user.get("name") or name

        if cid:
            tenant = await repo.get_tenant_by_id(cid)
            if not tenant or tenant.get("status") == "suspended":
                raise UnauthenticatedError("Tenant account is suspended.")

        return TenantContext(
            user_id=str(user_id),
            company_id=cid,
            role=role,
            name=name,
        )

    # 3. Fallback to opaque session hash lookup for session-based legacy tests/callers
    s_doc = await repo.get_session_by_hash(hash_token(token))
    if not s_doc:
        raise UnauthenticatedError("Session has expired or is invalid.")

    now = datetime.now(timezone.utc)
    exp = s_doc.get("expires_at")
    if exp:
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            await repo.delete_session(hash_token(token))
            raise UnauthenticatedError("Session has expired.")

    user = await repo.get_user_by_id(s_doc["user_id"])
    if not user or not user.get("is_active", True):
        raise UnauthenticatedError("User account is inactive or not found.")

    cid = user.get("company_id")
    if cid:
        tenant = await repo.get_tenant_by_id(cid)
        if not tenant or tenant.get("status") == "suspended":
            raise UnauthenticatedError("Tenant account is suspended.")

    return TenantContext(
        user_id=str(user["_id"]),
        company_id=cid,
        role=user.get("role", "employee"),
        name=user.get("name"),
    )


def require_roles(*allowed_roles: str) -> Callable:
    async def role_checker(ctx: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        if ctx.role not in allowed_roles:
            raise ForbiddenRoleError("Insufficient privileges for this action.")
        if ctx.role in ("employee", "company_admin") and not ctx.company_id:
            raise ForbiddenRoleError("Tenant company context required for this role.")
        return ctx
    return role_checker


async def require_service_token(
    x_service_token: Optional[str] = Header(None, alias="X-Service-Token"),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> bool:
    token = x_service_token or (auth_header.credentials if auth_header else "")
    if not token or not secrets.compare_digest(token.encode("utf-8"), settings.SERVICE_TOKEN.encode("utf-8")):
        raise UnauthenticatedError("Invalid or missing service token.")
    return True


async def verify_csrf(
    request: Request,
    csrf_cookie: Optional[str] = Cookie(None, alias="csrf_token"),
    csrf_header: Optional[str] = Header(None, alias="X-CSRF-Token"),
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    # If the request authenticates via Authorization: Bearer <jwt>, CSRF is not applicable
    if isinstance(auth_header, HTTPAuthorizationCredentials) and auth_header.credentials:
        return
    try:
        header_auth = getattr(request, "headers", {}).get("Authorization")
        if header_auth and str(header_auth).startswith("Bearer "):
            return
    except Exception:
        pass

    if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
        path = request.url.path
        if path in [
            "/v1/auth/login",
            "/v1/auth/accept-invite",
            "/v1/auth/super_login",
            "/v1/auth/register",
            "/v1/auth/refresh",
            "/v1/auth/verify-email",
            "/v1/auth/resend-verification",
            "/v1/auth/forgot-password",
            "/v1/auth/reset-password",
        ]:
            return
        if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
            raise CsrfFailedError("CSRF verification failed: cookie and header mismatch.")


# Backward-compatibility dependencies
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict:
    token = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    elif request:
        token = request.cookies.get("access_token") or request.cookies.get("session")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No credentials provided.",
        )

    try:
        if await revoked_tokens_collection.find_one({"token": token}):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked.")
    except HTTPException:
        raise
    except Exception:
        pass

    try:
        payload = decode_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token or token expired: {str(exc)}",
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim.",
        )

    user = None
    try:
        if ObjectId.is_valid(user_id):
            user = await users_collection.find_one({"_id": ObjectId(user_id)})
        if not user:
            user = await users_collection.find_one({"_id": user_id})
    except Exception:
        user = None

    if not user:
        user = {
            "_id": user_id,
            "id": str(user_id),
            "role": payload.get("role", "employee"),
            "company_id": payload.get("company_id"),
        }
    else:
        user["id"] = str(user["_id"])

    user["_current_token"] = token
    user["_token_payload"] = payload
    if "role" in payload:
        user["role"] = payload["role"]
    if "company_id" in payload:
        user["company_id"] = str(payload["company_id"])

    return user


async def get_db(request: Request):
    if hasattr(request.app, "mongodb_client") and request.app.mongodb_client:
        try:
            return request.app.mongodb_client[settings.MONGODB_DB]
        except Exception:
            pass
    return database


async def require_company_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    payload = current_user.get("_token_payload") or {}
    role = payload.get("role") or current_user.get("role")
    if role != "company_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: company_admin role required.",
        )
    company_id = payload.get("company_id") or current_user.get("company_id")
    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: company_id missing from credentials.",
        )
    return current_user
