"""
Auth Service (app/modules/auth/service.py)
Core authentication logic, constant-time credentials check, sessions and CSRF.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from app.core.config import settings
from app.core.email import send_invite_email, send_reset_email
from app.core.exceptions import (
    BadRequestError,
    InvalidCredentialsError,
    RateLimitedError,
    UnauthenticatedError,
    ValidationError,
)
from app.core.security import (
    constant_time_compare,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_csrf_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_dummy_password,
    verify_password,
)
from app.infrastructure.db.mongodb import revoked_tokens_collection
from app.modules.auth.interfaces import AuthRepoProtocol
from app.modules.auth.models import (
    create_password_reset_document,
    create_session_document,
)


class AuthService:
    """Manages credentials authentication, JWT tokens, session cookies, and resets."""

    def __init__(self, repo: AuthRepoProtocol):
        self.repo = repo

    async def login(
        self, email: str, password: str, ip: str = "127.0.0.1", user_agent: str = "unknown"
    ) -> Tuple[Dict[str, Any], str, str, str]:
        # 1. Lockout check: max 5 failed logins per email+IP per 15 minutes
        failed_count = await self.repo.check_failed_login(email, ip)
        if failed_count >= 5:
            raise RateLimitedError("Too many failed login attempts. Try again in 15 minutes.", retry_after=900)

        user = await self.repo.get_user_by_email(email)
        if not user or not user.get("is_active", True):
            verify_dummy_password(password)
            await self.repo.record_failed_login(email, ip)
            raise InvalidCredentialsError("Invalid email or password.")

        stored_hash = user.get("password") or user.get("password_hash") or ""
        if not verify_password(password, stored_hash):
            await self.repo.record_failed_login(email, ip)
            raise InvalidCredentialsError("Invalid email or password.")

        # Check tenant if user belongs to one
        company_name = None
        cid = user.get("company_id")
        if cid:
            tenant = await self.repo.get_tenant_by_id(cid)
            if not tenant or tenant.get("status") == "suspended":
                raise UnauthenticatedError("Tenant account is suspended or inactive.")
            company_name = tenant.get("name")

        await self.repo.clear_failed_login(email, ip)

        uid = str(user["_id"])
        role = user.get("role", "employee")
        name = user.get("name") or user.get("username") or email.split("@")[0]

        # 2. Generate JWT Access and Refresh Tokens
        access_token = create_access_token(
            user_id=uid,
            role=role,
            company_id=cid,
            email=user["email"],
            name=name,
        )
        refresh_token = create_refresh_token(user_id=uid)

        # 3. Create session record for session tracking
        raw_token = generate_opaque_token(32)
        token_hash = hash_token(raw_token)
        session_doc = create_session_document(
            user_id=uid,
            token_hash=token_hash,
            ttl_minutes=settings.SESSION_TTL_MINUTES,
            ip=ip,
            user_agent=user_agent,
        )
        await self.repo.create_session(session_doc)
        await self.repo.update_user(uid, {"last_login_at": session_doc["created_at"]})

        csrf_token = generate_csrf_token()
        user_profile = {
            "id": uid,
            "name": name,
            "email": user["email"],
            "role": role,
            "company_name": company_name,
            "is_verified": user.get("is_verified", False),
        }
        return user_profile, access_token, refresh_token, csrf_token

    async def logout(self, token: str, user_id: Optional[str] = None) -> None:
        if token:
            try:
                await revoked_tokens_collection.insert_one(
                    {"token": token, "user_id": user_id, "created_at": datetime.now(timezone.utc)}
                )
            except Exception:
                pass
            try:
                await self.repo.delete_session(hash_token(token))
            except Exception:
                pass
        if user_id:
            try:
                await self.repo.delete_user_sessions(user_id)
            except Exception:
                pass

    async def refresh(self, refresh_token: str) -> Dict[str, Any]:
        if not refresh_token:
            raise UnauthenticatedError("Refresh token missing.")

        try:
            if await revoked_tokens_collection.find_one({"token": refresh_token}):
                raise UnauthenticatedError("Refresh token has been revoked.")
        except UnauthenticatedError:
            raise
        except Exception:
            pass

        try:
            payload = decode_token(refresh_token)
        except Exception:
            raise UnauthenticatedError("Invalid or expired refresh token.")

        if payload.get("type") != "refresh":
            raise UnauthenticatedError("Invalid token type for refresh.")

        user_id = payload.get("sub")
        if not user_id:
            raise UnauthenticatedError("Token missing subject claim.")

        user = await self.repo.get_user_by_id(user_id)
        if not user or not user.get("is_active", True):
            raise UnauthenticatedError("User account is inactive or not found.")

        cid = user.get("company_id")
        role = user.get("role", "employee")
        name = user.get("name") or user.get("username") or user["email"].split("@")[0]

        if cid:
            tenant = await self.repo.get_tenant_by_id(cid)
            if not tenant or tenant.get("status") == "suspended":
                raise UnauthenticatedError("Tenant account is suspended.")

        new_access = create_access_token(
            user_id=str(user["_id"]),
            role=role,
            company_id=cid,
            email=user["email"],
            name=name,
        )
        new_refresh = create_refresh_token(user_id=str(user["_id"]))

        user_profile = {
            "id": str(user["_id"]),
            "username": user.get("username") or name,
            "name": name,
            "email": user["email"],
            "role": role,
            "is_verified": user.get("is_verified", False),
        }
        return {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
            "user": user_profile,
        }

    async def accept_invite(
        self,
        token: str,
        password: str,
        ip: str = "127.0.0.1",
        user_agent: str = "unknown",
    ) -> Dict[str, Any]:
        if not token or not str(token).strip():
            raise BadRequestError("Invitation token is required.")
        if len(password) < 10:
            raise BadRequestError("Password must be at least 10 characters long.")

        token_h = hash_token(token.strip())
        claimed = await self.repo.claim_invite_token(token_h)
        if not claimed:
            raise BadRequestError("Invalid, expired, or already used invitation token.")

        company_id = str(claimed.get("company_id", ""))
        user_id = str(claimed.get("user_id", ""))

        user = None
        if user_id:
            user = await self.repo.get_user_by_id(user_id)
        elif "email" in claimed:
            user = await self.repo.get_user_by_email(claimed["email"])
            if user:
                user_id = str(user["_id"])

        if not user or not user_id:
            raise BadRequestError("User associated with invitation was not found.")

        # Enforce company_id scoping
        if str(user.get("company_id", "")) != company_id:
            raise BadRequestError("Invalid invitation token.")

        now = datetime.now(timezone.utc)
        hashed = hash_password(password)
        updates = {
            "password": hashed,
            "password_hash": hashed,
            "status": "active",
            "is_active": True,
            "is_invited": False,
            "must_change_password": False,
            "activated_at": now,
            "is_verified": True,
        }
        await self.repo.update_user_scoped(user_id=user_id, company_id=company_id, updates=updates)

        tenant = await self.repo.get_tenant_by_id(company_id)
        if tenant and tenant.get("status") in ["pending_onboarding", "onboarding"]:
            await self.repo.update_tenant(company_id, {"status": "active"})

        role = user.get("role") or "company_admin"
        access_token = create_access_token(
            user_id=user_id,
            role=role,
            company_id=company_id,
            email=user.get("email"),
            name=user.get("name"),
        )
        refresh_token = create_refresh_token(user_id=user_id)

        session_doc = create_session_document(
            user_id=user_id,
            token_hash=hash_token(access_token),
            ttl_minutes=settings.SESSION_TTL_MINUTES,
            ip=ip,
            user_agent=user_agent,
        )
        await self.repo.create_session(session_doc)
        csrf_token = generate_csrf_token()

        user_profile = {
            "id": user_id,
            "name": user.get("name") or "Company Admin",
            "email": user.get("email", ""),
            "role": role,
            "company_name": tenant.get("name") if tenant else None,
            "is_verified": True,
        }

        from app.core.logging import logger
        logger.info(
            "Company admin invite accepted",
            extra={"company_id": company_id, "user_id": user_id, "status": "active"},
        )

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "refresh_token": refresh_token,
            "csrf_token": csrf_token,
            "user": user_profile,
            "message": "Account successfully activated.",
            "success": True,
        }

    async def forgot_password(self, email: str) -> None:
        user = await self.repo.get_user_by_email(email)
        if user and user.get("is_active", True):
            token = generate_opaque_token(32)
            doc = create_password_reset_document(str(user["_id"]), email, hash_token(token))
            await self.repo.create_password_reset(doc)
            await send_reset_email(email, user.get("name") or "User", token)

    async def reset_password(self, token: str, new_password: str) -> Dict[str, Any]:
        if len(new_password) < 10:
            raise ValidationError("Password must be at least 10 characters long.")

        user_id = None
        # Try JWT reset token first
        try:
            payload = decode_token(token)
            if payload.get("type") == "reset_password":
                user_id = payload.get("sub")
        except Exception:
            user_id = None

        if not user_id:
            reset_doc = await self.repo.get_password_reset_by_hash(hash_token(token))
            if not reset_doc:
                raise ValidationError("Invalid or expired password reset link.")
            user_id = reset_doc["user_id"]
            await self.repo.mark_password_reset_used(str(reset_doc["_id"]))

        hashed = hash_password(new_password)
        await self.repo.update_user(user_id, {"password": hashed})
        await self.repo.delete_user_sessions(user_id)
        return {"message": "Password reset successfully. Please login with your new password."}


# Backward-compatibility re-exports
from app.modules.auth.legacy import (
    register_user,
    verify_email,
    resend_verification_email,
    login_user,
    refresh_user_token,
    logout_user,
    authenticate_super_admin,
)

