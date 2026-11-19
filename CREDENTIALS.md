# Login Credentials & Test Data Reference

All accounts below are seeded in MongoDB (`scripts/seed.py`) and work on both the **Frontend Login Page** (`http://localhost:5173/login`) and **Swagger UI** (`http://127.0.0.1:8000/docs`).

---

## 1. User Login Credentials

### Super Admin (Platform Owner)
*Redirects to `/platform/tenants` after frontend login.*

| Name | Email | Password | Role | Company |
| :--- | :--- | :--- | :--- | :--- |
| Platform Owner | `owner@platform.com` | `Demo@1234` | `super_admin` | Platform-wide (`None`) |
| Demo Super Admin | `superadmin@example.com` | `SuperAdmin123!` | `super_admin` | Platform-wide (`None`) |

---

### Company Admin (Tenant Administrator)
*Redirects to `/admin/users` after frontend login. Can manage team users, invites, reports, and chat.*

| Name | Email | Password | Role | Company |
| :--- | :--- | :--- | :--- | :--- |
| Asha Patel | `asha@acme.in` | `Demo@1234` | `company_admin` | Acme Traders |
| Ravi Singh | `companyadmin@example.com` | `CompanyAdmin123!` | `company_admin` | Acme Traders |
| Vikram Patel | `vikram@bharat.in` | `Demo@1234` | `company_admin` | Bharat Foods |

---

### Company Employee (Standard User)
*Redirects directly to `/chat` after frontend login. Can ask AI questions, view conversations, and submit feedback.*

| Name | Email | Password | Role | Company |
| :--- | :--- | :--- | :--- | :--- |
| Rohan Sharma | `employee@acme.in` | `Demo@1234` | `employee` | Acme Traders |
| Demo Employee | `demo@example.com` | `Demo123!` | `employee` | Acme Traders |

---

## 2. Seeded Test IDs & Tokens

Use these pre-seeded IDs and tokens when testing invite, password reset, tenant, report, and feedback APIs:

### Tenants (`app_data.tenants`)
| Company Name | Tenant ID (`_id`) | Status |
| :--- | :--- | :--- |
| Acme Traders | `11111111-1111-1111-1111-111111111111` | `active` |
| Bharat Foods | `22222222-2222-2222-2222-222222222222` | `active` |
| Global Retail | `33333333-3333-3333-3333-333333333333` | `suspended` |

### Auth Flow Tokens (`app_data.invites`, `app_data.password_resets`)
* **Pending Invite (`newhire@acme.in`):**
  * **Invite ID:** `6aed88887c9b4d71857865aa`
  * **Plain Token (for `POST /v1/auth/accept-invite`):** `demo_invite_token_123`
* **Password Reset (`employee@acme.in`):**
  * **Plain Token (for `POST /v1/auth/reset-password`):** `demo_reset_token_123`

### Scheduled Reports (`app_data.reports` for Acme Traders)
| Report Name | Report ID | Status |
| :--- | :--- | :--- |
| Weekly Sales Summary | `6aed694d7c9b4d71857865ab` | `active` |
| Low Stock Alert | `6aed694d7c9b4d71857865ac` | `active` |
| Monthly Overdue Invoices | `6aed694d7c9b4d71857865ad` | `paused` |

### Conversations & Messages (`app_data.conversations`, `app_data.messages`)
* **Conversation ID:** `6aed79f07c9b4d71857865b1`
* **Message ID (for `POST /v1/feedback`):** `6aed79f07c9b4d71857865b2`

### Internal Service Header
* **Header:** `X-Service-Token: internal-service-secret-token` (default in `app/core/config.py`)

---

## 3. Reset / Re-seed Database Command
To reset all demo credentials and sample data in MongoDB at any time, run:
```powershell
.\venv\Scripts\python.exe scripts\seed.py
```
