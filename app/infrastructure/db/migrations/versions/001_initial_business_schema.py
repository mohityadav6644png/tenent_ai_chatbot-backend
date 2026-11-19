"""Initial business schema with RLS and secure views

Revision ID: 001_initial_business_schema
Revises: 
Create Date: 2026-11-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "001_initial_business_schema"
down_revision = None
branch_labels = None
depends_on = None

TABLES = ["customers", "products", "orders", "order_items", "invoices", "stock_levels"]


def upgrade():
    # 1. Create role chatbot_reader if not exists
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'chatbot_reader') THEN
            CREATE ROLE chatbot_reader NOSUPERUSER NOBYPASSRLS LOGIN PASSWORD 'reader_pass';
        END IF;
    END
    $$;
    """)

    # 2. Create tables
    op.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        company_id UUID NOT NULL,
        name TEXT NOT NULL,
        email TEXT,
        phone TEXT,
        city TEXT,
        state TEXT,
        country TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_customers_company_id ON customers(company_id);

    CREATE TABLE IF NOT EXISTS products (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        company_id UUID NOT NULL,
        sku TEXT NOT NULL,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        unit_price NUMERIC(12,2) NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_products_company_id ON products(company_id);

    CREATE TABLE IF NOT EXISTS orders (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        company_id UUID NOT NULL,
        customer_id UUID NOT NULL REFERENCES customers(id),
        order_number TEXT NOT NULL,
        order_date TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL,
        total_amount NUMERIC(12,2) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS idx_orders_company_id ON orders(company_id);

    CREATE TABLE IF NOT EXISTS order_items (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        company_id UUID NOT NULL,
        order_id UUID NOT NULL REFERENCES orders(id),
        product_id UUID NOT NULL REFERENCES products(id),
        quantity INTEGER NOT NULL,
        unit_price NUMERIC(12,2) NOT NULL,
        subtotal NUMERIC(12,2) NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_order_items_company_id ON order_items(company_id);

    CREATE TABLE IF NOT EXISTS invoices (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        company_id UUID NOT NULL,
        order_id UUID NOT NULL REFERENCES orders(id),
        invoice_number TEXT NOT NULL,
        invoice_date TIMESTAMPTZ NOT NULL,
        amount NUMERIC(12,2) NOT NULL,
        tax NUMERIC(12,2) NOT NULL,
        status TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_invoices_company_id ON invoices(company_id);

    CREATE TABLE IF NOT EXISTS stock_levels (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        company_id UUID NOT NULL,
        product_id UUID NOT NULL REFERENCES products(id),
        warehouse_location TEXT NOT NULL,
        quantity_on_hand INTEGER NOT NULL,
        reorder_threshold INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_stock_levels_company_id ON stock_levels(company_id);
    """)

    # 3. Create views hiding PII
    op.execute("""
    CREATE OR REPLACE VIEW v_customers AS
        SELECT id, company_id, name, city, state, country, created_at FROM customers;

    CREATE OR REPLACE VIEW v_products AS
        SELECT id, company_id, sku, name, category, unit_price, is_active, created_at FROM products;

    CREATE OR REPLACE VIEW v_orders AS
        SELECT id, company_id, customer_id, order_number, order_date, status, total_amount, created_at FROM orders;

    CREATE OR REPLACE VIEW v_invoices AS
        SELECT id, company_id, order_id, invoice_number, invoice_date, amount, tax, status FROM invoices;

    CREATE OR REPLACE VIEW v_stock AS
        SELECT id, company_id, product_id, warehouse_location, quantity_on_hand, reorder_threshold FROM stock_levels;
    """)

    # 4. Enable and FORCE RLS on all tables with tenant_isolation policy
    for table in TABLES:
        op.execute(f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS tenant_isolation ON {table};
        CREATE POLICY tenant_isolation ON {table}
            FOR ALL
            USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid);
        """)

    # 5. Grant permissions to chatbot_reader
    op.execute("""
    GRANT SELECT ON customers, products, orders, order_items, invoices, stock_levels TO chatbot_reader;
    GRANT SELECT ON v_customers, v_products, v_orders, v_invoices, v_stock TO chatbot_reader;
    """)


def downgrade():
    for table in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP VIEW IF EXISTS v_stock, v_invoices, v_orders, v_products, v_customers;")
    op.execute("DROP TABLE IF EXISTS stock_levels, invoices, order_items, orders, products, customers CASCADE;")
