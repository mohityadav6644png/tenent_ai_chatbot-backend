"""
Live check script for Phase 8 Multi-document Synthesis & Querying.
Runs end-to-end check with fake test data across two complementary CSV files.
"""

import asyncio
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import ASGITransport, AsyncClient
from bson import ObjectId

from app.core.cache import invalidate_company_cache
from app.core.security import generate_csrf_token, generate_opaque_token, hash_token
from app.infrastructure.db.mongodb import (
    app_database,
    business_database_writer,
    sessions_collection,
    tenants_collection,
    users_collection,
)
from app.main import app

LIVE_TENANT = "live_tenant_multidoc_888"
LIVE_USER = "live_user_multidoc_888"


async def run_live_check():
    now = datetime.now(timezone.utc)

    # 1. Setup tenant & user
    await tenants_collection.update_one(
        {"_id": LIVE_TENANT},
        {"$set": {"name": "Live Multidoc Corp", "schema_version": 1, "status": "active"}},
        upsert=True,
    )
    await users_collection.update_one(
        {"_id": LIVE_USER},
        {"$set": {"email": "live_user@multidoc.com", "role": "employee", "company_id": LIVE_TENANT, "is_active": True}},
        upsert=True,
    )

    # Clean prior data
    await app_database["company_files"].delete_many({"company_id": LIVE_TENANT})
    await business_database_writer["company_rows"].delete_many({"company_id": LIVE_TENANT})
    await invalidate_company_cache(LIVE_TENANT)

    # 2. Upload Document 1: Products
    doc1_id = ObjectId()
    await app_database["company_files"].insert_one({
        "_id": doc1_id,
        "company_id": LIVE_TENANT,
        "name": "Products Catalog",
        "slug": "products_catalog",
        "status": "ready",
        "row_count": 2,
        "columns": [
            {"key": "sku", "label": "SKU", "type": "text", "hidden": False},
            {"key": "product_name", "label": "Product Name", "type": "text", "hidden": False},
            {"key": "category", "label": "Category", "type": "text", "hidden": False},
        ],
        "created_at": now,
        "updated_at": now,
    })
    await business_database_writer["company_rows"].insert_many([
        {"company_id": LIVE_TENANT, "file_id": str(doc1_id), "data": {"sku": "SKU-A", "product_name": "Keyboard", "category": "Hardware"}},
        {"company_id": LIVE_TENANT, "file_id": str(doc1_id), "data": {"sku": "SKU-B", "product_name": "Monitor", "category": "Hardware"}},
    ])

    # 3. Upload Document 2: Inventory
    doc2_id = ObjectId()
    await app_database["company_files"].insert_one({
        "_id": doc2_id,
        "company_id": LIVE_TENANT,
        "name": "Warehouse Stock",
        "slug": "warehouse_stock",
        "status": "ready",
        "row_count": 2,
        "columns": [
            {"key": "sku", "label": "SKU", "type": "text", "hidden": False},
            {"key": "stock_qty", "label": "Stock Qty", "type": "number", "hidden": False},
            {"key": "location", "label": "Location", "type": "text", "hidden": False},
        ],
        "created_at": now,
        "updated_at": now,
    })
    await business_database_writer["company_rows"].insert_many([
        {"company_id": LIVE_TENANT, "file_id": str(doc2_id), "data": {"sku": "SKU-A", "stock_qty": 150, "location": "Warehouse-1"}},
        {"company_id": LIVE_TENANT, "file_id": str(doc2_id), "data": {"sku": "SKU-B", "stock_qty": 30, "location": "Warehouse-2"}},
    ])

    # 4. Session & CSRF auth setup
    from datetime import timedelta
    raw_token = generate_opaque_token()
    token_hash = hash_token(raw_token)
    raw_csrf = generate_csrf_token()

    await sessions_collection.insert_one({
        "user_id": LIVE_USER,
        "token_hash": token_hash,
        "expires_at": now + timedelta(hours=2),
        "ip": "127.0.0.1",
        "user_agent": "pytest",
        "created_at": now,
    })

    transport = ASGITransport(app=app)
    cookies = {"session": raw_token, "csrf_token": raw_csrf}
    headers = {"X-CSRF-Token": raw_csrf}

    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies, headers=headers) as client:
        # Cross-document query
        payload = {
            "question": "Combine Products Catalog and Warehouse Stock across sku",
            "history": [],
        }
        resp = await client.post("/api/v1/chat/query", json=payload)
        http_status = resp.status_code
        data = resp.json()
        used_md = data.get("multidoc_meta", {}).get("used_multi_doc", False) if data.get("multidoc_meta") else False
        sources = data.get("multidoc_meta", {}).get("source_names", []) if data.get("multidoc_meta") else []
        rows = data.get("answer", {}).get("rows", [])
        passed = (http_status == 200 and used_md is True and len(sources) == 2 and len(rows) == 2)

        print(f"HTTP Status: {http_status}")
        print(f"Multi-doc Merge Used: {used_md}")
        print(f"Source Count: {len(sources)} ({', '.join(sources)})")
        print(f"Live Check Result: {'PASS' if passed else 'FAIL'}")

    # Cleanup
    await app_database["company_files"].delete_many({"company_id": LIVE_TENANT})
    await business_database_writer["company_rows"].delete_many({"company_id": LIVE_TENANT})
    await sessions_collection.delete_many({"company_id": LIVE_TENANT})
    await tenants_collection.delete_many({"_id": LIVE_TENANT})
    await users_collection.delete_many({"_id": LIVE_USER})
    await invalidate_company_cache(LIVE_TENANT)


if __name__ == "__main__":
    asyncio.run(run_live_check())
