"""
Integration Tests: MongoDB Multi-Tenant Business Isolation (tests/integration/test_mongo_integration.py)
Tests scope_pipeline choke point, fail-closed executor, reader-writer isolation, and zero cross-tenant leakage.
"""

import pytest
from app.core.exceptions import SqlValidationFailedError, UnauthenticatedError
from app.infrastructure.azure.redis_cache import build_cache_key
from app.infrastructure.db.executor import MongoExecutor, scope_pipeline
from app.modules.sql_guard.validator import validate_and_format_sql

ACME_ID = "11111111-1111-1111-1111-111111111111"
BHARAT_ID = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
async def test_scope_pipeline_injected_first_and_fail_closed():
    # Calling with empty or missing company_id fails closed
    with pytest.raises(UnauthenticatedError):
        scope_pipeline("", [{"$limit": 5}])

    with pytest.raises(UnauthenticatedError):
        scope_pipeline(None, [{"$limit": 5}])

    # Injected $match is always the very first stage
    raw_pipeline = [{"$sort": {"order_date": -1}}, {"$project": {"_id": 0, "total": 1}}]
    scoped = scope_pipeline(ACME_ID, raw_pipeline, max_rows=50)

    assert scoped[0] == {"$match": {"company_id": ACME_ID}}
    assert scoped[-1] == {"$limit": 51}  # max_rows + 1 for truncation check


@pytest.mark.asyncio
async def test_hostile_pipelines_prevent_cross_tenant_leakage():
    # 1. Pipeline attempting unionWith is rejected by validator
    hostile_union = {
        "collection": "orders",
        "pipeline": [
            {"$unionWith": {"coll": "orders", "pipeline": []}},
            {"$project": {"_id": 0, "total": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(hostile_union)

    # 2. Pipeline attempting lookup is rejected
    hostile_lookup = {
        "collection": "orders",
        "pipeline": [
            {"$lookup": {"from": "customers", "localField": "c_id", "foreignField": "id", "as": "c"}},
            {"$project": {"_id": 0, "total": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(hostile_lookup)

    # 3. AI pipeline mentioning company_id is rejected
    hostile_cid = {
        "collection": "orders",
        "pipeline": [
            {"$match": {"company_id": BHARAT_ID}},
            {"$project": {"_id": 0, "total": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(hostile_cid)


def test_writer_credential_not_imported_by_chat_or_report_paths():
    import app.api.v1.endpoints.chat as chat_ep
    import app.api.v1.endpoints.reports as reports_ep
    import app.modules.chat.service as chat_svc
    import app.modules.reports.service as reports_svc

    for mod in [chat_ep, reports_ep, chat_svc, reports_svc]:
        assert not hasattr(mod, "business_writer_client")
        assert not hasattr(mod, "business_database_writer")
        assert not hasattr(mod, "MONGODB_BUSINESS_URL_WRITER")


@pytest.mark.asyncio
async def test_mongo_executor_requires_company_id():
    executor = MongoExecutor()
    query = {
        "collection": "orders",
        "pipeline": [
            {"$match": {"status": "completed"}},
            {"$project": {"_id": 0, "total": 1}},
        ],
    }
    # Fails closed on empty company_id
    with pytest.raises(UnauthenticatedError):
        await executor.execute_query(query, company_id="")

    with pytest.raises(UnauthenticatedError):
        await executor.execute_query(query, company_id="   ")


def test_cache_isolation_across_tenants():
    q = "total completed revenue"
    key_acme = build_cache_key(ACME_ID, 1, q)
    key_bharat = build_cache_key(BHARAT_ID, 1, q)
    assert key_acme != key_bharat
    assert ACME_ID in key_acme
    assert BHARAT_ID in key_bharat

