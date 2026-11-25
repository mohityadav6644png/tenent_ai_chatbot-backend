"""
Multi-document Synthesis & Isolation Integration Tests (tests/integration/test_multidoc_integration.py)
Tests multi-doc querying across real MongoDB collections, join key detection,
tenant isolation, column collision prefixing, and cache invalidation.
"""

from datetime import datetime, timezone
import pytest
import pytest_asyncio
from bson import ObjectId

from app.core.cache import invalidate_company_cache
from app.infrastructure.azure.fake_sql_generator import FakeSQLGenerator
from app.infrastructure.azure.redis_cache import InMemoryCache
from app.infrastructure.db.chat_repo import MongoChatRepository
from app.infrastructure.db.executor import MongoExecutor
from app.infrastructure.db.mongodb import (
    app_database,
    business_database_writer,
    tenants_collection,
    users_collection,
)
from app.infrastructure.db.multidoc_repository import MongoMultiDocRepository
from app.infrastructure.multidoc_planner.fake_planner import FakeMultiDocPlanner
from app.modules.chat.service import QueryService
from app.modules.multidoc.service import MultiDocService

TENANT_MD_A = "md_tenant_aaaa_1111"
TENANT_MD_B = "md_tenant_bbbb_2222"
USER_A = "md_user_a"
USER_B = "md_user_b"


@pytest_asyncio.fixture
async def setup_multidoc_data():
    now = datetime.now(timezone.utc)

    # 1. Setup tenants
    await tenants_collection.update_one(
        {"_id": TENANT_MD_A},
        {"$set": {"name": "Tenant A Corp", "schema_version": 1, "status": "active"}},
        upsert=True,
    )
    await tenants_collection.update_one(
        {"_id": TENANT_MD_B},
        {"$set": {"name": "Tenant B Corp", "schema_version": 1, "status": "active"}},
        upsert=True,
    )

    # 2. Cleanup prior test data
    await app_database["company_files"].delete_many(
        {"company_id": {"$in": [TENANT_MD_A, TENANT_MD_B]}}
    )
    await business_database_writer["company_rows"].delete_many(
        {"company_id": {"$in": [TENANT_MD_A, TENANT_MD_B]}}
    )
    await invalidate_company_cache(TENANT_MD_A)
    await invalidate_company_cache(TENANT_MD_B)

    # 3. Create Tenant A Doc 1: Products
    doc1_id = ObjectId()
    await app_database["company_files"].insert_one({
        "_id": doc1_id,
        "company_id": TENANT_MD_A,
        "name": "Product Catalog",
        "slug": "product_catalog",
        "status": "ready",
        "row_count": 2,
        "columns": [
            {"key": "product_id", "label": "Product ID", "type": "text", "hidden": False},
            {"key": "product_name", "label": "Product Name", "type": "text", "hidden": False},
            {"key": "cost_price", "label": "Cost Price", "type": "number", "hidden": True},
        ],
        "created_at": now,
        "updated_at": now,
    })
    await business_database_writer["company_rows"].insert_many([
        {
            "company_id": TENANT_MD_A,
            "file_id": str(doc1_id),
            "data": {"product_id": "P-101", "product_name": "Ultra Laptop", "cost_price": 500},
            "created_at": now,
        },
        {
            "company_id": TENANT_MD_A,
            "file_id": str(doc1_id),
            "data": {"product_id": "P-102", "product_name": "Wireless Mouse", "cost_price": 15},
            "created_at": now,
        },
    ])

    # 4. Create Tenant A Doc 2: Inventory
    doc2_id = ObjectId()
    await app_database["company_files"].insert_one({
        "_id": doc2_id,
        "company_id": TENANT_MD_A,
        "name": "Inventory Levels",
        "slug": "inventory_levels",
        "status": "ready",
        "row_count": 2,
        "columns": [
            {"key": "product_id", "label": "Product ID", "type": "text", "hidden": False},
            {"key": "stock_qty", "label": "Stock Qty", "type": "number", "hidden": False},
            {"key": "warehouse", "label": "Warehouse", "type": "text", "hidden": False},
        ],
        "created_at": now,
        "updated_at": now,
    })
    await business_database_writer["company_rows"].insert_many([
        {
            "company_id": TENANT_MD_A,
            "file_id": str(doc2_id),
            "data": {"product_id": "P-101", "stock_qty": 45, "warehouse": "West Coast"},
            "created_at": now,
        },
        {
            "company_id": TENANT_MD_A,
            "file_id": str(doc2_id),
            "data": {"product_id": "P-102", "stock_qty": 200, "warehouse": "East Coast"},
            "created_at": now,
        },
    ])

    # 5. Create Tenant B Doc: Top Secret
    doc_b_id = ObjectId()
    await app_database["company_files"].insert_one({
        "_id": doc_b_id,
        "company_id": TENANT_MD_B,
        "name": "Secret Tenant B Financials",
        "slug": "secret_financials",
        "status": "ready",
        "row_count": 1,
        "columns": [
            {"key": "product_id", "label": "Product ID", "type": "text", "hidden": False},
            {"key": "revenue_b", "label": "Revenue B", "type": "number", "hidden": False},
        ],
        "created_at": now,
        "updated_at": now,
    })
    await business_database_writer["company_rows"].insert_one({
        "company_id": TENANT_MD_B,
        "file_id": str(doc_b_id),
        "data": {"product_id": "P-101", "revenue_b": 999999},
        "created_at": now,
    })

    yield

    # Cleanup
    await app_database["company_files"].delete_many(
        {"company_id": {"$in": [TENANT_MD_A, TENANT_MD_B]}}
    )
    await business_database_writer["company_rows"].delete_many(
        {"company_id": {"$in": [TENANT_MD_A, TENANT_MD_B]}}
    )
    await invalidate_company_cache(TENANT_MD_A)
    await invalidate_company_cache(TENANT_MD_B)


