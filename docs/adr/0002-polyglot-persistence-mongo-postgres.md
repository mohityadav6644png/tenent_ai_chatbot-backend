# ADR 0002: Polyglot Persistence Architecture (MongoDB for App State, PostgreSQL for Business Data)

## Status
Accepted

## Context
A multi-tenant enterprise AI assistant has two fundamentally divergent data access patterns and requirements:
1. **Application & Operational State**: Tenant management, user identities, opaque sessions, password reset tokens, conversation threads with hierarchical chat turns, JSON follow-up options, audit events, scheduled report configurations, and transient query logs.
2. **Business Analytical Data**: Structured relational enterprise domain data (customers, products, invoices, order line items, inventory) queried arbitrarily by LLM-generated SQL statements.

## Decision
We adopt a strict polyglot persistence architecture:
1. **MongoDB (Application Database)**:
   - Houses `tenants`, `users`, `invites`, `sessions`, `password_resets`, `reports`, `conversations`, `messages`, `feedback`, `audit_events`, `query_log`, and `usage_counters`.
   - Utilizes asynchronous Motor driver for non-blocking I/O in FastAPI.
   - Leverages native MongoDB TTL indexes for automated 30-day session and query log expiries without manual background sweeps.
   - Naturally accommodates polymorphic AI responses (charts, options, message metadata).
2. **PostgreSQL (Business Analytical Database)**:
   - Houses the enterprise relational data that the Text-to-SQL engine translates queries for.
   - Provides strict relational integrity, ACID transactions, and Row Level Security (RLS) enforcement.
   - Exposes sanitised analytical views (`v_customers`, `v_products`, `v_orders`, `v_invoices`, `v_stock`) hiding PII from AI generation.
3. **Redis / In-Memory (Distributed Cache & Rate Limiting)**:
   - Houses fast token counters for rate limiting, sliding window lockout, and generated SQL caching.

## Consequences
- **Positive**: Complete separation of concern. Chat histories and operational logs cannot bloat or lock the business analytical database.
- **Positive**: Business data remains in a pure relational model with optimal index execution paths for SQL generation.
- **Trade-off**: Requires managing connection lifecycles for both Mongo and Postgres engines across application lifespan.
