"""
Users Service (app/modules/users/service.py)
Company admin user operations scoped strictly to the authenticated tenant.
"""

from typing import Any, Dict
from app.core.email import send_invite_email
from app.core.exceptions import (
    InviteExistsError,
    LastAdminError,
    NotFoundError,
    UserExistsError,
    ValidationError,
)
from app.core.security import generate_opaque_token, hash_token
from app.modules.auth.models import create_invite_document
from app.modules.users.interfaces import UserManagementRepoProtocol
from app.modules.users.models import create_tenant_user_document


class UserService:
    def __init__(self, repo: UserManagementRepoProtocol):
        self.repo = repo

    async def list_users(self, company_id: str, current_user_id: str) -> Dict[str, Any]:
        users_raw = await self.repo.list_company_users(company_id)
        invites_raw = await self.repo.list_company_pending_invites(company_id)

        user_items = []
        admin_count = 0

        for u in users_raw:
            role_norm = "admin" if u.get("role") in ["admin", "company_admin"] else "employee"
            if role_norm == "admin" and u.get("is_active", True):
                admin_count += 1
            user_items.append({
                "id": str(u["_id"]),
                "name": u.get("name") or u.get("username") or "User",
                "email": u["email"],
                "role": role_norm,
                "status": "active" if u.get("is_active", True) else "invite_pending",
                "is_self": str(u["_id"]) == current_user_id,
            })

        for inv in invites_raw:
            user_items.append({
                "id": str(inv["_id"]),
                "name": inv["name"],
                "email": inv["email"],
                "role": "admin" if inv.get("role") in ["admin", "company_admin"] else "employee",
                "status": "invite_pending",
                "is_self": False,
            })

        summary = {
            "total_users": len(users_raw) + len(invites_raw),
            "admins": admin_count,
            "pending_invites": len(invites_raw),
        }
        return {"summary": summary, "users": user_items}

    async def create_invite(
        self,
        company_id: str,
        company_name: str,
        name: str,
        email: str,
        role: str,
        inviter_name: str | None = None,
    ) -> Dict[str, str]:
        email_clean = email.strip().lower()
        if await self.repo.get_user_by_email_global(email_clean):
            raise UserExistsError("A user with this email address already exists.")
        if await self.repo.get_pending_invite_by_email(company_id, email_clean):
            raise InviteExistsError("A pending invite for this email already exists.")

        user_role = "company_admin" if role == "admin" else "employee"
        raw_token = generate_opaque_token(32)
        user_doc = create_tenant_user_document(company_id=company_id, name=name, email=email_clean, role=user_role)
        invite_doc = create_invite_document(company_id=company_id, email=email_clean, name=name, role=role, token_hash=hash_token(raw_token))

        invite_id = await self.repo.create_user_and_invite(user_doc, invite_doc)
        await send_invite_email(
            email=email_clean,
            name=name,
            token=raw_token,
            company_name=company_name,
            inviter_name=inviter_name,
        )
        return {"id": invite_id, "status": "invite_pending"}

    async def resend_invite(
        self,
        company_id: str,
        company_name: str,
        invite_id: str,
        inviter_name: str | None = None,
    ) -> Dict[str, str]:
        invite = await self.repo.get_invite_by_id_scoped(company_id, invite_id)
        if not invite:
            raise NotFoundError("Invitation not found.")
        raw_token = generate_opaque_token(32)
        await self.repo.update_invite(invite_id, {"token_hash": hash_token(raw_token)})
        await send_invite_email(
            email=invite["email"],
            name=invite["name"],
            token=raw_token,
            company_name=company_name,
            inviter_name=inviter_name,
        )
        return {"id": invite_id}

    async def delete_invite(self, company_id: str, invite_id: str) -> None:
        invite = await self.repo.get_invite_by_id_scoped(company_id, invite_id)
        if not invite:
            raise NotFoundError("Invitation not found.")
        await self.repo.delete_invite(invite_id)

    async def deactivate_user(self, company_id: str, target_user_id: str, current_user_id: str) -> Dict[str, str]:
        if target_user_id == current_user_id:
            raise ValidationError("You cannot deactivate your own user account.")
        user = await self.repo.get_company_user_by_id(company_id, target_user_id)
        if not user:
            raise NotFoundError("User not found.")
        if user.get("role") in ["admin", "company_admin"]:
            active_admins = await self.repo.count_company_active_admins(company_id)
            if active_admins <= 1:
                raise LastAdminError("Cannot deactivate the last remaining admin in the organization.")
        await self.repo.deactivate_user(target_user_id)
        await self.repo.revoke_user_sessions(target_user_id)
        return {"id": target_user_id, "status": "inactive"}
