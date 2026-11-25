"""
Test Company Admin Onboarding Invite & Password-Setup Flow (test_accept_invite.py)
Validates invite token claiming, JWT issuance with company_id scoping, single-use enforcement,
expiry rejection, password hashing, and provisioning email hook.
"""

from datetime import datetime, timedelta, timezone
import secrets
import httpx
import pytest
from app.core.security import decode_token, hash_password, hash_token, verify_password
from app.infrastructure.db.mongodb import (
    app_database,
    invite_tokens_collection,
    tenants_collection,
    users_collection,
)
from app.main import create_app
from app.modules.auth.models import (
    create_invite_document,
    create_invite_token_document,
    create_user_document,
)
from app.modules.tenants.models import create_tenant_document
from app.modules.tenants.service import TenantService


@pytest.mark.asyncio
async def test_accept_invite_success_and_jwt_claims():
    app = create_app()

    # 1. Setup tenant and invited company admin
    company_name = f"TestCorp_{secrets.token_hex(4)}"
    tenant_doc = create_tenant_document(name=company_name, monthly_query_limit=10000, status="pending_onboarding")
    await tenants_collection.insert_one(tenant_doc)
    cid = str(tenant_doc["_id"])

    admin_email = f"admin_{secrets.token_hex(4)}@example.com"
    admin_doc = create_user_document(
        username=admin_email.split("@")[0],
        email=admin_email,
        hashed_password="PENDING_SETUP",
        name="Jane Admin",
        role="company_admin",
        company_id=cid,
        is_active=False,
        is_invited=True,
        status="invited",
        must_change_password=True,
    )
    await users_collection.insert_one(admin_doc)
    uid = str(admin_doc["_id"])

    # 2. Generate cryptographically random token and store SHA-256 hash
    raw_token = secrets.token_urlsafe(32)
    token_h = hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)

    invite_token_doc = create_invite_token_document(
        token_hash=token_h,
        company_id=cid,
        user_id=uid,
        expires_at=expires_at,
    )
    await invite_tokens_collection.insert_one(invite_token_doc)

    # 3. Call POST /api/v1/auth/accept-invite
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": raw_token, "password": "SecurePassword123!"},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["role"] == "company_admin"
        assert data["user"]["email"] == admin_email

        # 4. Verify JWT claims: sub, company_id, role
        claims = decode_token(data["access_token"])
        assert claims["sub"] == uid
        assert claims["company_id"] == cid
        assert claims["role"] == "company_admin"

    # 5. Verify database updates: user active, password hashed with Argon2, never plain text
    updated_user = await users_collection.find_one({"_id": admin_doc["_id"]})
    assert updated_user["status"] == "active"
    assert updated_user["is_active"] is True
    assert updated_user["is_invited"] is False
    assert updated_user["must_change_password"] is False
    assert updated_user["activated_at"] is not None
    assert updated_user["password"] != "SecurePassword123!"
    assert verify_password("SecurePassword123!", updated_user["password"]) is True

    # 6. Verify invite_token is marked used atomically
    token_in_db = await invite_tokens_collection.find_one({"token_hash": token_h})
    assert token_in_db["used"] is True
    assert token_in_db["used_at"] is not None

    # 7. Verify tenant status activated
    updated_tenant = await tenants_collection.find_one({"_id": tenant_doc["_id"]})
    assert updated_tenant["status"] == "active"

    # 8. Verify subsequent normal login (Work email + new Password) succeeds with company_id & role
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": admin_email, "password": "SecurePassword123!"},
        )
        assert login_res.status_code == 200, login_res.text
        login_data = login_res.json()
        assert "access_token" in login_data
        login_claims = decode_token(login_data["access_token"])
        assert login_claims["sub"] == uid
        assert login_claims["company_id"] == cid
        assert login_claims["role"] == "company_admin"


@pytest.mark.asyncio
async def test_accept_invite_single_use_replay_rejected():
    app = create_app()

    company_name = f"ReplayCorp_{secrets.token_hex(4)}"
    tenant_doc = create_tenant_document(name=company_name, monthly_query_limit=10000, status="onboarding")
    await tenants_collection.insert_one(tenant_doc)
    cid = str(tenant_doc["_id"])

    admin_email = f"replay_{secrets.token_hex(4)}@example.com"
    admin_doc = create_user_document(
        username=admin_email.split("@")[0],
        email=admin_email,
        hashed_password="PENDING_SETUP",
        role="company_admin",
        company_id=cid,
        is_invited=True,
    )
    await users_collection.insert_one(admin_doc)
    uid = str(admin_doc["_id"])

    raw_token = secrets.token_urlsafe(32)
    token_h = hash_token(raw_token)
    await invite_tokens_collection.insert_one(
        create_invite_token_document(
            token_hash=token_h,
            company_id=cid,
            user_id=uid,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # First use succeeds
        res1 = await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": raw_token, "password": "Password12345!"},
        )
        assert res1.status_code == 200

        # Second use (replay) MUST be rejected with 400
        res2 = await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": raw_token, "password": "AnotherPassword123!"},
        )
        assert res2.status_code == 400


