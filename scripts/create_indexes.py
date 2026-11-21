"""
Create Business Indexes (scripts/create_indexes.py)
Creates compound indexes on business collections and app collections starting with company_id.
"""

import asyncio
from pathlib import Path
import sys

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import ASCENDING, DESCENDING
from app.infrastructure.db.mongodb import (
    app_client,
    app_database,
    business_database_writer,
    business_writer_client,
)


async def create_business_indexes():
    print("Creating compound business and data import indexes on MongoDB...")
    db = business_database_writer
    app_db = app_database

    # Orders collection indexes
    await db["orders"].create_index([("company_id", ASCENDING), ("order_date", DESCENDING)])
    await db["orders"].create_index([("company_id", ASCENDING), ("region", ASCENDING)])
    await db["orders"].create_index([("company_id", ASCENDING), ("status", ASCENDING)])
    await db["orders"].create_index([("company_id", ASCENDING), ("customer_name", ASCENDING)])
    await db["orders"].create_index(
        [("company_id", ASCENDING), ("order_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"order_id": {"$type": "string"}},
    )
    await db["orders"].create_index([("company_id", ASCENDING), ("updated_at", DESCENDING)])

    # Customers collection indexes
    await db["customers"].create_index([("company_id", ASCENDING), ("name", ASCENDING)])
    await db["customers"].create_index(
        [("company_id", ASCENDING), ("customer_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"customer_id": {"$type": "string"}},
    )
    await db["customers"].create_index([("company_id", ASCENDING), ("updated_at", DESCENDING)])

    # Products collection indexes
    await db["products"].create_index([("company_id", ASCENDING), ("category", ASCENDING)])
    await db["products"].create_index([("company_id", ASCENDING), ("name", ASCENDING)])
    await db["products"].create_index(
        [("company_id", ASCENDING), ("product_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"product_id": {"$type": "string"}},
    )
    await db["products"].create_index([("company_id", ASCENDING), ("updated_at", DESCENDING)])

    # Invoices collection indexes
    await db["invoices"].create_index([("company_id", ASCENDING), ("status", ASCENDING)])
    await db["invoices"].create_index([("company_id", ASCENDING), ("invoice_date", DESCENDING)])
    await db["invoices"].create_index(
        [("company_id", ASCENDING), ("invoice_number", ASCENDING)],
        unique=True,
        partialFilterExpression={"invoice_number": {"$type": "string"}},
    )
    await db["invoices"].create_index([("company_id", ASCENDING), ("updated_at", DESCENDING)])

    # Stock levels collection indexes
    await db["stock_levels"].create_index([("company_id", ASCENDING), ("warehouse_location", ASCENDING)])
    await db["stock_levels"].create_index([("company_id", ASCENDING), ("quantity_on_hand", ASCENDING)])
    await db["stock_levels"].create_index(
        [("company_id", ASCENDING), ("product_name", ASCENDING), ("warehouse_location", ASCENDING)],
        unique=True,
        partialFilterExpression={
            "product_name": {"$type": "string"},
            "warehouse_location": {"$type": "string"},
        },
    )
    await db["stock_levels"].create_index([("company_id", ASCENDING), ("updated_at", DESCENDING)])

    # App database indexes for company_records and data_imports
    await app_db["company_records"].create_index([("company_id", ASCENDING), ("title", "text"), ("content", "text")])
    await app_db["company_records"].create_index([("company_id", ASCENDING), ("created_at", DESCENDING)])
    await app_db["company_records"].create_index([("company_id", ASCENDING), ("import_id", ASCENDING)])
    await app_db["data_imports"].create_index([("company_id", ASCENDING), ("dataset", ASCENDING), ("status", ASCENDING)])
    await app_db["data_imports"].create_index([("company_id", ASCENDING), ("started_at", DESCENDING)])
    await app_db["data_imports"].create_index([("import_id", ASCENDING)], unique=True)
    await app_db["dataset_catalog"].create_index([("company_id", ASCENDING), ("dataset", ASCENDING)], unique=True)

    # Company files and generic company rows indexes
    await db["company_rows"].create_index([("company_id", ASCENDING), ("file_id", ASCENDING)])
    await db["company_rows"].create_index([("company_id", ASCENDING), ("file_id", ASCENDING), ("import_id", ASCENDING)])
    await app_db["company_files"].create_index([("company_id", ASCENDING), ("slug", ASCENDING)], unique=True)
    await app_db["company_files"].create_index([("company_id", ASCENDING), ("updated_at", DESCENDING)])

    print("Compound business and data import indexes created successfully.")
    await business_writer_client.close()
    await app_client.close()


if __name__ == "__main__":
    asyncio.run(create_business_indexes())
