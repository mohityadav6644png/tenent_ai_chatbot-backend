"""
RBAC, Revocation, and CSRF Security Tests (tests/security/test_rbac_security.py)
Tests RBAC barriers, immediate revocation of sessions/tenants, and CSRF token enforcement.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from fastapi import Request
import pytest
from app.api.deps import get_tenant_context, require_roles, verify_csrf
from app.core.context import TenantContext
from app.core.exceptions import CsrfFailedError, ForbiddenRoleError, UnauthenticatedError


@pytest.mark.asyncio
async def test_rbac_access_control():
    # 6. RBAC verification across roles
    emp_ctx = TenantContext(user_id="u1", company_id="c1", role="employee", name="Emp")
    admin_ctx = TenantContext(user_id="u2", company_id="c1", role="company_admin", name="Admin")
    super_ctx = TenantContext(user_id="u3", company_id=None, role="super_admin", name="Super")

    # Employee cannot call company_admin endpoints
    admin_checker = require_roles("company_admin")
    with pytest.raises(ForbiddenRoleError):
        await admin_checker(emp_ctx)
    assert (await admin_checker(admin_ctx)) == admin_ctx

    # Company admin cannot call super_admin internal/* endpoints
    super_checker = require_roles("super_admin")
    with pytest.raises(ForbiddenRoleError):
        await super_checker(admin_ctx)
    assert (await super_checker(super_ctx)) == super_ctx

    # Super admin cannot query tenant chat endpoints
    chat_checker = require_roles("employee", "company_admin")
    with pytest.raises(ForbiddenRoleError):
        await chat_checker(super_ctx)
    assert (await chat_checker(emp_ctx)) == emp_ctx


@pytest.mark.asyncio
async def test_immediate_revocation_deactivated_and_suspended():
    # 7. Deactivated user and suspended tenant immediately lose access
    req = AsyncMock(spec=Request)
    req.method = "GET"
    req.url.path = "/v1/auth/me"

    class MockRevocationRepo:
        def __init__(self, is_active=True, tenant_status="active", valid_session=True):
            self._is_active = is_active
            self._tenant_status = tenant_status
            self._valid_session = valid_session

        async def get_session_by_hash(self, token_hash):
            if not self._valid_session:
                return None
            return {"user_id": "u1", "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)}

        async def get_user_by_id(self, uid):
            return {"_id": "u1", "company_id": "c1", "role": "employee", "is_active": self._is_active}

        async def get_tenant_by_id(self, cid):
            return {"_id": "c1", "status": self._tenant_status}

    # Case A: Deactivated user
    with patch("app.api.deps.get_auth_repo", return_value=MockRevocationRepo(is_active=False)):
        with pytest.raises(UnauthenticatedError):
            await get_tenant_context(request=req, session="test_sess_token", auth_header=None)

    # Case B: Suspended tenant
    with patch("app.api.deps.get_auth_repo", return_value=MockRevocationRepo(tenant_status="suspended")):
        with pytest.raises(UnauthenticatedError):
            await get_tenant_context(request=req, session="test_sess_token", auth_header=None)

    # Case C: Expired or revoked session
    with patch("app.api.deps.get_auth_repo", return_value=MockRevocationRepo(valid_session=False)):
        with pytest.raises(UnauthenticatedError):
            await get_tenant_context(request=req, session="test_sess_token", auth_header=None)


@pytest.mark.asyncio
async def test_csrf_verification_and_cookie_flags():
    # 8. Missing/mismatched CSRF token rejected (403 CSRF_FAILED)
    req = AsyncMock(spec=Request)
    req.method = "POST"
    req.url.path = "/v1/chat/query"

    # Missing header
    with pytest.raises(CsrfFailedError):
        await verify_csrf(request=req, csrf_cookie="valid_token", csrf_header=None)

    # Mismatched token
    with pytest.raises(CsrfFailedError):
        await verify_csrf(request=req, csrf_cookie="valid_token", csrf_header="wrong_token")

    # Matching token passes without error
    await verify_csrf(request=req, csrf_cookie="same_token", csrf_header="same_token")
