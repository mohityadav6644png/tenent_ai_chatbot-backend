# Multi-Tenant NLP-to-Mongo SaaS Chatbot Backend

Production-grade multi-tenant AI analytics enterprise backend powered by FastAPI, MongoDB (Motor async), Redis, and Azure OpenAI.

---

## 🏗️ Architecture Overview

The system strictly adheres to Clean / Hexagonal Layered Architecture:
- **Presentation (`app/api/v1/`)**: Thin routers parsing requests, invoking `Depends()`, calling exactly one module service, and returning validated schemas. Zero database driver or query logic.
- **Business Modules (`app/modules/`)**: Pure domain logic (`auth`, `tenants`, `users`, `chat`, `reports`, `audit`, `sql_guard`). Imports only its own interfaces/models and `app.core`. Zero direct imports of database drivers or cloud SDKs.
- **Infrastructure (`app/infrastructure/`)**: External adapters implementing domain interfaces:
  - `infrastructure/db/`: Motor async MongoDB client, isolated credentials, business collection helpers, aggregation pipeline executor, and tenant schema catalog.
  - `infrastructure/azure/`: Azure OpenAI client, Key Vault client, Blob Storage client, Redis cache client, App Insights monitoring.
- **Composition Root (`app/container.py`)**: The single location resolving concrete dependencies.

---

## 🗄️ Databases & Credential Architecture

Two logical MongoDB databases and **three isolated credentials**:
1. **App Database (`MONGODB_DB = app_data`)**:
   - Collections: `tenants`, `users`, `invites`, `sessions`, `password_resets`, `reports`, `conversations`, `messages`, `feedback`, `audit_events`, `query_log` (TTL 30 days, restricted), `usage_counters`.
   - Used by the app credential (`MONGODB_URL`), possessing `readWrite` on `app_data` only.
2. **Business Database (`MONGODB_BUSINESS_DB = business_data`)**:
   - Denormalized collections queried by AI: `customers`, `products`, `orders` (embedded customer, region, status, BSON Date, items), `invoices`, `stock_levels`.
   - Every document contains a mandatory `company_id` (string UUID).
   - Compound indexes start with `company_id` (e.g. `(company_id, order_date)`, `(company_id, region)`).
   - **Two Distinct Credentials**:
     - `MONGODB_BUSINESS_URL_READER`: Role `read` on `business_data` only. Used by `MongoExecutor` and the report runner; **can NEVER write**.
     - `MONGODB_BUSINESS_URL_WRITER`: Role `readWrite` on `business_data`. Used exclusively by `scripts/seed.py` and ingestion paths; **never imported by chat/report paths**.

---

## 🛡️ Multi-Tenant Isolation & Security Invariants

1. **`scope_pipeline` Choke Point**:
   All business queries run through `scope_pipeline(ctx, pipeline)` in `app/infrastructure/db/executor.py`. This injects `{"$match": {"company_id": ctx.company_id}}` as the **mandatory first stage** of every aggregation pipeline, followed by `{"$limit": MAX_ROWS + 1}`. The AI is strictly forbidden from generating or altering this stage.
2. **Pipeline AST Validator (`modules/sql_guard/`)**:
   Enforces 1 to 8 stages, max 8 KB size, max depth 6, allowlisted stages (`$match`, `$group`, `$project`, etc.), forbidden operators blocklist (`$out`, `$merge`, `$lookup`, `$where`, `$function`), and ensures the final stage is `$project` with `"_id": 0`.
3. **Tenant Context Immutability**:
   `company_id` is derived **strictly** from authenticated server session state (`TenantContext`). Any client-supplied `company_id` in request bodies or query parameters is completely ignored.
4. **Opaque Server Sessions & Immediate Revocation**:
   256-bit cryptographically random tokens stored hashed (`SHA-256`) in MongoDB. Immediate revocation on user deactivation or tenant suspension.
5. **Double-Submit CSRF**:
   State-changing requests require matching `csrf_token` cookie and `X-CSRF-Token` header.

---

## 👥 Role Permissions Matrix