@pytest.mark.asyncio
async def test_accept_invite_expired_token_rejected():
    app = create_app()

    company_name = f"ExpiredCorp_{secrets.token_hex(4)}"
    tenant_doc = create_tenant_document(name=company_name, monthly_query_limit=10000)
    await tenants_collection.insert_one(tenant_doc)
    cid = str(tenant_doc["_id"])

    admin_email = f"expired_{secrets.token_hex(4)}@example.com"
    admin_doc = create_user_document(
        username=admin_email.split("@")[0],
        email=admin_email,
        hashed_password="PENDING_SETUP",
        role="company_admin",
        company_id=cid,
    )
    await users_collection.insert_one(admin_doc)
    uid = str(admin_doc["_id"])

    raw_token = secrets.token_urlsafe(32)
    token_h = hash_token(raw_token)
    # Expired 2 days ago
    past_expiry = datetime.now(timezone.utc) - timedelta(days=2)
    await invite_tokens_collection.insert_one(
        create_invite_token_document(
            token_hash=token_h,
            company_id=cid,
            user_id=uid,
            expires_at=past_expiry,
        )
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": raw_token, "password": "Password12345!"},
        )
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_accept_invite_invalid_token_rejected():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": "non-existent-token-value", "password": "Password12345!"},
        )
        assert res.status_code == 400


@pytest.mark.asyncio
async def test_accept_invite_short_password_rejected():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": "some-token", "password": "short"},
        )
        assert res.status_code in [400, 422]


@pytest.mark.asyncio
async def test_tenant_service_provisioning_stores_token_hash_and_sends_email(monkeypatch):
    from app.infrastructure.db.repositories import MongoAuditRepository
    from app.infrastructure.db.tenant_user_repo import MongoTenantRepository
    from app.modules.audit.service import AuditService

    repo = MongoTenantRepository()
    audit_repo = MongoAuditRepository()
    audit = AuditService(audit_repo)
    svc = TenantService(repo, audit)

    sent_emails = []

    async def mock_send_email(email, name, token, company_name):
        sent_emails.append((email, name, token, company_name))
        return True

    monkeypatch.setattr(
        "app.modules.tenants.service.send_tenant_onboarding_email",
        mock_send_email,
    )

    company_name = f"ProvisionCorp_{secrets.token_hex(4)}"
    admin_email = f"prov_{secrets.token_hex(4)}@example.com"
    admin_name = "Alex Provisioner"

    res = await svc.create_tenant(
        company_name=company_name,
        admin_name=admin_name,
        admin_email=admin_email,
        monthly_query_limit=10000,
    )

    assert res["status"] == "onboarding"
    assert res["invite_email_sent"] is True
    cid = res["id"]

    # Verify email was triggered with raw token
    assert len(sent_emails) == 1
    recipient, name, raw_token, cname = sent_emails[0]
    assert recipient == admin_email
    assert name == admin_name
    assert cname == company_name
    assert len(raw_token) >= 32

    # Verify raw token is NOT in database, ONLY SHA-256 hash
    token_h = hash_token(raw_token)
    token_doc = await invite_tokens_collection.find_one({"company_id": cid})
    assert token_doc is not None
    assert token_doc["token_hash"] == token_h
    assert raw_token not in str(token_doc)

    # Verify user was created with company_id, status="invited", must_change_password=True
    user_doc = await users_collection.find_one({"company_id": cid, "email": admin_email})
    assert user_doc is not None
    assert user_doc["role"] == "company_admin"
    assert user_doc["status"] == "invited"
    assert user_doc["must_change_password"] is True


@pytest.mark.asyncio
async def test_accept_invite_logs_contain_no_raw_tokens_or_passwords(caplog):
    import logging
    app = create_app()

    raw_token = "secret_raw_token_xyz123"
    chosen_password = "SecretPasswordToNotLog123!"

    with caplog.at_level(logging.DEBUG):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/v1/auth/accept-invite",
                json={"token": raw_token, "password": chosen_password},
            )

    log_text = caplog.text
    assert raw_token not in log_text
    assert chosen_password not in log_text

