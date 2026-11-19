"""
Seed Constants (scripts/seed_constants.py)
Provides tenant IDs, demo tokens, and reference fixtures for database seeding.
"""

from datetime import datetime, timezone
from app.core.security import hash_password, hash_token

ACME_ID = "11111111-1111-1111-1111-111111111111"
BHARAT_ID = "22222222-2222-2222-2222-222222222222"
SUSPENDED_ID = "33333333-3333-3333-3333-333333333333"

DEMO_PASSWORD_HASH = hash_password("Demo@1234")
DEMO_SUPERADMIN_HASH = hash_password("SuperAdmin123!")
DEMO_COMPANYADMIN_HASH = hash_password("CompanyAdmin123!")
DEMO_EMPLOYEE_HASH = hash_password("Demo123!")

DEMO_INVITE_TOKEN = "demo_invite_token_123"
DEMO_INVITE_HASH = hash_token(DEMO_INVITE_TOKEN)
DEMO_RESET_TOKEN = "demo_reset_token_123"
DEMO_RESET_HASH = hash_token(DEMO_RESET_TOKEN)

PRODUCTS_CATALOG = [
    {"name": "Basmati Rice 5kg", "category": "Grains", "price": 450},
    {"name": "Organic Turmeric 500g", "category": "Spices", "price": 180},
    {"name": "Mustard Oil 1L", "category": "Oils", "price": 160},
    {"name": "Toor Dal 1kg", "category": "Pulses", "price": 150},
    {"name": "Whole Wheat Atta 10kg", "category": "Flours", "price": 420},
    {"name": "Green Cardamom 100g", "category": "Spices", "price": 320},
]

STOCK_FIXTURES = [
    {"product_name": "Basmati Rice 5kg", "warehouse_location": "Mumbai-Central", "quantity_on_hand": 18, "reorder_threshold": 20},
    {"product_name": "Organic Turmeric 500g", "warehouse_location": "Delhi-Hub", "quantity_on_hand": 120, "reorder_threshold": 30},
    {"product_name": "Mustard Oil 1L", "warehouse_location": "Mumbai-Central", "quantity_on_hand": 8, "reorder_threshold": 25},
    {"product_name": "Toor Dal 1kg", "warehouse_location": "Bengaluru-South", "quantity_on_hand": 65, "reorder_threshold": 40},
    {"product_name": "Whole Wheat Atta 10kg", "warehouse_location": "Delhi-Hub", "quantity_on_hand": 14, "reorder_threshold": 20},
]
