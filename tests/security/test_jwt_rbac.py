"""
JWT Authentication & Role-Based Access Control (RBAC) Tests
(tests/security/test_jwt_rbac.py)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from bson import ObjectId
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials
import pytest

from app.api.deps import get_tenant_context, require_roles
from app.core.config import settings
from app.core.context import TenantContext
from app.core.exceptions import ForbiddenRoleError, UnauthenticatedError
from app.core.security import create_access_token, create_refresh_token, decode_token


@pytest.mark.asyncio
async def test_jwt_claims_and_decoding():
    uid = str(ObjectId())
    cid = "test_company_123"
    token = create_access_token(
        user_id=uid,
        role="company_admin",
        company_id=cid,
        email="admin@test.com",
        name="Admin User",
    )

    payload = decode_token(token)
    assert payload["sub"] == uid
    assert payload["role"] == "company_admin"
    assert payload["company_id"] == cid
    assert payload["email"] == "admin@test.com"
    assert payload["name"] == "Admin User"
    assert payload["type"] == "access"


@pytest.mark.asyncio
async def test_jwt_bearer_get_tenant_context_resolution():
    uid = str(ObjectId())
    cid = "test_comp_456"
    token = create_access_token(
        user_id=uid,
        role="employee",
        company_id=cid,
        email="emp@test.com",
        name="Emp User",
    )

    req = AsyncMock(spec=Request)
    req.method = "GET"
    req.url.path = "/v1/chat/query"
    auth_header = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    class MockRepo:
        async def get_user_by_id(self, user_id):
            return {"_id": user_id, "is_active": True, "role": "employee", "company_id": cid, "name": "Emp User"}

        async def get_tenant_by_id(self, tenant_id):
            return {"_id": tenant_id, "status": "active"}

    with patch("app.api.deps.get_auth_repo", return_value=MockRepo()):
        ctx = await get_tenant_context(request=req, session=None, auth_header=auth_header)
        assert ctx.user_id == uid
        assert ctx.role == "employee"
        assert ctx.company_id == cid
        assert ctx.name == "Emp User"


@pytest.mark.asyncio
async def test_jwt_rbac_enforcement():
    emp_ctx = TenantContext(user_id="u1", company_id="c1", role="employee", name="Emp")
    admin_ctx = TenantContext(user_id="u2", company_id="c1", role="company_admin", name="Admin")
    super_ctx = TenantContext(user_id="u3", company_id=None, role="super_admin", name="Super")

    # 1. Employee role guard
    emp_guard = require_roles("employee", "company_admin")
    assert (await emp_guard(emp_ctx)) == emp_ctx
    assert (await emp_guard(admin_ctx)) == admin_ctx
    with pytest.raises(ForbiddenRoleError):
        await emp_guard(super_ctx)

    # 2. Company Admin role guard
    admin_guard = require_roles("company_admin")
    with pytest.raises(ForbiddenRoleError):
        await admin_guard(emp_ctx)
    assert (await admin_guard(admin_ctx)) == admin_ctx
    with pytest.raises(ForbiddenRoleError):
        await admin_guard(super_ctx)

    # 3. Super Admin role guard
    super_guard = require_roles("super_admin")
    with pytest.raises(ForbiddenRoleError):
        await super_guard(emp_ctx)
    with pytest.raises(ForbiddenRoleError):
        await super_guard(admin_ctx)
    assert (await super_guard(super_ctx)) == super_ctx


@pytest.mark.asyncio
async def test_revoked_jwt_token_rejected():
    token = create_access_token(user_id="u1", role="employee", company_id="c1")
    req = AsyncMock(spec=Request)
    auth_header = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with patch("app.api.deps.revoked_tokens_collection.find_one", return_value={"token": token}):
        with pytest.raises(UnauthenticatedError) as exc_info:
            await get_tenant_context(request=req, session=None, auth_header=auth_header)
        assert "revoked" in str(exc_info.value).lower()
