# ADR 0004: Caching Validated SQL and Response Templates Instead of Row Data

## Status
Accepted

## Context
When enterprise users query business analytics through natural language, LLM generation is the highest latency component (often 1000ms - 3000ms), whereas indexed SQL execution against PostgreSQL is fast (often 5ms - 50ms).

Caching strategies often consider either:
1. Caching the finalized SQL results (the actual row data returned from PostgreSQL).
2. Caching the validated SQL query and presentation template returned by the LLM.

Caching row data poses severe multi-tenant and real-time operational risks:
- **Stale Data**: Inventory changes, new invoices, and sales transactions occurring seconds ago would not reflect in answers until cache eviction.
- **Data Leakage Risk**: If cache isolation has any flaws, raw business rows could potentially be shared.
- **Large Memory Footprint**: Storing large result sets in Redis consumes significant memory.

## Decision
We cache the **validated SQL query and response templates**, NEVER the returned business row data:
1. **Cache Key Structure**: All cache keys are built strictly in `app/infrastructure/azure/redis_cache.py`:
   `v1:{company_id}:{schema_version}:{sha256(normalized_question)}`
2. **Cached Payload**:
   The cache stores:
   - Validated SQL statement
   - Presentation templates (`headline_template`, `detail_template`)
   - Suggested follow-up prompts
3. **Execution on Cache Hit**:
   On a cache hit, LLM generation and AST validation are skipped entirely (reducing latency by ~95%), but the cached SQL is **always executed live** against PostgreSQL with the tenant's `app.company_id` session parameter.
4. **Cache Invalidation on Schema Change**:
   When schema catalog is synchronized via `POST /v1/internal/tenants/{id}/schema/sync`, the tenant's `schema_version` increments, automatically invalidating all prior cached SQL queries for that tenant.

## Consequences
- **Positive**: Answers always reflect live real-time business data (stock levels, invoices, orders).
- **Positive**: Eliminates LLM latency and token costs on repeated or frequent questions.
- **Positive**: Strict tenant isolation guaranteed by both company-scoped cache keys and live RLS execution.
