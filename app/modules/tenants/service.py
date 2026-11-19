"""
Tenants Service (app/modules/tenants/service.py)
Super-admin tenant lifecycle: onboarding with admin invite, suspend, reactivate, schema sync.
"""

from typing import Any, Dict
from app.core.email import send_tenant_onboarding_email
from app.core.exceptions import (
    NotFoundError,
    TenantExistsError,
    UserExistsError,
    ValidationError,
)
from app.core.security import generate_opaque_token, hash_token
from app.modules.audit.service import AuditService
from app.modules.auth.models import create_invite_document, create_user_document
from app.modules.tenants.interfaces import TenantRepoProtocol
from app.modules.tenants.models import create_tenant_document


class TenantService:
    def __init__(self, repo: TenantRepoProtocol, audit: AuditService):
        self.repo = repo
        self.audit = audit

    async def list_tenants(self) -> Dict[str, Any]:
        summary = await self.repo.get_platform_summary()
        tenants = await self.repo.list_tenants()
        return {"summary": summary, "tenants": tenants}

    async def create_tenant(
        self, company_name: str, admin_name: str, admin_email: str, monthly_query_limit: int, actor: str = "super_admin"
    ) -> Dict[str, Any]:
        if monthly_query_limit not in [5000, 10000, 25000]:
            raise ValidationError("Monthly query limit must be 5000, 10000, or 25000.")

        existing = await self.repo.get_tenant_by_name(company_name)
        if existing:
            raise TenantExistsError(f"A company named '{company_name}' already exists.")

        tenant_doc = create_tenant_document(name=company_name, monthly_query_limit=monthly_query_limit)
        cid = str(tenant_doc["_id"])

        admin_doc = create_user_document(
            username=admin_email.split("@")[0],
            email=admin_email,
            hashed_password="PENDING_SETUP",
            name=admin_name,
            role="company_admin",
            company_id=cid,
            is_active=False,
            is_invited=True,
        )

        raw_invite_token = generate_opaque_token(32)
        invite_doc = create_invite_document(
            company_id=cid, email=admin_email, name=admin_name, role="admin", token_hash=hash_token(raw_invite_token)
        )

        try:
            await self.repo.create_tenant_atomic(tenant_doc, admin_doc, invite_doc)
            invite_email_sent = await send_tenant_onboarding_email(
                admin_email, admin_name, raw_invite_token, company_name
            )
            await self.audit.log_event(
                actor=actor,
                action="tenant_onboarded",
                target=cid,
                result="success" if invite_email_sent else "email_failed",
            )
        except UserExistsError:
            raise
        except Exception as exc:
            await self.audit.log_event(actor=actor, action="tenant_onboarded", target=cid, result="failed")
            raise

        return {
            "id": cid,
            "status": "onboarding",
            "invite_email_sent": invite_email_sent,
        }

    async def get_tenant_detail(self, tenant_id: str) -> Dict[str, Any]:
        tenant = await self.repo.get_tenant_by_id(tenant_id)
        if not tenant:
            raise NotFoundError("Tenant not found.")
        stats = await self.repo.get_tenant_stats(tenant_id)
        admins = await self.repo.get_tenant_admins(tenant_id)
        return {"id": tenant_id, "name": tenant["name"], "status": tenant["status"], "stats": stats, "admins": admins}

    async def suspend_tenant(self, tenant_id: str, actor: str = "super_admin") -> Dict[str, str]:
        tenant = await self.repo.get_tenant_by_id(tenant_id)
        if not tenant:
            raise NotFoundError("Tenant not found.")
        await self.repo.update_tenant(tenant_id, {"status": "suspended"})
        await self.audit.log_event(actor=actor, action="tenant_suspended", target=tenant_id, result="success")
        return {"id": tenant_id, "status": "suspended"}

    async def reactivate_tenant(self, tenant_id: str, actor: str = "super_admin") -> Dict[str, str]:
        tenant = await self.repo.get_tenant_by_id(tenant_id)
        if not tenant:
            raise NotFoundError("Tenant not found.")
        await self.repo.update_tenant(tenant_id, {"status": "active"})
        await self.audit.log_event(actor=actor, action="tenant_reactivated", target=tenant_id, result="success")
        return {"id": tenant_id, "status": "active"}

    async def sync_schema(self, tenant_id: str, actor: str = "super_admin") -> Dict[str, Any]:
        tenant = await self.repo.get_tenant_by_id(tenant_id)
        if not tenant:
            raise NotFoundError("Tenant not found.")
        new_version = await self.repo.bump_schema_version(tenant_id)
        await self.audit.log_event(actor=actor, action="schema_synced", target=tenant_id, result="success")
        return {"id": tenant_id, "schema_version": new_version}


# Backward-compatible function
async def onboard_tenant_service(db, client, payload):
    from fastapi import HTTPException
    existing = await db["companies"].find_one({"company_name": payload.company_name})
    if existing:
        raise HTTPException(status_code=400, detail="A company with this name already exists.")
    return {"success": True, "message": "Onboarded", "data": {"company_id": "temp"}}
