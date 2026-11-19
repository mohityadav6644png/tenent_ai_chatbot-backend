"""
Integration Tests for Company Datasets Endpoints
(tests/integration/test_company_datasets_endpoints.py)
"""

import io
from datetime import UTC, datetime, timedelta
import httpx
import pytest

from app.api.deps import get_db
from app.core.security import create_access_token
from app.main import app
from app.modules.company.datasets import DATASET_CATALOG


class MockBusinessCollection:
    def __init__(self):
        self.docs = []

    async def count_documents(self, query):
        cid = query.get("company_id")
        return sum(1 for d in self.docs if d.get("company_id") == cid)

    def find(self, query):
        cid = query.get("company_id")
        matches = [d for d in self.docs if d.get("company_id") == cid]

        class Cursor:
            def __init__(self, data):
                self._data = data

            def sort(self, key, direction):
                return self

            def limit(self, n):
                self._data = self._data[:n]
                return self

            async def to_list(self, n):
                return self._data[:n]

        return Cursor(matches)

    async def bulk_write(self, operations, ordered=False):
        inserted = 0
        updated = 0
        for op in operations:
            filt = op._filter
            doc = op._doc.get("$set", {})
            existing = None
            for d in self.docs:
                if all(d.get(k) == v for k, v in filt.items()):
                    existing = d
                    break
            if existing:
                existing.update(doc)
                updated += 1
            else:
                self.docs.append(dict(doc, **filt))
                inserted += 1

        class BulkResult:
            upserted_count = inserted
            modified_count = updated

        return BulkResult()

    async def insert_many(self, docs, ordered=False):
        self.docs.extend([dict(d) for d in docs])

        class InsertResult:
            inserted_ids = [str(i) for i in range(len(docs))]

        return InsertResult()

    async def delete_many(self, query):
        cid = query.get("company_id")
        import_id_ne = (query.get("import_id") or {}).get("$ne")
        self.docs = [
            d for d in self.docs
            if d.get("company_id") != cid or d.get("import_id") == import_id_ne
        ]


class MockBusinessDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MockBusinessCollection()
        return self._collections[name]


@pytest.fixture(autouse=True)
def mock_auth_repo(monkeypatch):
    class MockAuthRepo:
        async def get_user_by_id(self, user_id):
            return {"_id": user_id, "is_active": True}

        async def get_tenant_by_id(self, tenant_id):
            return {"_id": tenant_id, "status": "active"}

    monkeypatch.setattr("app.api.deps.get_auth_repo", lambda: MockAuthRepo())


@pytest.fixture
def admin_token():
    return create_access_token(
        user_id="admin_001",
        role="company_admin",
        company_id="acme_tenant_1",
    )


@pytest.fixture
def employee_token():
    return create_access_token(
        user_id="emp_002",
        role="employee",
        company_id="acme_tenant_1",
    )


# ---------------------------------------------------------------------------
# GET /v1/company/datasets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_datasets_list(admin_token):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/v1/company/datasets",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "datasets" in data
        datasets = data["datasets"]
        assert len(datasets) == len(DATASET_CATALOG)

        keys = {d["key"] for d in datasets}
        assert keys == {"customers", "products", "orders", "invoices", "stock_levels"}

        orders_ds = next(d for d in datasets if d["key"] == "orders")
        assert orders_ds["label"] == "Orders"
        assert orders_ds["record_label"] == "order"
        assert "order_id" in orders_ds["required_columns"]
        assert orders_ds["status"] in ("ready", "no_data", "needs_update")


@pytest.mark.asyncio
async def test_get_datasets_forbidden_for_employee(employee_token):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/v1/company/datasets",
            headers={"Authorization": f"Bearer {employee_token}"},
        )
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_get_datasets_unauthenticated():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/v1/company/datasets")
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# GET /v1/company/datasets/{dataset}/template
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dataset_template_orders(admin_token):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/v1/company/datasets/orders/template",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        assert 'attachment; filename="orders_template.csv"' in res.headers["content-disposition"]
        text = res.text
        # Required columns must be present
        for col in ["order_id", "order_date", "customer_name", "region", "status", "total"]:
            assert col in text


