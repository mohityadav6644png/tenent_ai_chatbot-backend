"""
Integration Tests: Users and Invites (tests/integration/test_users_invites.py)
Tests company-admin scoped user operations, LAST_ADMIN guard, and cross-company 404 isolation.
"""

import pytest
from app.core.exceptions import LastAdminError, NotFoundError, ValidationError
from app.modules.users.service import UserService


class MockUserRepo:
    def __init__(self):
        self.users = [
            {"_id": "u_admin1", "company_id": "c1", "email": "admin1@c1.com", "role": "admin", "is_active": True},
            {"_id": "u_emp1", "company_id": "c1", "email": "emp1@c1.com", "role": "employee", "is_active": True},
            {"_id": "u_other2", "company_id": "c2", "email": "admin@c2.com", "role": "admin", "is_active": True},
        ]
        self.invites = [
            {"_id": "inv_1", "company_id": "c1", "email": "pending@c1.com", "name": "Pending", "role": "employee", "status": "pending"}
        ]

    async def list_company_users(self, cid):
        return [u for u in self.users if u["company_id"] == cid]

    async def list_company_pending_invites(self, cid):
        return [i for i in self.invites if i["company_id"] == cid and i["status"] == "pending"]

    async def get_company_user_by_id(self, cid, uid):
        for u in self.users:
            if u["_id"] == uid and u["company_id"] == cid:
                return u
        return None

    async def count_company_active_admins(self, cid):
        return len([u for u in self.users if u["company_id"] == cid and u["role"] == "admin" and u["is_active"]])

    async def deactivate_user(self, uid):
        for u in self.users:
            if u["_id"] == uid:
                u["is_active"] = False

    async def revoke_user_sessions(self, uid):
        pass


@pytest.mark.asyncio
async def test_last_admin_guard():
    repo = MockUserRepo()
    svc = UserService(repo)

    # Deactivating self is rejected
    with pytest.raises(ValidationError):
        await svc.deactivate_user("c1", "u_admin1", "u_admin1")

    # Deactivating the only admin in company c1 raises LastAdminError
    with pytest.raises(LastAdminError):
        await svc.deactivate_user("c1", "u_admin1", "caller_other")


@pytest.mark.asyncio
async def test_cross_company_returns_404():
    repo = MockUserRepo()
    svc = UserService(repo)

    # Admin of company c1 trying to act on user of company c2 gets 404 (NotFoundError), NOT 403
    with pytest.raises(NotFoundError):
        await svc.deactivate_user(company_id="c1", target_user_id="u_other2", current_user_id="u_admin1")
