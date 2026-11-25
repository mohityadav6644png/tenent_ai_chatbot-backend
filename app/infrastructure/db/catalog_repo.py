"""
Schema Catalog Repository (app/infrastructure/db/catalog_repo.py)
Exposes tenant-specific collections, fields, types, sample values, relationships, and schema version.
company_id is strictly omitted from the AI-visible catalog.
"""

from datetime import datetime
import json
from typing import Any, Dict
from app.core.config import settings
from app.infrastructure.db.mongodb import tenants_collection

MONGO_BUSINESS_CATALOG = {
    "allowed_collections": ["orders", "customers", "products", "invoices", "stock_levels"],
    "allowed_views": ["orders", "customers", "products", "invoices", "stock_levels"],
    "collections": {
        "orders": {
            "description": "Denormalized orders embedding customer_name, region, status, order_date, total, items",
            "fields": {
                "customer_name": {"type": "string", "desc": "Customer name", "samples": ["Asha Patel", "Rohan Sharma"]},
                "region": {"type": "string", "desc": "Sales geographic region", "samples": ["North", "South", "West", "East"]},
                "status": {"type": "string", "desc": "Order state", "samples": ["completed", "pending", "cancelled"]},
                "order_date": {"type": "date", "desc": "Order placement timestamp", "samples": ["2026-05-01T00:00:00Z"]},
                "total": {"type": "number", "desc": "Total order amount in INR", "samples": [1500, 4200, 12500]},
                "items": {"type": "array", "desc": "Items list [{product_name, category, qty, price}]"},
            },
        },
        "customers": {
            "description": "Customer profiles and demographic location",
            "fields": {
                "name": {"type": "string", "desc": "Customer company or contact name", "samples": ["Acme Traders", "Zenith Retail"]},
                "city": {"type": "string", "desc": "City of operations", "samples": ["Mumbai", "Delhi", "Bengaluru"]},
                "created_at": {"type": "date", "desc": "Account registration date"},
            },
        },
        "products": {
            "description": "Product catalog, categories and pricing",
            "fields": {
                "name": {"type": "string", "desc": "Product name", "samples": ["Basmati Rice 5kg", "Organic Turmeric 500g"]},
                "category": {"type": "string", "desc": "Product category", "samples": ["Grains", "Spices", "Oils"]},
                "price": {"type": "number", "desc": "Unit selling price", "samples": [250, 480, 1100]},
            },
        },
        "invoices": {
            "description": "Invoicing records, due dates and billing statuses",
            "fields": {
                "invoice_number": {"type": "string", "desc": "Invoice code", "samples": ["INV-2024-001", "INV-2024-002"]},
                "invoice_date": {"type": "date", "desc": "Billing issue date"},
                "amount": {"type": "number", "desc": "Invoice payable amount in INR", "samples": [5000, 18500]},
                "status": {"type": "string", "desc": "Payment status", "samples": ["paid", "unpaid", "overdue"]},
            },
        },
        "stock_levels": {
            "description": "Warehouse stock levels and reorder thresholds",
            "fields": {
                "product_name": {"type": "string", "desc": "Product SKU name", "samples": ["Basmati Rice 5kg"]},
                "warehouse_location": {"type": "string", "desc": "Warehouse facility", "samples": ["Mumbai-Central", "Delhi-Hub"]},
                "quantity_on_hand": {"type": "number", "desc": "Units currently on hand", "samples": [10, 45, 120]},
                "reorder_threshold": {"type": "number", "desc": "Minimum trigger level", "samples": [20, 50]},
            },
        },
    },
    "relationships": [
        "orders.customer_name documents customer name in customers.name",
        "orders.items.product_name documents product in products.name",
        "stock_levels.product_name documents product in products.name",
    ],
    "sample_values": {
        "region": ["North", "South", "West", "East"],
        "status": ["completed", "pending", "cancelled"],
        "category": ["Grains", "Spices", "Oils"],
        "warehouse_location": ["Mumbai-Central", "Delhi-Hub", "Bengaluru-Depot"],
    },
}


