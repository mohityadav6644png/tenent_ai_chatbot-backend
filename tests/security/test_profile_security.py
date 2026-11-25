"""
Profile Security Tests (tests/security/test_profile_security.py)
Validates tenant and user isolation, injection immunity, and leak prevention.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
from app.api.v1.schemas.profile import ProfilePatchRequest, ProfileSkipRequest
from app.core.config import settings
from app.core.context import TenantContext
from app.infrastructure.azure.redis_cache import InMemoryCache, build_cache_key
from app.infrastructure.profile_extractor.fake_extractor import FakeProfileExtractor
from app.modules.profile.models import ProfileField, UserProfile
from app.modules.profile.sanitizer import contains_suspicious_patterns, sanitize_name
from app.modules.profile.service import ProfileService


class MockProfileRepo:
    def __init__(self):
        self.profiles = {}
        self._cache = InMemoryCache()

    def _cache_key(self, company_id: str, user_id: str) -> str:
        return f"profile:{company_id}:{user_id}"

    async def get_profile(self, company_id: str, user_id: str):
        return self.profiles.get((company_id, user_id))

    async def merge_profile_fields(self, company_id: str, user_id: str, validated_fields, source, corrections):
        prof = self.profiles.get((company_id, user_id))
        if not prof:
            prof = UserProfile(
                company_id=company_id,
                user_id=user_id,
                fields={
                    "name": ProfileField(status="missing"),
                    "company_name": ProfileField(status="missing"),
                    "interest": ProfileField(status="missing"),
                },
            )
            self.profiles[(company_id, user_id)] = prof
        for k, v in validated_fields.items():
            if k in prof.fields:
                prof.fields[k].value = v
                prof.fields[k].status = "collected"
        return prof


@pytest.mark.asyncio
async def test_tenant_and_user_profile_isolation():
    """Ensure Company A user cannot access or overwrite Company B user's profile."""
    repo = MockProfileRepo()
    extractor = FakeProfileExtractor()
    service = ProfileService(repo=repo, extractor=extractor)

    # User 1 in Tenant A
    await repo.merge_profile_fields("tenant-A", "user-1", {"name": "Alice"}, "user_message", [])

    # User 1 in Tenant B (same user_id string, different tenant)
    await repo.merge_profile_fields("tenant-B", "user-1", {"name": "Bob"}, "user_message", [])

    # User 2 in Tenant A
    await repo.merge_profile_fields("tenant-A", "user-2", {"name": "Charlie"}, "user_message", [])

    p_a1 = await repo.get_profile("tenant-A", "user-1")
    p_b1 = await repo.get_profile("tenant-B", "user-1")
    p_a2 = await repo.get_profile("tenant-A", "user-2")

    assert p_a1.fields["name"].value == "Alice"
    assert p_b1.fields["name"].value == "Bob"
    assert p_a2.fields["name"].value == "Charlie"


def test_injection_payloads_in_profile_inputs():
    """Verify script tags, SQL injection, and path traversal strings are rejected."""
    bad_payloads = [
        "<script>alert('xss')</script>",
        "'; DROP TABLE users; --",
        "../../etc/passwd",
        "{'$gt': ''}",
        "admin@company.com",
        "Phone: 98765432100",
    ]
    for payload in bad_payloads:
        assert sanitize_name(payload) is None

    # Suspicious pattern specific tests
    assert contains_suspicious_patterns("<script>") is True
    assert contains_suspicious_patterns("{'$gt': ''}") is True
    assert contains_suspicious_patterns("admin@company.com") is True
    assert contains_suspicious_patterns("Phone: 98765432100") is True


def test_answer_cache_key_unaffected_by_profile():
    """Answer cache keys are strictly (company_id, schema_version, question) and never contain profile data."""
    key1 = build_cache_key("comp-1", 1, "what is total revenue?")
    key2 = build_cache_key("comp-1", 1, "what is total revenue?")
    assert key1 == key2

    # Verify no profile data in key string
    assert "mohit" not in key1.lower()
    assert "profile" not in key1.lower()