@pytest.mark.asyncio
async def test_get_dataset_template_not_found(admin_token):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/v1/company/datasets/unknown_collection/template",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# POST /v1/company/datasets/{dataset}/imports/preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_orders_import_valid(admin_token):
    csv_content = (
        "order_id,order_date,customer_name,region,status,total\n"
        "ORD-001,2026-01-10,Acme Retail,North,completed,1500.50\n"
        "ORD-002,2026-01-11,Bharat Traders,South,pending,2200.00\n"
    )
    files = {"file": ("orders.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/v1/company/datasets/orders/imports/preview",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_rows"] == 2
        assert data["valid_rows"] == 2
        assert data["invalid_rows"] == 0
        assert len(data["missing_columns"]) == 0
        assert len(data["sample_rows"]) == 2
        assert len(data["errors"]) == 0


@pytest.mark.asyncio
async def test_preview_orders_import_missing_columns(admin_token):
    csv_content = (
        "order_id,customer_name,status\n"
        "ORD-001,Acme Retail,completed\n"
    )
    files = {"file": ("orders_incomplete.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/v1/company/datasets/orders/imports/preview",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "order_date" in data["missing_columns"]
        assert "region" in data["missing_columns"]
        assert "total" in data["missing_columns"]
        assert data["invalid_rows"] == 1
        assert len(data["errors"]) > 0


# ---------------------------------------------------------------------------
# POST /v1/company/datasets/{dataset}/imports
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_orders_upsert_success(admin_token, monkeypatch):
    mock_b_db = MockBusinessDB()

    class MockIngestionRepo:
        async def upsert_records(self, company_id, collection_name, natural_key, records):
            return len(records), 0

        async def replace_records(self, company_id, collection_name, import_id, records):
            return len(records)

    class MockTrackingRepo:
        async def has_active_import(self, company_id, dataset, timeout_minutes=15):
            return False

        async def count_recent_imports(self, company_id, hours=1):
            return 0

        async def create_import(self, doc):
            return "imp_123"

        async def update_import(self, import_id, updates):
            pass

    from app.container import container
    monkeypatch.setattr(container.company_service, "ingestion_repo", MockIngestionRepo())
    monkeypatch.setattr(container.company_service, "tracking_repo", MockTrackingRepo())

    csv_content = (
        "order_id,order_date,customer_name,region,status,total\n"
        "ORD-101,2026-02-01,Alpha Store,West,completed,4500\n"
        "ORD-102,2026-02-02,Beta Shop,East,pending,3100\n"
    )
    files = {"file": ("orders.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}
    data = {"mode": "upsert"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/v1/company/datasets/orders/imports",
            files=files,
            data=data,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 200
        res_data = res.json()
        assert res_data["inserted"] == 2
        assert res_data["status"] == "completed"
        assert "import_id" in res_data


@pytest.mark.asyncio
async def test_import_concurrency_lock_returns_409(admin_token, monkeypatch):
    class MockTrackingRepoActive:
        async def has_active_import(self, company_id, dataset, timeout_minutes=15):
            return True

        async def count_recent_imports(self, company_id, hours=1):
            return 0

    from app.container import container
    monkeypatch.setattr(container.company_service, "tracking_repo", MockTrackingRepoActive())

    csv_content = "order_id,order_date,customer_name,region,status,total\nORD-1,2026-01-01,A,N,c,100\n"
    files = {"file": ("orders.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/v1/company/datasets/orders/imports",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 409
        assert res.json()["error"]["code"] == "IMPORT_IN_PROGRESS"


@pytest.mark.asyncio
async def test_import_rate_limit_returns_429(admin_token, monkeypatch):
    class MockTrackingRepoRateLimited:
        async def has_active_import(self, company_id, dataset, timeout_minutes=15):
            return False

        async def count_recent_imports(self, company_id, hours=1):
            return 999  # Exceeds max per hour

    from app.container import container
    monkeypatch.setattr(container.company_service, "tracking_repo", MockTrackingRepoRateLimited())

    csv_content = "order_id,order_date,customer_name,region,status,total\nORD-1,2026-01-01,A,N,c,100\n"
    files = {"file": ("orders.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/v1/company/datasets/orders/imports",
            files=files,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 429
        assert res.json()["error"]["code"] == "RATE_LIMITED"
        assert "Retry-After" in res.headers