| Endpoint Group | Allowed Roles | Isolation Rule |
| :--- | :--- | :--- |
| **Auth** (`/v1/auth/*`) | Public / Authenticated | Constant-time password checks, lockout after 5 failures / 15m. |
| **Chat & AI** (`/v1/chat/*`) | `employee`, `company_admin` | Scoped by `ctx.company_id`; `super_admin` blocked (company_id is None). |
| **Users & Invites** (`/v1/users`, `/v1/invites`) | `company_admin` | Scoped by `ctx.company_id`; acting across companies returns `404 Not Found`. |
| **Reports** (`/v1/reports/*`) | `company_admin` | Scoped by `ctx.company_id`. |
| **Reports Run Internal** (`/v1/internal/reports/run`) | Internal Service | Guarded by constant-time `X-Service-Token`. |
| **Internal Tenants & Audit** (`/v1/internal/*`) | `super_admin` | Returns counts and metadata only; never business rows. |
| **Health** (`/healthz`, `/readyz`) | Public | Liveness / Readiness probes (checks Mongo and Redis). |
| **Internal Health** (`/v1/internal/health`) | `super_admin` | Detailed subsystem status with 15s cache. |

---

## 🚀 Quickstart & Setup (Windows PowerShell)

### 1. Prerequisites & Environment
Ensure Python 3.11+ is installed. Activate your virtual environment:
```powershell
.\venv\Scripts\Activate.ps1
```

Install pinned project dependencies:
```powershell
.\venv\Scripts\python.exe -m pip install -e .
```

### 2. Start Supporting Services (Docker Compose)
Start MongoDB 7 (with auto-provisioned users via `create_db_users.js`) and Redis 7:
```powershell
docker compose -f docker/docker-compose.yml up -d
```

### 3. Create Business Indexes
Create compound indexes starting with `company_id`:
```powershell
.\venv\Scripts\python.exe scripts/create_indexes.py
```

### 4. Seed Initial Data
Seed demo tenants (Acme Traders, Bharat Foods), initial users, and 6+ months of business data:
```powershell
.\venv\Scripts\python.exe scripts/seed.py
```

