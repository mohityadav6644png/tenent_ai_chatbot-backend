"""
MongoDB Async Database Setup (app/infrastructure/db/mongodb.py)
Provides three isolated credentials (App, Business Reader, Business Writer) and collection helpers.
"""

import inspect
from typing import Any
from pymongo import ASCENDING, AsyncMongoClient
from app.core.config import settings
from app.core.exceptions import AppError, SqlValidationFailedError, UnauthenticatedError
from app.core.logging import logger

ALLOWED_BUSINESS_COLLECTIONS = frozenset({"customers", "products", "orders", "invoices", "stock_levels"})

# 1. App Database Client (readWrite on app_data)
client = AsyncMongoClient(settings.MONGODB_URL, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
app_client = client
database = client[settings.MONGODB_DB]
app_database = database

# 2. Business Reader Client (role "read" on business_data only; used by chat and reports)
business_reader_client = AsyncMongoClient(settings.MONGODB_BUSINESS_URL_READER, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
business_database_reader = business_reader_client[settings.MONGODB_BUSINESS_DB]

# 3. Business Writer Client (role "readWrite" on business_data; used ONLY by seed and ingestion)
business_writer_client = AsyncMongoClient(settings.MONGODB_BUSINESS_URL_WRITER, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
business_database_writer = business_writer_client[settings.MONGODB_BUSINESS_DB]

# App database collections
tenants_collection = database["tenants"]
users_collection = database["users"]
invites_collection = database["invites"]
sessions_collection = database["sessions"]
password_resets_collection = database["password_resets"]
reports_collection = database["reports"]
conversations_collection = database["conversations"]
messages_collection = database["messages"]
feedback_collection = database["feedback"]
audit_events_collection = database["audit_events"]
query_log_collection = database["query_log"]
usage_counters_collection = database["usage_counters"]
revoked_tokens_collection = database["revoked_tokens"]
company_records_collection = database["company_records"]


def get_business_collection(ctx: Any, name: str):
    """Retrieve an allowlisted business collection scoped by caller tenant context."""
    company_id = getattr(ctx, "company_id", None)
    if not company_id or not str(company_id).strip():
        raise UnauthenticatedError("Tenant context is missing company_id.")
    if name not in ALLOWED_BUSINESS_COLLECTIONS:
        raise SqlValidationFailedError(f"Collection '{name}' is not allowed.")
    return business_database_reader[name]


async def connect_to_mongodb():
    """Verify MongoDB connectivity and ensure critical indexes exist in app DB."""
    try:
        await client.admin.command("ping")
        await users_collection.create_index("email", unique=True)
        await sessions_collection.create_index("token_hash", unique=True)
        await sessions_collection.create_index("expires_at", expireAfterSeconds=0)
        await invites_collection.create_index([("company_id", ASCENDING), ("email", ASCENDING)])
        await invites_collection.create_index("token_hash")
        await password_resets_collection.create_index("token_hash")
        await password_resets_collection.create_index("expires_at", expireAfterSeconds=0)
        await query_log_collection.create_index("created_at", expireAfterSeconds=2592000)
        await company_records_collection.create_index([("company_id", ASCENDING), ("created_at", ASCENDING)])
        logger.info("MongoDB app client connected and indexes verified")
    except Exception as exc:
        logger.warning(f"Could not connect to MongoDB or set indexes: {exc}")


async def close_mongodb_connection():
    for c in [client, business_reader_client, business_writer_client]:
        try:
            res = c.close()
            if inspect.isawaitable(res):
                await res
        except Exception:
            pass
    logger.info("MongoDB connections closed.")


async def check_db_health() -> bool:
    try:
        await client.admin.command("ping")
        await business_reader_client.admin.command("ping")
        return True
    except Exception:
        return False
