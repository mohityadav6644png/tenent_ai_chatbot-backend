"""
Integration Tests: Dataset Understanding & Multi-Tenant Chatbot Answering
(tests/integration/test_dataset_understanding.py)

Tests real MongoDB with FakeSQLGenerator:
1) Tenant A imports customers (customer_id, name, city, region, email, phone).
2) The summary has row_count, name, city, region (with real region values) and NOT email or phone.
3) The pipeline validator rejects any pipeline referencing email or phone.
4) A count-by-region question through the real MongoExecutor returns only Tenant A's data.
5) Tenant B (no data uploaded) returns the friendly "no data uploaded" reply without calling the AI.
6) Tenant B's summary has 0 rows and never contains Tenant A's values.
7) Re-importing updates the row_count and clears the cache.
8) A CSV with a company_id column does NOT override the tenant context.
"""

import json
import uuid
import pytest
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.core.exceptions import SqlValidationFailedError
from app.infrastructure.azure.fake_sql_generator import FakeSQLGenerator
from app.infrastructure.azure.redis_cache import InMemoryCache, build_cache_key
from app.infrastructure.db.catalog_repo import SchemaCatalogRepo
from app.infrastructure.db.company_repo import (
    MongoDataImportRepository,
    MongoDataIngestionRepository,
)
from app.infrastructure.db.executor import MongoExecutor
from app.infrastructure.db.repositories import MongoTenantRepository
from app.modules.company.service import CompanyService
from app.modules.company.summary import build_dataset_summary, get_tenant_summaries
from app.modules.chat.service import QueryService
from app.modules.sql_guard.validator import validate_and_format_sql


TENANT_A_ID = "aaaa1111-1111-1111-1111-111111111111"
TENANT_B_ID = "bbbb2222-2222-2222-2222-222222222222"
ADMIN_USER_A = "admin_user_a"
ADMIN_USER_B = "admin_user_b"


class MockChatRepo:
    def __init__(self, catalog_repo):
        self.catalog_repo = catalog_repo
        self.monthly_usage = 0

    async def check_and_increment_rate_limits(self, company_id: str, user_id: str):
        pass

    async def get_schema_catalog(self, company_id: str):
        return await self.catalog_repo.get_catalog(company_id)

    async def increment_monthly_usage(self, company_id: str, tokens: int = 150):
        self.monthly_usage += tokens

    async def save_conversation(self, doc):
        return uuid.uuid4().hex

    async def save_message(self, doc):
        pass

    async def list_user_conversations(self, user_id: str, company_id: str):
        return []


class TrackingGenerator:
    """Wraps FakeSQLGenerator and records whether generate() was called."""

    def __init__(self):
        self.delegate = FakeSQLGenerator()
        self.call_count = 0

    async def generate(self, question, catalog, history, company_id):
        self.call_count += 1
        return await self.delegate.generate(question, catalog, history, company_id)