### 5. Start the Development Server
```powershell
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
- Interactive API Documentation: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- OpenAPI Specification: `docs/openapi.json`

---

## 🧪 Testing & Verification

### Run Full Test Suite
```powershell
.\venv\Scripts\python.exe -m pytest -v
```

### Run Pipeline Validator Unit Tests
```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/test_sql_guard.py -v
```

### Run Multi-Tenant Business Isolation Integration Tests
```powershell
.\venv\Scripts\python.exe -m pytest tests/integration/test_mongo_integration.py -v
```

### Run Security CI-Blocking Tests
```powershell
.\venv\Scripts\python.exe -m pytest tests/security/ -v
```

### Run Accuracy Benchmark Evaluation Suite
```powershell
.\venv\Scripts\python.exe -m pytest tests/eval/ -v
```

---

## 🤖 Free AI Providers

The platform supports free-tier AI providers with in-process concurrency limiting, exponential backoff retries, and strict schema validation:

### 1. Google Gemini (Free Tier)
1. Get a free API key at [Google AI Studio](https://aistudio.google.com/).
2. Configure `.env`:
   ```bash
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=your_key_here
   GEMINI_MODEL=gemini-2.5-flash
   GEMINI_MAX_RETRIES=2
   LLM_MAX_REQUESTS_PER_MINUTE=10
   LLM_MAX_CONCURRENCY=3
   ```
3. Verify connection:
   ```powershell
   python -m scripts.llm_check
   ```

### 2. OpenAI-Compatible Providers (Groq, OpenRouter, Mistral, Ollama)
1. **Groq** (Fast free inference):
   - Register at [Groq Console](https://console.groq.com/) and create an API key.
   - Configure `.env`:
     ```bash
     LLM_PROVIDER=openai_compat
     LLM_API_BASE=https://api.groq.com/openai/v1
     LLM_API_KEY=your_groq_key_here
     LLM_MODEL=llama-3.3-70b-versatile
     ```
2. **OpenRouter** (Aggregated models):
   - Register at [OpenRouter](https://openrouter.ai/) and create an API key.
   - Configure `.env`:
     ```bash
     LLM_PROVIDER=openai_compat
     LLM_API_BASE=https://openrouter.ai/api/v1
     LLM_API_KEY=your_openrouter_key_here
     LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
     ```

### Configuration Variables Guide
* `LLM_PROVIDER`: Active generator (`gemini`, `openai_compat`, `azure`, or `fake`).
* `GEMINI_API_KEY`: API key for Google GenAI SDK.
* `GEMINI_MODEL`: Model name for Gemini (no hardcoded default).
* `GEMINI_MAX_RETRIES`: Number of retry attempts on rate limits with exponential backoff (default: `2`).
* `LLM_MAX_REQUESTS_PER_MINUTE`: In-process rate limiter preventing quota exhaustion (default: `10`).
* `LLM_MAX_CONCURRENCY`: In-process maximum parallel queries to the LLM (default: `3`).
* `LLM_API_BASE`: Base URL for OpenAI-compatible API.
* `LLM_API_KEY`: Secret API key for OpenAI-compatible endpoint.
* `LLM_MODEL`: Target model identifier on the provider.
* `LLM_TIMEOUT_SECONDS`: Client HTTP timeout (default: `20.0`).
* `LLM_MAX_OUTPUT_TOKENS`: Maximum generated tokens (default: `2048`).
* `LLM_TEMPERATURE`: Generation temperature (default: `0.0`).
* `LLM_FALLBACK_PROVIDER`: Optional fallback provider on non-retryable errors (empty by default; no silent fallback).

### ⚠️ Privacy and Data Policy Notice
* **Dynamic Rate Limits:** Free tiers enforce strict rate and daily quota limits that change without notice. When daily quota is exhausted, requests return `LLM_UNAVAILABLE` with a retry delay.
* **Training on Inputs:** Google AI Studio free tier terms allow prompts to be reviewed and used to improve Google products. Mistral free tier may train on inputs.
* **Production Data:** Any real customer, proprietary, or regulated data should be routed through a paid tier (e.g., Azure OpenAI, paid Gemini API, or dedicated models) where contractual terms prohibit training on customer data.
#   t e n e n t _ a i _ c h a t b o t - b a c k e n d  
 

## Company files

Multi-tenant CSV file management and chatbot data source.

### Endpoints
- GET /v1/company/files: List company files with summary
- POST /v1/company/files/preview: Preview CSV upload (types, suggested hidden, validation errors)
- POST /v1/company/files: Create company file with multipart file and meta JSON
- GET /v1/company/files/{id}: Get file detail with column definitions
- PATCH /v1/company/files/{id}: Update file metadata, column meanings, or hidden flags
- POST /v1/company/files/{id}/upload: Re-upload CSV data (replace or append mode)
- DELETE /v1/company/files/{id}: Delete file metadata and all associated data rows

### Settings
- FILES_MAX_PER_COMPANY (default: 10): Maximum files allowed per tenant
- IMPORT_MAX_FILE_MB (default: 5): Maximum file size in MB
- IMPORT_MAX_ROWS (default: 50000): Maximum rows per CSV file
- IMPORT_MAX_COLUMNS (default: 30): Maximum columns per CSV file
- IMPORT_MAX_CELL_CHARS (default: 2000): Maximum character length per cell
- IMPORT_MAX_PER_HOUR (default: 10): Maximum file import/upload operations per hour
- FILES_CATALOG_MAX_KB (default: 15): Maximum size of schema catalog injected into generator prompt
- IMPORT_PREVIEW_ERRORS (default: 500): Maximum preview errors captured

### Rules
- Access is restricted strictly to company_admin with valid session cookie and CSRF token (X-CSRF-Token).
- company_id is always derived exclusively from authenticated TenantContext; never accepted from client input.
- Data rows are stored in company_rows within the business database, isolated by company_id and ile_id.
- Hidden columns are completely excluded from AI schema catalog and stats, rejected by query validator, and stripped via $unset in scope_file_pipeline.
