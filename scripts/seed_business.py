"""
Business Data Seeding (scripts/seed_business.py)
Seeds 6+ months of realistic denormalized business documents (orders, invoices, customers, stock).
"""

from datetime import datetime, timedelta, timezone
from app.infrastructure.db.mongodb import business_database_writer
from scripts.seed_constants import ACME_ID, BHARAT_ID, PRODUCTS_CATALOG, STOCK_FIXTURES


async def seed_business_data():
    b_db = business_database_writer
    now = datetime.now(timezone.utc)

    for cid, cname in [(ACME_ID, "Acme Traders"), (BHARAT_ID, "Bharat Foods")]:
        # Customers
        await b_db["customers"].delete_many({"company_id": cid})
        custs = [
            {"company_id": cid, "name": f"{cname} Retail Mumbai", "city": "Mumbai", "region": "West", "created_at": now - timedelta(days=200)},
            {"company_id": cid, "name": f"{cname} Wholesale Delhi", "city": "Delhi", "region": "North", "created_at": now - timedelta(days=180)},
            {"company_id": cid, "name": f"{cname} Mart Bengaluru", "city": "Bengaluru", "region": "South", "created_at": now - timedelta(days=90)},
        ]
        await b_db["customers"].insert_many(custs)

        # Products
        await b_db["products"].delete_many({"company_id": cid})
        await b_db["products"].insert_many([dict(p, company_id=cid) for p in PRODUCTS_CATALOG])

        # 6+ months of Orders
        await b_db["orders"].delete_many({"company_id": cid})
        orders = []
        for i in range(1, 28):
            o_date = now - timedelta(days=i * 7)
            orders.append({
                "company_id": cid,
                "customer_name": f"{cname} Retail Mumbai" if i % 2 == 0 else f"{cname} Wholesale Delhi",
                "region": "West" if i % 3 == 0 else ("North" if i % 3 == 1 else "South"),
                "status": "completed" if i % 5 != 0 else "pending",
                "order_date": o_date,
                "total": 3200 + (i * 280),
                "items": [
                    {"product_name": "Basmati Rice 5kg", "category": "Grains", "qty": 4, "price": 450},
                    {"product_name": "Organic Turmeric 500g", "category": "Spices", "qty": 5, "price": 180},
                ],
            })
        await b_db["orders"].insert_many(orders)

        # Invoices
        await b_db["invoices"].delete_many({"company_id": cid})
        invs = [
            {"company_id": cid, "invoice_number": f"INV-{cid[:4]}-001", "invoice_date": now - timedelta(days=60), "amount": 12500, "status": "paid"},
            {"company_id": cid, "invoice_number": f"INV-{cid[:4]}-002", "invoice_date": now - timedelta(days=45), "amount": 8400, "status": "overdue"},
            {"company_id": cid, "invoice_number": f"INV-{cid[:4]}-003", "invoice_date": now - timedelta(days=10), "amount": 15000, "status": "unpaid"},
        ]
        await b_db["invoices"].insert_many(invs)

        # Stock Levels
        await b_db["stock_levels"].delete_many({"company_id": cid})
        await b_db["stock_levels"].insert_many([dict(s, company_id=cid) for s in STOCK_FIXTURES])

    print("[OK] Business database seeded with customers, products, 6+ months of orders, invoices, stock.")

