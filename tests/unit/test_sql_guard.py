"""
Unit Tests: MongoDB Pipeline Validator (tests/unit/test_sql_guard.py)
Tests validation of MongoDB aggregation pipelines, forbidden operators, stage limits, and valid queries.
"""

import pytest
from app.core.exceptions import SqlValidationFailedError
from app.modules.sql_guard.validator import validate_and_format_sql


def test_validator_rejects_forbidden_operators():
    for forbidden in ["$out", "$merge", "$lookup", "$unionWith", "$facet", "$where", "$function"]:
        spec = {
            "collection": "orders",
            "pipeline": [
                {forbidden: "evil_collection"},
                {"$limit": 10},
                {"$project": {"_id": 0, "total": 1}},
            ],
        }
        with pytest.raises(SqlValidationFailedError):
            validate_and_format_sql(spec)


def test_validator_rejects_company_id_reference():
    # 1. In field key
    spec1 = {
        "collection": "orders",
        "pipeline": [
            {"$match": {"company_id": "other-tenant"}},
            {"$limit": 5},
            {"$project": {"_id": 0, "total": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(spec1)

    # 2. In string field path
    spec2 = {
        "collection": "orders",
        "pipeline": [
            {"$project": {"_id": 0, "cid": "$company_id"}},
        ],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(spec2)


def test_validator_rejects_disallowed_variables_and_regex():
    spec_var = {
        "collection": "orders",
        "pipeline": [
            {"$project": {"_id": 0, "val": "$$DISALLOWED"}},
        ],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(spec_var)

    spec_long_regex = {
        "collection": "orders",
        "pipeline": [
            {"$match": {"customer_name": {"$regex": "a" * 45}}},
            {"$limit": 10},
            {"$project": {"_id": 0, "customer_name": 1}},
        ],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(spec_long_regex)


def test_validator_rejects_structural_violations():
    # Too many stages (> 8)
    spec_too_many = {
        "collection": "orders",
        "pipeline": [{"$match": {"status": "completed"}}] * 9 + [{"$project": {"_id": 0, "total": 1}}],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(spec_too_many)

    # Missing final $project
    spec_no_project = {
        "collection": "orders",
        "pipeline": [{"$match": {"status": "completed"}}, {"$limit": 10}],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(spec_no_project)

    # Missing _id: 0 in final project
    spec_no_suppress = {
        "collection": "orders",
        "pipeline": [{"$project": {"total": 1}}],
    }
    with pytest.raises(SqlValidationFailedError):
        validate_and_format_sql(spec_no_suppress)


def test_validator_caps_and_enforces_limit():
    spec = {
        "collection": "orders",
        "pipeline": [
            {"$match": {"status": "completed"}},
            {"$limit": 500},
            {"$project": {"_id": 0, "total": 1}},
        ],
    }
    norm, cols = validate_and_format_sql(spec)
    assert cols == ["total"]
    limit_stage = next(s for s in norm["pipeline"] if "$limit" in s)
    assert limit_stage["$limit"] == 100


def test_validator_accepts_valid_typical_queries():
    # 1. Group by month
    monthly_spec = {
        "collection": "orders",
        "pipeline": [
            {"$match": {"status": "completed"}},
            {"$group": {"_id": {"$month": "$order_date"}, "monthly_total": {"$sum": "$total"}}},
            {"$limit": 12},
            {"$project": {"_id": 0, "month": "$_id", "monthly_total": 1}},
        ],
    }
    norm, cols = validate_and_format_sql(monthly_spec)
    assert "monthly_total" in cols

    # 2. Top 5 customers
    top5_spec = {
        "collection": "orders",
        "pipeline": [
            {"$match": {"status": "completed"}},
            {"$group": {"_id": "$customer_name", "total_spent": {"$sum": "$total"}}},
            {"$sort": {"total_spent": -1}},
            {"$limit": 5},
            {"$project": {"_id": 0, "customer_name": "$_id", "total_spent": 1}},
        ],
    }
    norm, cols = validate_and_format_sql(top5_spec)
    assert cols == ["customer_name", "total_spent"]

    # 3. Ratio with $divide
    ratio_spec = {
        "collection": "orders",
        "pipeline": [
            {"$group": {"_id": None, "total": {"$sum": 1}, "comp": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}}}},
            {"$limit": 1},
            {"$project": {"_id": 0, "ratio": {"$divide": ["$comp", "$total"]}}},
        ],
    }
    norm, cols = validate_and_format_sql(ratio_spec)
    assert cols == ["ratio"]