@pytest.mark.asyncio
async def test_multidoc_query_execution_and_isolation(setup_multidoc_data):
    md_repo = MongoMultiDocRepository()
    planner = FakeMultiDocPlanner()
    cache = InMemoryCache()
    md_service = MultiDocService(repo=md_repo, planner=planner, cache=cache)

    executor = MongoExecutor()
    generator = FakeSQLGenerator()
    chat_repo = MongoChatRepository()

    query_service = QueryService(
        executor=executor,
        generator=generator,
        repo=chat_repo,
        cache=cache,
        multidoc_service=md_service,
    )

    # 1. Ask cross-document question for Tenant A
    res = await query_service.answer(
        company_id=TENANT_MD_A,
        user_id=USER_A,
        question="Combine Product Catalog and Inventory Levels across products",
        history=[],
    )

    assert res["status"] == "success"
    assert res["multidoc_meta"] is not None
    assert res["multidoc_meta"]["used_multi_doc"] is True
    assert "Product Catalog" in res["multidoc_meta"]["source_names"]
    assert "Inventory Levels" in res["multidoc_meta"]["source_names"]
    assert "product_id" in res["multidoc_meta"]["join_keys_used"]

    # Verify merged columns
    cols = [c["name"] for c in res["answer"]["columns"]]
    assert "product_id" in cols
    assert "product_name" in cols
    assert "stock_qty" in cols
    # Verify hidden column 'cost_price' was filtered out
    assert "cost_price" not in cols

    # Verify rows contain merged data
    rows = res["answer"]["rows"]
    assert len(rows) == 2
    p101_row = next(r for r in rows if r[cols.index("product_id")] == "P-101")
    assert p101_row[cols.index("product_name")] == "Ultra Laptop"
    assert p101_row[cols.index("stock_qty")] == 45

    # 2. Verify Cache Hit on identical question
    cached_res = await query_service.answer(
        company_id=TENANT_MD_A,
        user_id=USER_A,
        question="Combine Product Catalog and Inventory Levels across products",
        history=[],
    )
    assert cached_res["meta"]["cache"] == "hit"
    assert cached_res["multidoc_meta"]["used_multi_doc"] is True

    # 3. Verify Cache Invalidation
    await invalidate_company_cache(TENANT_MD_A)
    invalidated_res = await query_service.answer(
        company_id=TENANT_MD_A,
        user_id=USER_A,
        question="Combine Product Catalog and Inventory Levels across products",
        history=[],
    )
    assert invalidated_res["meta"]["cache"] == "miss"

    # 4. Security & Tenant Isolation:
    # Tenant A must NEVER see or merge Tenant B's data
    for row in rows:
        assert "revenue_b" not in cols
        for cell in row:
            assert cell != 999999

    # Tenant B queries its own data; should not see Tenant A
    docs_b = await md_repo.list_documents(TENANT_MD_B)
    assert len(docs_b) == 1
    assert docs_b[0].source_name == "Secret Tenant B Financials"
    assert all("Product Catalog" != d.source_name for d in docs_b)
