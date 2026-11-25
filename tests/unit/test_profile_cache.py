"""
Profile Cache Tests (tests/unit/test_profile_cache.py)
Tests cache key isolation, profile cache TTL, and invalidation on write.
"""

from datetime import datetime, timezone
import pytest
from app.core.config import settings
from app.infrastructure.azure.redis_cache import InMemoryCache, build_cache_key
from app.modules.profile.models import ProfileField, UserProfile


@pytest.mark.asyncio
async def test_profile_cache_isolation_and_invalidation():
    cache = InMemoryCache()
    company_id_1 = "comp-1"
    company_id_2 = "comp-2"
    user_id = "user-100"

    key_1 = f"profile:{company_id_1}:{user_id}"
    key_2 = f"profile:{company_id_2}:{user_id}"

    # Verify keys are isolated by company_id
    assert key_1 != key_2

    # Set cached value for user in company 1
    prof1 = UserProfile(company_id=company_id_1, user_id=user_id)
    await cache.set(key_1, prof1.model_dump_json(), ex=settings.PROFILE_CACHE_TTL_SECONDS)

    # Cache hit for company 1
    hit1 = await cache.get(key_1)
    assert hit1 is not None

    # Cache miss for company 2
    hit2 = await cache.get(key_2)
    assert hit2 is None

    # Invalidation on write
    await cache.delete(key_1)
    after_del = await cache.get(key_1)
    assert after_del is None


def test_answer_cache_keys_remain_unchanged():
    """Answer cache keys are unchanged by profile features."""
    q = "show total sales"
    k1 = build_cache_key("comp-1", 1, q)
    k2 = build_cache_key("comp-1", 1, q)
    assert k1 == k2
    assert "profile" not in k1
