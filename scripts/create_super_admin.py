"""Create a one-time super-admin account in the application MongoDB database."""

import asyncio
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.core.security import hash_password
from app.infrastructure.db.mongodb import (
    client,
    close_mongodb_connection,
    users_collection,
)
from app.modules.auth.models import create_user_document


async def create_super_admin() -> int:
    email = input("Super-admin login email: ").strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        print("Enter a valid email address.", file=sys.stderr)
        return 1

    default_username = email.split("@", 1)[0]
    username = input(f"Username [{default_username}]: ").strip() or default_username
    name = input("Display name [Platform Super Admin]: ").strip() or "Platform Super Admin"

    await client.admin.command("ping")
    await users_collection.create_index("email", unique=True)

    if await users_collection.find_one({"email": email}, {"_id": 1}):
        print("A user with this email already exists; no account was changed.", file=sys.stderr)
        return 1

    password = secrets.token_urlsafe(24)
    user_doc = create_user_document(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        name=name,
        role="super_admin",
        company_id=None,
        is_active=True,
        is_invited=False,
    )

    try:
        await users_collection.insert_one(user_doc)
    except DuplicateKeyError:
        print("A user with this email already exists; no account was changed.", file=sys.stderr)
        return 1

    print(f"Created super admin in database '{settings.MONGODB_DB}'.")
    print(f"Email: {email}")
    print(f"Username: {username}")
    print(f"Temporary password (save it now): {password}")
    return 0


async def main() -> int:
    try:
        return await create_super_admin()
    finally:
        await close_mongodb_connection()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
