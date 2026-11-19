# ADR 0001: PostgreSQL Row Level Security (RLS) as True Isolation Boundary

## Status
Accepted

## Context
In a multi-tenant Text-to-SQL SaaS application, an AI model translates untrusted user natural language queries into SQL. Even with strict AST parsing and validation (SQL Guard), prompt injection, malformed SQL, or logic bugs in the application layer could generate queries that span across tenant boundaries or omit `WHERE company_id = ...` filters.

If multi-tenant isolation relies solely on the application code or the LLM's prompt adherence, any failure in the application layer or LLM prompt defense directly results in cross-tenant data leaks.

## Decision
We enforce multi-tenant isolation at the database engine level via PostgreSQL Row Level Security (RLS):
1. **Base Tables & RLS Policies**: Every business table (`customers`, `products`, `orders`, `order_items`, `invoices`, `stock_levels`) has `company_id uuid NOT NULL`, with `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`.
2. **Session Parameterization**: Tenant context is communicated to PostgreSQL in an explicit read-only transaction:
   ```sql
   SET TRANSACTION READ ONLY;
   SELECT set_config('app.company_id', :company_id, true);
   ```
3. **Fail-Closed Security**: The policy condition checks `company_id = current_setting('app.company_id', true)::uuid`. If `app.company_id` is missing or unset, it returns `NULL`, which evaluates to `UNKNOWN` in SQL, causing the query to return zero rows.
4. **Least-Privilege Role**: The AI query executor connects exclusively using the unprivileged `chatbot_reader` role. This role possesses `NOBYPASSRLS` and has `SELECT` privileges only on sanitised views (`v_*`), with zero permissions to alter tables, policies, or system catalogs.

## Consequences
- **Positive**: Complete defense-in-depth. Even if the LLM generates a cross-company query (e.g. `SELECT * FROM v_orders;` without a `WHERE` clause) or attempts UNION injection, PostgreSQL strictly filters out any rows not belonging to `app.company_id`.
- **Positive**: App-level bypasses cannot expose other tenants' data.
- **Trade-off**: Requires opening each connection transaction with `set_config` and rolling back on completion to return clean pooled connections.
