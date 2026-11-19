# ADR 0003: Opaque 256-bit Server Sessions vs Stateless JWTs

## Status
Accepted

## Context
In an enterprise multi-tenant B2B SaaS platform, security administrators frequently perform critical lifecycle operations:
- Deactivating an employee who departed or had their credentials compromised.
- Suspending an entire tenant due to non-payment or security incident.
- Logging out across all devices.

When using stateless JWTs (JSON Web Tokens), tokens remain valid until their expiration timestamp unless an external revocation blacklist is checked on every single request. Using a revocation blacklist in front of stateless JWTs removes the primary theoretical benefit of JWTs (stateless verification) while retaining their downsides (payload bloat, client-side exposure risks, complex refresh flows).

## Decision
We utilize opaque, cryptographically random 256-bit server sessions stored in MongoDB and indexed via SHA-256 hash:
1. **Opaque Token Generation**: On authentication, a high-entropy random token is generated via `secrets.token_urlsafe(32)`.
2. **Hashed Persistence**: Only the SHA-256 digest of the token is stored in the database (`sessions` collection) with `user_id`, `ip`, `user_agent`, and `expires_at`.
3. **Cookie Transport**: The raw token is stored in an `httpOnly`, `SameSite=Lax`, `Secure` (in production) cookie named `session`.
4. **Immediate Revocation Guarantee**:
   Every incoming request resolving `get_tenant_context` checks the session hash and verifies:
   - User document has `is_active == True`.
   - Tenant document has `status == "active"` (not `"suspended"`).
   Any deactivation or suspension instantly invalidates subsequent requests without waiting for token expiry.
5. **Double Submit CSRF**: Paired with double-submit readable cookie `csrf_token` and `X-CSRF-Token` header on state-changing requests.

## Consequences
- **Positive**: True zero-lag session revocation. Deactivating a user immediately locks them out.
- **Positive**: Immune to client-side token exfiltration via XSS due to `httpOnly` cookie storage.
- **Trade-off**: Requires a database lookup on authenticated requests (mitigated by indexed lookup on `token_hash`).
