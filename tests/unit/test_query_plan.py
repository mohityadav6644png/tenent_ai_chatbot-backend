"""
Unit Tests for Query Plan, Translator, Text Search Fallback, and Answer Writer
(tests/unit/test_query_plan.py)
"""

import json
from unittest.mock import AsyncMock, MagicMock
import pytest
from pydantic import ValidationError

from app.core.exceptions import SqlValidationFailedError
from app.infrastructure.db.catalog_repo import MONGO_BUSINESS_CATALOG
from app.modules.chat.answer_writer import sanitize_for_writer
from app.modules.chat.plan_translator import translate_plan_to_query
from app.modules.chat.query_plan import (
    QueryPlan,
    QueryPlanDateBucket,
    QueryPlanFilter,
    QueryPlanMetric,
    QueryPlanSort,
)
from app.modules.chat.service import QueryService
from app.modules.sql_guard.validator import validate_and_format_sql


def test_query_plan_model_validation():
    # Valid plan
    plan = QueryPlan(
        decision="answer",
        collection="orders",
        filters=[
            QueryPlanFilter(field="status", op="eq", value=json.dumps("completed")),
            QueryPlanFilter(field="total", op="gt", value=json.dumps(1000)),
        ],
        group_by=["region"],
        metrics=[QueryPlanMetric(fn="sum", field="total", **{"as": "total_sales"})],
        sort=[QueryPlanSort(field="total_sales", dir="desc")],
        limit=10,
    )
    assert plan.decision == "answer"
    assert len(plan.filters) == 2
    assert plan.group_by == ["region"]
    assert plan.metrics[0].as_name == "total_sales"

    # Refusal decision
    refusal = QueryPlan(decision="refuse")
    assert refusal.decision == "refuse"

    # Clarify decision
    clarify = QueryPlan(
        decision="clarify",
        clarification_question="Which region?",
        options=["North", "South"],
    )
    assert clarify.decision == "clarify"
    assert len(clarify.options) == 2


def test_plan_translator_aggregation():
    plan = QueryPlan(
        decision="answer",
        collection="orders",
        filters=[QueryPlanFilter(field="status", op="eq", value=json.dumps("completed"))],
        group_by=["region"],
        metrics=[QueryPlanMetric(fn="sum", field="total", **{"as": "total_sales"})],
        sort=[QueryPlanSort(field="total_sales", dir="desc")],
        limit=5,
    )
    query_spec, cols = translate_plan_to_query(plan, MONGO_BUSINESS_CATALOG)
    assert query_spec["collection"] == "orders"
    assert cols == ["region", "total_sales"]

    # Verify through defense-in-depth validator
    norm_spec, val_cols = validate_and_format_sql(query_spec)
    assert val_cols == ["region", "total_sales"]
    assert norm_spec["pipeline"][-1]["$project"]["_id"] == 0


def test_plan_translator_date_bucket():
    plan = QueryPlan(
        decision="answer",
        collection="orders",
        date_bucket=QueryPlanDateBucket(field="order_date", unit="month"),
        metrics=[QueryPlanMetric(fn="sum", field="total", **{"as": "monthly_sales"})],
        limit=6,
    )
    query_spec, cols = translate_plan_to_query(plan, MONGO_BUSINESS_CATALOG)
    assert query_spec["collection"] == "orders"
    assert "order_date" in cols
    assert "monthly_sales" in cols

    norm_spec, val_cols = validate_and_format_sql(query_spec)
    assert "order_date" in val_cols


def test_plan_translator_contains_and_select():
    plan = QueryPlan(
        decision="answer",
        collection="products",
        filters=[QueryPlanFilter(field="name", op="contains", value=json.dumps("Basmati"))],
        select=["name", "category", "price"],
        limit=10,
    )
    query_spec, cols = translate_plan_to_query(plan, MONGO_BUSINESS_CATALOG)
    assert cols == ["name", "category", "price"]

    # Ensure regex is escaped
    match_stage = query_spec["pipeline"][0]["$match"]
    assert match_stage["name"]["$regex"] == "Basmati"

    norm_spec, val_cols = validate_and_format_sql(query_spec)
    assert val_cols == ["name", "category", "price"]


def test_plan_translator_rejects_unknown_collection_or_field():
    # Unknown collection
    bad_col = QueryPlan(decision="answer", collection="secret_data")
    with pytest.raises(SqlValidationFailedError):
        translate_plan_to_query(bad_col, MONGO_BUSINESS_CATALOG)

    # Unknown field
    bad_field = QueryPlan(
        decision="answer",
        collection="orders",
        filters=[QueryPlanFilter(field="unknown_col", op="eq", value=json.dumps("val"))],
    )
    with pytest.raises(SqlValidationFailedError):
        translate_plan_to_query(bad_field, MONGO_BUSINESS_CATALOG)