class SchemaCatalogRepo:
    def __init__(self, app_db: Any = None, reader_db: Any = None):
        self._app_db = app_db
        self._reader_db = reader_db

    async def get_catalog(self, company_id: str) -> Dict[str, Any]:
        """Fetch AI-visible catalog without company_id."""
        schema_version = 1
        try:
            from bson import ObjectId
            oid = ObjectId(company_id) if ObjectId.is_valid(company_id) else company_id
            t_coll = (self._app_db["tenants"] if self._app_db is not None else tenants_collection)
            tenant = await t_coll.find_one({"_id": oid})
            if tenant:
                schema_version = tenant.get("schema_version", 1)
        except Exception:
            pass

        summaries = []
        total_row_count = 0
        try:
            from app.modules.company.summary import get_tenant_summaries
            summaries = await get_tenant_summaries(company_id, app_db=self._app_db, reader_db=self._reader_db)
            total_row_count = sum(s.get("row_count", 0) for s in summaries)
        except Exception:
            pass

        ready_files = []
        active_conns = []
        try:
            from app.infrastructure.db.mongodb import company_files_collection, file_connections_collection
            files_coll = self._app_db["company_files"] if self._app_db is not None else company_files_collection
            cursor = files_coll.find({"company_id": str(company_id), "status": "ready"}).sort("updated_at", -1)
            raw_files = await cursor.to_list(length=100)
            raw_files_by_id = {str(f["_id"]): f for f in raw_files}
            rf_by_id = {}
            for f in raw_files:
                f_stats = f.get("stats", {}).get("columns", {})
                cols = []
                for c in f.get("columns", []):
                    if c.get("hidden"):
                        continue
                    k = c["key"]
                    col_info = {
                        "key": k,
                        "label": c.get("label", k),
                        "type": c.get("type", "text"),
                        "meaning": c.get("meaning"),
                        "example": c.get("example"),
                    }
                    if k in f_stats:
                        st = f_stats[k]
                        if "min" in st:
                            col_info["min"] = st.get("min")
                            col_info["max"] = st.get("max")
                        if "distinct_values" in st and st.get("distinct_values") is not None:
                            col_info["distinct_values"] = st.get("distinct_values")
                    cols.append(col_info)

                rf_entry = {
                    "slug": f.get("slug", ""),
                    "name": f.get("name", ""),
                    "description": f.get("description", ""),
                    "row_count": f.get("row_count", 0),
                    "updated_at": f.get("updated_at"),
                    "columns": cols,
                    "connections": [],
                }
                ready_files.append(rf_entry)
                rf_by_id[str(f["_id"])] = rf_entry

            conn_coll = self._app_db["file_connections"] if self._app_db is not None else file_connections_collection
            c_cursor = conn_coll.find({"company_id": str(company_id), "status": "active"}).sort("created_at", -1)
            active_conns = await c_cursor.to_list(length=100)
            for conn in active_conns:
                conn_id = str(conn.get("id") or conn.get("_id"))
                conn["id"] = conn_id
                left_id = str(conn.get("left_file_id"))
                right_id = str(conn.get("right_file_id"))
                left_rf = rf_by_id.get(left_id)
                right_raw = raw_files_by_id.get(right_id)
                if left_rf and right_raw:
                    left_name = left_rf.get("name") or left_rf.get("slug")
                    right_name = right_raw.get("name") or right_raw.get("slug")
                    alias = conn.get("alias")
                    conn["left_file_slug"] = left_rf.get("slug")
                    conn["right_file_slug"] = right_raw.get("slug")
                    conn["left_file_name"] = left_name
                    conn["right_file_name"] = right_name
                    rate = conn.get("match_rate", 0)
                    pct = round(rate * 100, 1)
                    pct_str = f"{int(pct)}" if pct == int(pct) else f"{pct}"
                    desc = (
                        f"Each row of {left_name} can be linked to one row of {right_name} "
                        f"through {conn.get('left_key')} = {conn.get('right_key')}; {pct_str}% of rows match"
                    )
                    left_rf["connections"].append({
                        "id": conn_id,
                        "alias": alias,
                        "description": desc,
                        "left_key": conn.get("left_key"),
                        "right_key": conn.get("right_key"),
                    })
                    r_stats = right_raw.get("stats", {}).get("columns", {})
                    for rc in right_raw.get("columns", []):
                        if rc.get("hidden"):
                            continue
                        rk = rc["key"]
                        joined_col_info = {
                            "key": f"{alias}.{rk}",
                            "label": f"{alias}.{rc.get('label', rk)}",
                            "type": rc.get("type", "text"),
                            "meaning": rc.get("meaning"),
                            "example": rc.get("example"),
                        }
                        if rk in r_stats:
                            rst = r_stats[rk]
                            if "min" in rst:
                                joined_col_info["min"] = rst.get("min")
                                joined_col_info["max"] = rst.get("max")
                            if "distinct_values" in rst and rst.get("distinct_values") is not None:
                                joined_col_info["distinct_values"] = rst.get("distinct_values")
                        left_rf["columns"].append(joined_col_info)
        except Exception:
            pass

        # Cap whole catalog at settings.FILES_CATALOG_MAX_KB (15 KB)
        max_bytes = getattr(settings, "FILES_CATALOG_MAX_KB", 15) * 1024
        serialized = json.dumps(ready_files, default=str)
        if len(serialized.encode("utf-8")) > max_bytes:
            # 1. Drop distinct-value lists first
            for rf in ready_files:
                for c in rf.get("columns", []):
                    c.pop("distinct_values", None)
            serialized = json.dumps(ready_files, default=str)

        if len(serialized.encode("utf-8")) > max_bytes:
            # 2. Mark least recently updated files "details omitted"
            sorted_indices = sorted(range(len(ready_files)), key=lambda i: ready_files[i].get("updated_at") or datetime.min)
            for idx in sorted_indices:
                ready_files[idx]["columns"] = [{"note": "details omitted"}]
                serialized = json.dumps(ready_files, default=str)
                if len(serialized.encode("utf-8")) <= max_bytes:
                    break

        catalog = dict(MONGO_BUSINESS_CATALOG)
        catalog["schema_version"] = schema_version
        catalog["summaries"] = summaries
        catalog["total_row_count"] = total_row_count
        catalog["files"] = ready_files

        file_slugs = [rf["slug"] for rf in ready_files if rf.get("slug")]
        catalog["allowed_collections"] = list(catalog.get("allowed_collections", [])) + file_slugs
        catalog["allowed_views"] = list(catalog.get("allowed_views", [])) + file_slugs

        file_fields = {}
        for rf in ready_files:
            file_fields[rf["slug"]] = {c["key"] for c in rf.get("columns", []) if "key" in c}
        catalog["file_fields"] = file_fields

        catalog["connections"] = active_conns
        catalog["model_rules"] = [
            "Use the file that has MANY rows per linked row as the primary (marks, orders, deals) and reference linked fields as '$alias.column'.",
            f"Use at most {getattr(settings, 'QUERY_MAX_JOINS', 2)} connections per question.",
            "Never invent connections.",
        ]

        return catalog
