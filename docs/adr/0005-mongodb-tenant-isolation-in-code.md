# ADR 0005: Enforcing Multi-Tenant Isolation in Code for MongoDB

## Status
Accepted

## Context
Unlike PostgreSQL, MongoDB does not offer built-in native Row-Level Security (RLS) policies within shared collections. Because enterprise tenant collections are shared across organizations within `business_data`, multi-tenant boundary enforcement cannot rely on database engine policies alone.

If query generation, execution, and credential separation are not strictly isolated, malicious prompt injections or un-scoped queries could leak another tenant's documents.

## Decision
We enforce multi-tenant isolation through four defense-in-depth controls centered in application code:
1. **Single Choke Point (`scope_pipeline`)**:
   All business queries run through `scope_pipeline(ctx, pipeline)` in `app/infrastructure/db/executor.py`. This function injects `{"$match": {"company_id": ctx.company_id}}` as the **mandatory first stage** of every aggregation pipeline, followed by the validated pipeline and `{"$limit": MAX_ROWS + 1}`. The AI is forbidden from generating or managing this stage.
2. **Pipeline Validator (`modules/sql_guard/`)**:
   Strict AST validation rejects any query that references or projects `company_id`, attempts collection traversal (`$lookup`, `$graphLookup`, `$unionWith`), or calls forbidden write/scripting operators (`$out`, `$merge`, `$where`, `$function`).
3. **Read-Only Database Credential (`MONGODB_BUSINESS_URL_READER`)**:
   The AI chat and report paths connect exclusively through `business_reader_client`, which possesses read-only permissions on `business_data` and cannot mutate or create collections.
4. **Abstracted Scoping Boundary for Future Database-Per-Tenant Upgrade**:
   All collection access and scoping logic are confined strictly behind:
   - `get_business_collection(ctx, name)` in `app/infrastructure/db/mongodb.py`
   - `scope_pipeline(ctx, pipeline)` in `app/infrastructure/db/executor.py`

When the platform transitions to a database-per-tenant architecture (`business_data_{company_id}`), only those two functions will need updating, leaving the business modules, services, and AI generation contracts completely unchanged.

## Consequences
- **Positive**: Strict, non-bypassable tenant isolation without relying on client-supplied filters.
- **Positive**: Zero cross-tenant data leakage guaranteed by code-injected root `$match`.
- **Positive**: Clear, isolated upgrade path to database-per-tenant architecture.
- **Trade-off**: Requires rigorous regression testing of `scope_pipeline` and `get_business_collection` on every build.

