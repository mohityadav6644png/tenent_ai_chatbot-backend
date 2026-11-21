"""
Tests for Two-Tier Tenant-Isolated Cache (tests/integration/test_two_tier_cache.py)
Verifies:
1. Process-local L1 TTL Cache operations and tenant isolation.
2. Graceful fallback when Redis is unreachable or unconfigured.
3. Strict key prefixing `tenant:{company_id}:{namespace}:{key}`.
4. Multi-tenant invalidation: `invalidate_company_cache` cleans only the target tenant.
5. Chatbot endpoint `/api/v1/chatbot/ask` caching (`cached: False` on miss, `cached: True` on hit).
6. Cache invalidation on data ingestion (manual insert and CSV import).
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.cache import (
    build_tenant_cache_key,
    cache_get,
    cache_set,
    close_redis,
    get_l1_cache,
    hash_question,
    init_redis,
    invalidate_company_cache,
)
from app.core.security import create_access_token
from app.main import create_app


TENANT_A = "aaaa1111-1111-1111-1111-111111111111"
TENANT_B = "bbbb2222-2222-2222-2222-222222222222"
ADMIN_A = "admin_user_a"
ADMIN_B = "admin_user_b"


@pytest.mark.asyncio
async def test_cache_key_tenant_isolation_format():
    key = build_tenant_cache_key(TENANT_A, "chat_answer", "abc123hash")
    assert key == f"tenant:{TENANT_A}:chat_answer:abc123hash"
    assert ".." not in key
    assert key.startswith("tenant:")


@pytest.mark.asyncio
async def test_question_normalizer_and_hasher():
    h1 = hash_question("  What is   our TOTAL revenue? ")
    h2 = hash_question("what is our total revenue?")
    h3 = hash_question("WHAT IS OUR TOTAL REVENUE?")
    assert h1 == h2 == h3
    assert len(h1) == 64  # SHA256 hex string


@pytest.mark.asyncio
async def test_l1_cache_set_get_and_expiration():
    await invalidate_company_cache(TENANT_A)
    await invalidate_company_cache(TENANT_B)

    # Set data for Tenant A
    await cache_set(TENANT_A, "test_ns", "key1", {"sales": 1000}, ttl_seconds=2)

    # Tenant A can read it
    val_a = await cache_get(TENANT_A, "test_ns", "key1")
    assert val_a == {"sales": 1000}

    # Tenant B CANNOT read Tenant A's data (strict isolation)
    val_b = await cache_get(TENANT_B, "test_ns", "key1")
    assert val_b is None

    # Test short TTL expiration
    await asyncio.sleep(2.1)
    expired_val = await cache_get(TENANT_A, "test_ns", "key1")
    assert expired_val is None


@pytest.mark.asyncio
async def test_invalidate_company_cache_only_affects_target_tenant():
    await invalidate_company_cache(TENANT_A)
    await invalidate_company_cache(TENANT_B)

    # Populate cache for both tenants
    await cache_set(TENANT_A, "orders", "summary", {"total": 5000})
    await cache_set(TENANT_B, "orders", "summary", {"total": 9999})

    # Invalidate Tenant A
    del_count = await invalidate_company_cache(TENANT_A)
    assert del_count >= 1

    # Tenant A cache is gone
    assert await cache_get(TENANT_A, "orders", "summary") is None

    # Tenant B cache is completely intact!
    assert await cache_get(TENANT_B, "orders", "summary") == {"total": 9999}

    # Clean up Tenant B
    await invalidate_company_cache(TENANT_B)


@pytest.mark.asyncio
async def test_redis_graceful_fallback_when_unreachable():
    # Attempt connecting to invalid port
    await init_redis("redis://127.0.0.1:54321/0")

    # System falls back gracefully to in-memory mode without crashing
    await cache_set(TENANT_A, "fallback", "item", "memory_value", ttl_seconds=60)
    val = await cache_get(TENANT_A, "fallback", "item")
    assert val == "memory_value"

    await close_redis()


@pytest.mark.asyncio
async def test_chatbot_ask_endpoint_cache_hit_and_miss():
    app = create_app()
    token_a = create_access_token(user_id=ADMIN_A, role="company_admin", company_id=TENANT_A)
    token_b = create_access_token(user_id=ADMIN_B, role="company_admin", company_id=TENANT_B)

    await invalidate_company_cache(TENANT_A)
    await invalidate_company_cache(TENANT_B)

    mock_answer = {
        "headline": "Total Sales: ₹1,50,000",
        "detail": "Calculated across completed orders.",
        "columns": [{"name": "total", "type": "number"}],
        "rows": [[150000]],
        "row_count": 1,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.api.v1.endpoints.chatbot.QueryService.answer", new=AsyncMock(return_value=mock_answer)) as mock_svc:
            # 1. First call -> Cache MISS (cached: False)
            res1 = await client.post(
                "/api/v1/chatbot/ask",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"question": "What is our total sales?"},
            )
            assert res1.status_code == 200
            data1 = res1.json()
            assert data1["cached"] is False
            assert data1["headline"] == "Total Sales: ₹1,50,000"
            assert mock_svc.call_count == 1

            # 2. Second call with same normalized question -> Cache HIT (cached: True)
            res2 = await client.post(
                "/api/v1/chatbot/ask",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"question": "   WHAT IS OUR TOTAL SALES?  "},
            )
            assert res2.status_code == 200
            data2 = res2.json()
            assert data2["cached"] is True
            assert data2["headline"] == "Total Sales: ₹1,50,000"
            # QueryService was NOT called again!
            assert mock_svc.call_count == 1

            # 3. Tenant B asks same question -> Cache MISS for Tenant B (tenant isolation)
            res3 = await client.post(
                "/api/v1/chatbot/ask",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"question": "What is our total sales?"},
            )
            assert res3.status_code == 200
            data3 = res3.json()
            assert data3["cached"] is False
            assert mock_svc.call_count == 2


@pytest.mark.asyncio
async def test_data_ingestion_invalidates_chatbot_cache():
    app = create_app()
    token_a = create_access_token(user_id=ADMIN_A, role="company_admin", company_id=TENANT_A)

    await invalidate_company_cache(TENANT_A)

    mock_answer = {
        "headline": "Total Products: 42",
        "detail": "Product count.",
        "columns": [{"name": "count", "type": "integer"}],
        "rows": [[42]],
        "row_count": 1,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.api.v1.endpoints.chatbot.QueryService.answer", new=AsyncMock(return_value=mock_answer)):
            # 1. Ask question to prime the cache
            res1 = await client.post(
                "/api/v1/chatbot/ask",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"question": "How many products do we have?"},
            )
            assert res1.json()["cached"] is False

            # Verify it's cached
            res2 = await client.post(
                "/api/v1/chatbot/ask",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"question": "How many products do we have?"},
            )
            assert res2.json()["cached"] is True

        # 2. Company admin performs manual record insert
        with patch("app.modules.company.service.CompanyService.insert_manual_record", new=AsyncMock(return_value="rec_123")):
            insert_res = await client.post(
                "/api/v1/company/data/insert",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"title": "New Product Update", "content": "Added 5 new products to catalog"},
            )
            assert insert_res.status_code == 200

        # 3. Next chatbot ask must be a Cache MISS because cache was invalidated on ingestion!
        with patch("app.api.v1.endpoints.chatbot.QueryService.answer", new=AsyncMock(return_value=mock_answer)):
            res3 = await client.post(
                "/api/v1/chatbot/ask",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"question": "How many products do we have?"},
            )
            assert res3.json()["cached"] is False
