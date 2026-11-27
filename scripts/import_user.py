"""
Import / Activate User in MongoDB (backend/scripts/import_user.py)
Inserts or updates a user document into the application database
with proper native datetime objects and exact password hash.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys

# Ensure backend package can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.infrastructure.db.mongodb import (
    close_mongodb_connection,
    connect_to_mongodb,
    users_collection,
)

USER_DOCUMENT = {
    "_id": "6afab7e7e39cbf339f33f939",
    "username": "mohit",
    "email": "mohit@gmail.com",
    "name": "Maria Shah",
    "password": "$argon2id$v=19$m=65536,t=3,p=4$IQroHmMEUpefnpK5CKnP2Q$h+CiaIA9/H4uLBCUhGKdvY/qDxQk1RwIcdDPOU98Rr4",
    "is_verified": True,
    "role": "super_admin",
    "company_id": None,
    "is_active": True,
    "is_invited": False,
    "last_login_at": datetime.fromisoformat("2026-11-23T06:59:09.322+00:00"),
    "created_at": datetime.fromisoformat("2026-11-16T00:00:00.000+00:00"),
    "updated_at": datetime.fromisoformat("2026-11-23T06:59:09.378+00:00"),
}


async def main():
    await connect_to_mongodb()
    result = await users_collection.update_one(
        {"email": USER_DOCUMENT["email"]},
        {"$set": USER_DOCUMENT},
        upsert=True,
    )
    print("User upserted successfully.")
    print(f"Matched: {result.matched_count}, Modified: {result.modified_count}, Upserted ID: {result.upserted_id}")
    
    # Verify in DB
    user = await users_collection.find_one({"email": USER_DOCUMENT["email"]})
    print(f"Stored user: email={user.get('email')}, role={user.get('role')}, active={user.get('is_active')}")
    print(f"Exact password hash length: {len(user.get('password'))}")
    print(f"Password starts with: {user.get('password')[:20]}...")
    await close_mongodb_connection()


if __name__ == "__main__":
    asyncio.run(main())
