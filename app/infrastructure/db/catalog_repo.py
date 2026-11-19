"""
Schema Catalog Repository (app/infrastructure/db/catalog_repo.py)
Exposes tenant-specific collections, fields, types, sample values, relationships, and schema version.
company_id is strictly omitted from the AI-visible catalog.
"""

from typing import Any, Dict
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
    async def get_catalog(self, company_id: str) -> Dict[str, Any]:
        """Fetch AI-visible catalog without company_id."""
        schema_version = 1
        try:
            from bson import ObjectId
            oid = ObjectId(company_id) if ObjectId.is_valid(company_id) else company_id
            tenant = await tenants_collection.find_one({"_id": oid})
            if tenant:
                schema_version = tenant.get("schema_version", 1)
        except Exception:
            pass

        summaries = []
        total_row_count = 0
        try:
            from app.modules.company.summary import get_tenant_summaries
            summaries = await get_tenant_summaries(company_id)
            total_row_count = sum(s.get("row_count", 0) for s in summaries)
        except Exception:
            pass

        catalog = dict(MONGO_BUSINESS_CATALOG)
        catalog["schema_version"] = schema_version
        catalog["summaries"] = summaries
        catalog["total_row_count"] = total_row_count
        return catalog