@pytest.mark.asyncio
async def test_tenant_a_import_and_dataset_understanding_flow():
    # Create isolated clients bound strictly to current test event loop
    test_app_client = AsyncMongoClient(settings.MONGODB_URL, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
    test_writer_client = AsyncMongoClient(settings.MONGODB_BUSINESS_URL_WRITER, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
    test_reader_client = AsyncMongoClient(settings.MONGODB_BUSINESS_URL_READER, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)

    app_db = test_app_client[settings.MONGODB_DB]
    writer_db = test_writer_client[settings.MONGODB_BUSINESS_DB]
    reader_db = test_reader_client[settings.MONGODB_BUSINESS_DB]

    coll_names = ["customers", "orders", "products", "invoices", "stock_levels"]
    try:
        # Clean test tenants
        for name in coll_names:
            await writer_db[name].delete_many({"company_id": {"$in": [TENANT_A_ID, TENANT_B_ID]}})
        await app_db["dataset_catalog"].delete_many({"company_id": {"$in": [TENANT_A_ID, TENANT_B_ID]}})
        await app_db["data_imports"].delete_many({"company_id": {"$in": [TENANT_A_ID, TENANT_B_ID]}})

        cache = InMemoryCache()
        company_service = CompanyService(
            ingestion_repo=MongoDataIngestionRepository(writer_db=writer_db, reader_db=reader_db),
            tracking_repo=MongoDataImportRepository(db=app_db),
            tenant_repo=MongoTenantRepository(),
            cache=cache,
        )

        # 1. Tenant A imports customers CSV (with customer_id, name, city, region, email, phone)
        csv_data = (
            "customer_id,name,city,region,email,phone\n"
            "CUST-001,Customer Alpha,Pune,West,alpha@test.com,+919876543210\n"
            "CUST-002,Customer Beta,Noida,North,beta@test.com,+919876543211\n"
            "CUST-003,Customer Gamma,Chennai,South,gamma@test.com,+919876543212\n"
        ).encode("utf-8")

        result = await company_service.import_dataset(
            company_id=TENANT_A_ID,
            user_id=ADMIN_USER_A,
            dataset_key="customers",
            content_bytes=csv_data,
            filename="customers.csv",
            mode="upsert",
        )
        assert result["status"] == "completed"
        assert result["inserted"] == 3

        # 2. Check summary has row_count, name, city, region (with its values) and NOT email or phone
        summary = await build_dataset_summary(TENANT_A_ID, "customers", reader_db=reader_db, app_db=app_db)
        assert summary["row_count"] == 3
        field_map = {f["name"]: f for f in summary["fields"]}
        assert "region" in field_map
        assert field_map["region"]["queryable"] is True
        assert set(field_map["region"]["distinct_values"]) == {"West", "North", "South"}
        assert "name" in field_map
        assert "city" in field_map
        # Email and phone must NOT be queryable
        assert field_map["email"]["queryable"] is False
        assert field_map["phone"]["queryable"] is False
        assert "distinct_values" not in field_map["email"]
        assert "distinct_values" not in field_map["phone"]

        # 3. Pipeline validator rejects any pipeline that references email or phone
        with pytest.raises(SqlValidationFailedError):
            validate_and_format_sql({
                "collection": "customers",
                "pipeline": [{"$project": {"_id": 0, "name": 1, "email": 1}}],
            })

        with pytest.raises(SqlValidationFailedError):
            validate_and_format_sql({
                "collection": "customers",
                "pipeline": [{"$match": {"phone": "+919876543210"}}],
            })

        with pytest.raises(SqlValidationFailedError):
            validate_and_format_sql({
                "collection": "customers",
                "pipeline": [{"$group": {"_id": "$email", "count": {"$sum": 1}}}],
            })

        # 4. MongoExecutor query on region returns only Tenant A's data
        executor = MongoExecutor(db=reader_db)
        catalog_repo = SchemaCatalogRepo(app_db=app_db, reader_db=reader_db)
        query_spec = {
            "collection": "customers",
            "pipeline": [
                {"$group": {"_id": "$region", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$project": {"_id": 0, "region": "$_id", "count": 1}},
            ],
        }
        norm_spec, cols = validate_and_format_sql(query_spec)
        col_defs, rows, trunc = await executor.execute_query(norm_spec, TENANT_A_ID, columns=cols)
        assert len(rows) == 3
        regions_found = {r[0] for r in rows}
        assert regions_found == {"West", "North", "South"}

        # 5. Tenant B (no data uploaded) returns friendly "no data uploaded" reply without calling AI
        tracking_gen = TrackingGenerator()
        query_service = QueryService(
            executor=executor,
            generator=tracking_gen,
            repo=MockChatRepo(catalog_repo),
            cache=cache,
        )
        ans_b = await query_service.answer(
            company_id=TENANT_B_ID,
            user_id=ADMIN_USER_B,
            question="How many customers are in each region?",
            history=[],
        )
        assert ans_b["status"] == "refused"
        assert "No data has been uploaded for your company yet" in ans_b["answer"]["detail"]
        assert tracking_gen.call_count == 0, "AI generator must NOT be called when tenant has no data"

        # 6. Tenant B's summary has 0 rows and never contains Tenant A's values
        summaries_b = await get_tenant_summaries(TENANT_B_ID, app_db=app_db, reader_db=reader_db)
        for s in summaries_b:
            assert s["row_count"] == 0
            for f in s.get("fields", []):
                assert f.get("distinct_values") is None

        # 7. Re-importing updates the row_count and clears the cache
        cache_key = build_cache_key(TENANT_A_ID, 1, "test question")
        await cache.set(cache_key, json.dumps({"cached": True}))
        assert await cache.get(cache_key) is not None

        csv_data_updated = (
            "customer_id,name,city,region,email,phone\n"
            "CUST-004,Customer Delta,Kolkata,East,delta@test.com,+919876543213\n"
        ).encode("utf-8")
        await company_service.import_dataset(
            company_id=TENANT_A_ID,
            user_id=ADMIN_USER_A,
            dataset_key="customers",
            content_bytes=csv_data_updated,
            filename="customers_delta.csv",
            mode="upsert",
        )
        # Check cache is cleared
        assert await cache.get(cache_key) is None
        # Check row count updated
        updated_summary = await build_dataset_summary(TENANT_A_ID, "customers", reader_db=reader_db, app_db=app_db)
        assert updated_summary["row_count"] == 4

        # 8. A CSV with a company_id column does NOT override the tenant context
        hostile_csv = (
            "company_id,customer_id,name,city,region,email,phone\n"
            "MALICIOUS_TENANT,CUST-005,Customer Hostile,Bhopal,Central,h@test.com,+919876543299\n"
        ).encode("utf-8")
        await company_service.import_dataset(
            company_id=TENANT_A_ID,
            user_id=ADMIN_USER_A,
            dataset_key="customers",
            content_bytes=hostile_csv,
            filename="hostile.csv",
            mode="upsert",
        )
        # Verify in real MongoDB that company_id was forced to TENANT_A_ID
        hostile_doc = await reader_db["customers"].find_one({"name": "Customer Hostile"})
        assert hostile_doc is not None
        assert hostile_doc["company_id"] == TENANT_A_ID
        assert hostile_doc["company_id"] != "MALICIOUS_TENANT"

    finally:
        for name in coll_names:
            await writer_db[name].delete_many({"company_id": {"$in": [TENANT_A_ID, TENANT_B_ID]}})
        await app_db["dataset_catalog"].delete_many({"company_id": {"$in": [TENANT_A_ID, TENANT_B_ID]}})
        await app_db["data_imports"].delete_many({"company_id": {"$in": [TENANT_A_ID, TENANT_B_ID]}})
        await test_app_client.close()
        await test_writer_client.close()
        await test_reader_client.close()
