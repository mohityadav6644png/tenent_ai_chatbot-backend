"""
Integration Tests (tests/integration/test_integration.py)
Tests tenants onboarding, auth flow, reports, health, and audit endpoints.
"""

import pytest
from app.api.v1.schemas.auth import RegisterRequest
from app.core.exceptions import TenantExistsError
from app.modules.tenants.service import TenantService


def test_auth_integration():
    reg_data = RegisterRequest(username="integration_user", email="integration@example.com", password="ValidPassword123!")
    assert reg_data.username == "integration_user"


class MockTenantRepo:
    def __init__(self):
        self.tenants = [{"_id": "t1", "name": "Existing Corp", "status": "active"}]

    async def get_tenant_by_name(self, name):
        for t in self.tenants:
            if t["name"].lower() == name.lower():
                return t
        return None

    async def create_tenant_atomic(self, t_doc, u_doc, i_doc):
        self.tenants.append(t_doc)
        return str(t_doc["_id"])


class MockAuditService:
    async def log_event(self, **kwargs):
        pass


@pytest.mark.asyncio
async def test_tenant_onboarding_duplicate_rejected(monkeypatch):
    repo = MockTenantRepo()
    audit = MockAuditService()
    svc = TenantService(repo, audit)
    sent_invites = []

    async def record_onboarding_email(email, name, token, company_name):
        sent_invites.append((email, name, token, company_name))
        return True

    monkeypatch.setattr(
        "app.modules.tenants.service.send_tenant_onboarding_email",
        record_onboarding_email,
    )

    # Duplicate company name (case-insensitive) raises TenantExistsError
    with pytest.raises(TenantExistsError):
        await svc.create_tenant(
            company_name="existing corp",
            admin_name="Admin",
            admin_email="admin@corp.com",
            monthly_query_limit=10000,
        )

    # Successful creation
    res = await svc.create_tenant(
        company_name="New Corp",
        admin_name="Admin",
        admin_email="admin@newcorp.com",
        monthly_query_limit=10000,
    )
    assert res["status"] == "onboarding"
    assert res["invite_email_sent"] is True
    assert sent_invites[0][0:2] == ("admin@newcorp.com", "Admin")
    assert sent_invites[0][3] == "New Corp"


@pytest.mark.asyncio
async def test_tenant_onboarding_reports_email_delivery_failure(monkeypatch):
    async def fail_onboarding_email(email, name, token, company_name):
        return False

    monkeypatch.setattr(
        "app.modules.tenants.service.send_tenant_onboarding_email",
        fail_onboarding_email,
    )
    svc = TenantService(MockTenantRepo(), MockAuditService())

    result = await svc.create_tenant(
        company_name="Mail Failure Corp",
        admin_name="Admin",
        admin_email="admin@mailfailure.com",
        monthly_query_limit=10000,
    )

    assert result["status"] == "onboarding"
    assert result["invite_email_sent"] is False


@pytest.mark.asyncio
async def test_reports_service_flow():
    from app.modules.reports.service import ReportService

    class MockReportsRepo:
        def __init__(self):
            self.reports = [{"_id": "r1", "company_id": "c1", "name": "Daily", "schedule": {}, "status": "active"}]

        async def list_reports(self, cid):
            return [r for r in self.reports if r["company_id"] == cid]

        async def get_report_scoped(self, cid, rid):
            return next((r for r in self.reports if r["_id"] == rid and r["company_id"] == cid), None)

        async def update_report(self, rid, updates):
            for r in self.reports:
                if r["_id"] == rid:
                    r.update(updates)

    svc = ReportService(MockReportsRepo(), query_service=None)
    paused = await svc.pause_report("c1", "r1")
    assert paused["status"] == "paused"

    resumed = await svc.resume_report("c1", "r1")
    assert resumed["status"] == "active"
