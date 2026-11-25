"""
Unit tests for Multi-document Querying & Synthesis (tests/unit/test_multidoc_unit.py)
"""

import pytest
from app.modules.multidoc.join_detector import detect_join_keys, normalize_field_name
from app.modules.multidoc.merger import merge_document_records
from app.modules.multidoc.models import DocumentSummary, MultiDocQueryPlan
from app.modules.multidoc.service import MultiDocService
from app.infrastructure.multidoc_planner.fake_planner import FakeMultiDocPlanner


def test_normalize_field_name():
    assert normalize_field_name("Product ID") == "product_id"
    assert normalize_field_name(" SKU-Code ") == "sku_code"
    assert normalize_field_name("Student__Name") == "student_name"


def test_detect_join_keys():
    doc1_fields = ["product_id", "product_name", "category", "price", "_id", "company_id"]
    doc2_fields = ["product_id", "stock_qty", "warehouse", "secret_token"]

    keys = detect_join_keys([doc1_fields, doc2_fields], hidden_fields={"price"})
    # product_id is common and not excluded
    assert "product_id" in keys
    # _id, company_id, secret_token should never be candidate join keys
    assert "_id" not in keys
    assert "company_id" not in keys
    assert "secret_token" not in keys
    # price is hidden
    assert "price" not in keys


def test_detect_join_keys_prioritizes_id():
    doc1_fields = ["region", "sku", "category"]
    doc2_fields = ["region", "sku", "notes"]

    keys = detect_join_keys([doc1_fields, doc2_fields])
    assert keys[0] == "sku"
    assert keys[1] == "region"


def test_merge_document_records_basic():
    doc_records = {
        "catalog": [
            {"product_id": "P1", "name": "Apple iPhone", "category": "Electronics"},
            {"product_id": "P2", "name": "Samsung Galaxy", "category": "Electronics"},
        ],
        "inventory": [
            {"product_id": "P1", "stock": 100, "warehouse": "West"},
            {"product_id": "P2", "stock": 50, "warehouse": "East"},
            {"product_id": "P3", "stock": 20, "warehouse": "North"},
        ],
    }
    doc_names = {"catalog": "catalog", "inventory": "inventory"}

    col_defs, rows, trunc = merge_document_records(
        doc_records=doc_records,
        doc_names=doc_names,
        join_keys=["product_id"],
        max_merged_rows=50,
    )

    col_names = [c["name"] for c in col_defs]
    assert "product_id" in col_names
    assert "name" in col_names
    assert "stock" in col_names
    assert not trunc
    assert len(rows) == 3

    # Check P1 row has data from both
    p1_row = next(r for r in rows if r[col_names.index("product_id")] == "P1")
    assert p1_row[col_names.index("name")] == "Apple iPhone"
    assert p1_row[col_names.index("stock")] == 100


def test_merge_document_records_collision_prefixing():
    doc_records = {
        "file1": [{"id": "1", "score": 90}],
        "file2": [{"id": "1", "score": 85}],
    }
    doc_names = {"file1": "exam1", "file2": "exam2"}

    col_defs, rows, _ = merge_document_records(
        doc_records=doc_records,
        doc_names=doc_names,
        join_keys=["id"],
    )

    col_names = [c["name"] for c in col_defs]
    assert "id" in col_names
    assert "exam1_score" in col_names
    assert "exam2_score" in col_names


def test_merge_document_records_caps_rows():
    doc_records = {
        "f1": [{"id": str(i), "val": i} for i in range(100)],
    }
    col_defs, rows, trunc = merge_document_records(
        doc_records=doc_records,
        doc_names={"f1": "f1"},
        join_keys=["id"],
        max_merged_rows=10,
    )
    assert len(rows) == 10
    assert trunc is True


class MockMultiDocRepo:
    def __init__(self, docs, records):
        self.docs = docs
        self.records = records

    async def list_documents(self, company_id: str, limit: int = 6):
        return [d for d in self.docs if getattr(d, "_company_id", company_id) == company_id]

    async def get_document_records(self, company_id: str, document_id: str, limit: int = 200, hidden_keys=None):
        return self.records.get(document_id, [])

    async def get_document_by_id_or_slug(self, company_id: str, doc_id_or_slug: str):
        for d in self.docs:
            if d.document_id == doc_id_or_slug:
                return {"_id": d.document_id, "slug": d.document_id, "name": d.source_name}
        return None


@pytest.mark.asyncio
async def test_multidoc_service_eval_and_execute():
    docs = [
        DocumentSummary(document_id="doc1", source_name="Products", record_count=2, fields=["sku", "price"]),
        DocumentSummary(document_id="doc2", source_name="Stock", record_count=2, fields=["sku", "qty"]),
    ]
    records = {
        "doc1": [{"sku": "SKU1", "price": 10}, {"sku": "SKU2", "price": 20}],
        "doc2": [{"sku": "SKU1", "qty": 100}, {"sku": "SKU2", "qty": 200}],
    }
    repo = MockMultiDocRepo(docs, records)
    planner = FakeMultiDocPlanner()
    service = MultiDocService(repo=repo, planner=planner)

    result = await service.evaluate_and_execute("c1", "combine Products and Stock by sku", [])
    assert result is not None
    assert result["multidoc_meta"].used_multi_doc is True
    assert "Products" in result["multidoc_meta"].source_names
    assert "Stock" in result["multidoc_meta"].source_names
    assert len(result["rows"]) == 2


def test_merge_three_documents_with_partial_matches():
    doc_records = {
        "d1": [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}],
        "d2": [{"id": "1", "grade": "A"}, {"id": "3", "grade": "B"}],
        "d3": [{"id": "1", "fee": 500}, {"id": "2", "fee": 600}],
    }
    doc_names = {"d1": "students", "d2": "grades", "d3": "fees"}

    col_defs, rows, trunc = merge_document_records(
        doc_records=doc_records,
        doc_names=doc_names,
        join_keys=["id"],
    )
    col_names = [c["name"] for c in col_defs]
    assert "id" in col_names
    assert "name" in col_names
    assert "grade" in col_names
    assert "fee" in col_names
    # 3 unique IDs: 1, 2, 3
    assert len(rows) == 3


def test_merge_hidden_columns_filtering():
    doc_records = {
        "d1": [{"id": "1", "name": "Alice", "secret_salary": 10000}],
        "d2": [{"id": "1", "dept": "Engineering"}],
    }
    col_defs, rows, _ = merge_document_records(
        doc_records=doc_records,
        doc_names={"d1": "d1", "d2": "d2"},
        join_keys=["id"],
        hidden_fields_by_doc={"d1": {"secret_salary"}},
    )
    col_names = [c["name"] for c in col_defs]
    assert "secret_salary" not in col_names


@pytest.mark.asyncio
async def test_multidoc_service_planner_timeout_fallback():
    import asyncio

    class TimeoutPlanner:
        async def plan_query(self, *args, **kwargs):
            await asyncio.sleep(10)
            return MultiDocQueryPlan(requires_multi_doc=True)

    docs = [
        DocumentSummary(document_id="doc1", source_name="P", record_count=1, fields=["id"]),
        DocumentSummary(document_id="doc2", source_name="S", record_count=1, fields=["id"]),
    ]
    repo = MockMultiDocRepo(docs, {})
    service = MultiDocService(repo=repo, planner=TimeoutPlanner())

    # Should safely timeout and fall back to None without raising exception
    result = await service.evaluate_and_execute("c1", "combine both", [])
    assert result is None

