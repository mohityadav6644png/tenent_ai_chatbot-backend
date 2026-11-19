"""
Unit Tests (tests/unit/test_unit.py)
Tests template filler, chart hints, cache key builder, and security tokens.
"""

from app.core.security import (
    constant_time_compare,
    generate_csrf_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.infrastructure.azure.redis_cache import build_cache_key
from app.modules.auth.models import create_user_document
from app.modules.chat.response_builder import determine_chart_hint, fill_template


def test_password_hashing():
    raw_pass = "SecurePass123!"
    hashed = hash_password(raw_pass)
    assert verify_password(raw_pass, hashed) is True
    assert verify_password("Wrong!", hashed) is False


def test_user_document():
    doc = create_user_document("test_user", "test@example.com", "fake_hash")
    assert doc["username"] == "test_user"
    assert doc["email"] == "test@example.com"


def test_template_filler():
    cols = [{"name": "name", "type": "string"}, {"name": "revenue", "type": "numeric"}]
    rows = [["Basmati Rice", 50000], ["Mustard Oil", 25000]]

    # 1. Unknown placeholder fallback
    assert fill_template("Top item {unknown_field}", cols, rows) == "Results for your question"

    # 2. Known placeholders and row count
    res = fill_template("Top product: {row1.name} with ₹{row1.revenue} ({row_count} items)", cols, rows)
    assert res == "Top product: Basmati Rice with ₹50000 (2 items)"

    # 3. Pct calculation
    res_pct = fill_template("Difference between first and second: {pct_1_2}", cols, rows)
    assert "+100.0%" in res_pct or "%" in res_pct

    # 4. Single row result
    one_row = [["Single Product", 1000]]
    res_single = fill_template("Found {row_count} item: {row1.name}", cols, one_row)
    assert res_single == "Found 1 item: Single Product"


def test_cache_key_builder():
    k1 = build_cache_key("tenant_1", 1, "What is our revenue?")
    k2 = build_cache_key("tenant_1", 1, "what is our revenue? ")
    k3 = build_cache_key("tenant_2", 1, "What is our revenue?")
    k4 = build_cache_key("tenant_1", 2, "What is our revenue?")

    assert k1 == k2  # Case & whitespace normalized
    assert k1 != k3  # Different tenant
    assert k1 != k4  # Different schema version


def test_chart_hint():
    # Less than 2 rows
    assert determine_chart_hint([{"name": "a", "type": "string"}], [["v1"]]) == "none"

    # Date and number -> line chart
    line_cols = [{"name": "order_date", "type": "date"}, {"name": "total_amount", "type": "number"}]
    assert determine_chart_hint(line_cols, [["2024-01-01", 100], ["2024-01-02", 200]]) == "line"

    # Category and number -> bar chart
    bar_cols = [{"name": "category", "type": "string"}, {"name": "revenue", "type": "number"}]
    assert determine_chart_hint(bar_cols, [["Grains", 100], ["Spices", 200]]) == "bar"


def test_security_helpers():
    token = generate_opaque_token(32)
    assert len(token) == 64
    thash = hash_token(token)
    assert len(thash) == 64

    csrf = generate_csrf_token()
    assert len(csrf) > 20
    assert constant_time_compare(csrf, csrf) is True
    assert constant_time_compare(csrf, "wrong_csrf") is False