def test_plan_translator_rejects_type_mismatches():
    # 'gt' operator on non-numeric/non-date field
    bad_op = QueryPlan(
        decision="answer",
        collection="products",
        filters=[QueryPlanFilter(field="category", op="gt", value=json.dumps("Grains"))],
    )
    with pytest.raises(SqlValidationFailedError):
        translate_plan_to_query(bad_op, MONGO_BUSINESS_CATALOG)

    # 'contains' operator on numeric field
    bad_contains = QueryPlan(
        decision="answer",
        collection="products",
        filters=[QueryPlanFilter(field="price", op="contains", value=json.dumps(100))],
    )
    with pytest.raises(SqlValidationFailedError):
        translate_plan_to_query(bad_contains, MONGO_BUSINESS_CATALOG)


def test_answer_writer_sanitizer():
    columns = [
        {"name": "customer_name", "type": "string"},
        {"name": "user_email", "type": "string"},
        {"name": "password_hash", "type": "string"},
        {"name": "total_spent", "type": "number"},
    ]
    rows = [
        ["Asha Patel", "asha@acme.in", "secret123", 45000],
        ["Rohan Sharma", "rohan@acme.in", "secret456", 32000],
    ]

    safe_cols, safe_rows = sanitize_for_writer(columns, rows, max_rows=5, max_chars_per_val=50)

    # Sensitive columns stripped
    assert "user_email" not in safe_cols
    assert "password_hash" not in safe_cols
    assert safe_cols == ["customer_name", "total_spent"]

    # Sensitive row values stripped
    assert safe_rows[0] == ["Asha Patel", 45000]
    assert safe_rows[1] == ["Rohan Sharma", 32000]


@pytest.mark.asyncio
async def test_query_service_plan_caching_and_retranslation():
    executor = AsyncMock()
    executor.execute_query.return_value = (
        [{"name": "total_sales", "type": "number"}],
        [[50000]],
        False,
    )
    generator = AsyncMock()
    repo = AsyncMock()
    repo.get_schema_catalog.return_value = MONGO_BUSINESS_CATALOG
    cache = AsyncMock()

    # Cached plan JSON
    cached_plan = {
        "plan": {
            "decision": "answer",
            "collection": "orders",
            "filters": [{"field": "status", "op": "eq", "value": "\"completed\""}],
            "metrics": [{"fn": "sum", "field": "total", "as": "total_sales"}],
            "limit": 1,
        },
        "headline_template": "Total Sales: {row1.total_sales}",
        "detail_template": "Summary",
    }
    cache.get.return_value = json.dumps(cached_plan)

    service = QueryService(
        executor=executor,
        generator=generator,
        repo=repo,
        cache=cache,
    )

    res = await service.answer(
        company_id="11111111-1111-1111-1111-111111111111",
        user_id="user1",
        question="What is total sales?",
        history=[],
    )

    assert res["status"] == "success"
    assert res["meta"]["cache"] == "hit"
    assert res["answer"]["headline"] == "Total Sales: 50000"
    # Generator was NOT called on cache hit
    generator.generate.assert_not_called()
    # Executor was called with translated & validated pipeline
    assert executor.execute_query.called


@pytest.mark.asyncio
async def test_text_search_fallback():
    executor = AsyncMock()
    generator = AsyncMock()
    generator.generate.return_value = {"decision": "text_search"}
    repo = AsyncMock()
    repo.get_schema_catalog.return_value = MONGO_BUSINESS_CATALOG
    cache = AsyncMock()
    cache.get.return_value = None

    search_repo = AsyncMock()
    search_repo.search_records.return_value = [
        {"title": "Return Policy", "snippet": "Items can be returned within 30 days."}
    ]

    service = QueryService(
        executor=executor,
        generator=generator,
        repo=repo,
        cache=cache,
        record_search_repo=search_repo,
    )

    res = await service.answer(
        company_id="11111111-1111-1111-1111-111111111111",
        user_id="user1",
        question="What is our return policy?",
        history=[],
    )

    assert res["status"] == "success"
    assert res["meta"]["source"] == "records"
    assert res["answer"]["columns"][0]["name"] == "title"
    assert res["answer"]["rows"][0][0] == "Return Policy"
