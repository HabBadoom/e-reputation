# Security

## Tenant isolation

Defence in depth, each layer independently tested:

| Layer | Mechanism | Test |
|---|---|---|
| Database | RLS `ENABLE` + `FORCE` on every tenant table; API role is `NOBYPASSRLS`; context unset ⇒ zero rows | `tests/sql/test_rls_isolation.sql` (10 checks) |
| Connection | Context set with `set_config(…, true)` (transaction-local) at the start of *every* transaction | SQL check 10 |
| References | Composite FKs `(tenant_id, parent_id)` so a row can't point at another tenant's parent | SQL check 5 |
| API | Tenant taken from the signed token only; membership + role re-read every request; explicit `tenant_id` filters in queries | `tests/test_tenant_isolation.py` |
| Privileges | App role: DML only, no DDL, read-only RBAC tables, append-only audit log | SQL checks 6–7 |

Pre-authentication lookups (login by email, refresh token, invitation token) use three `SECURITY DEFINER` functions with a pinned `search_path` that return only the columns those flows need. `EXECUTE` is revoked from `PUBLIC`.

**Known nuance:** the `memberships` policy also exposes a user's *own* memberships in other tenants (needed for the tenant switcher). Every membership query in application code therefore filters by `tenant_id` explicitly; `test_multi_tenant_user_sees_only_current_tenant` guards this.

## Authentication

- Argon2id (m=64 MiB, t=3, p=4), constant-time path for unknown emails, identical error for bad email and bad password.
- Access JWT (HS256, 15 min) carries `sub`, `tid`, `jti`, `typ`. `alg=none` and foreign signatures are rejected (tested).
- Refresh tokens: random 48 bytes, stored as SHA-256, rotated on every use. Presenting a rotated-out token revokes the whole family (theft detection, tested).
- Browser: `httpOnly; Secure; SameSite=Lax` cookies, refresh cookie scoped to `/api/v1/auth`. Double-submit CSRF token required on unsafe methods (tested). Tokens are never returned in the body to browsers.
- API clients: `X-Auth-Mode: token` returns tokens in the body; send `Authorization: Bearer`.
- Rate limiting: login per-email (10/min) and per-IP (30/min); register per-IP (20/h). Redis-backed and **fails closed** if Redis is down.
- Disabling a user, removing a member or changing a role takes effect on the next request (tested), not at token expiry.

## RBAC

Roles `owner (100) > admin (80) > analyst (50) > viewer (10)`; permissions are rows in `role_permissions`. Rules: nobody changes their own role; you can't grant above your rank; only owners grant `owner`; non-owners can't modify equal/higher members; the last owner can't be removed or demoted.

## Other controls

- Structured error envelope; unhandled exceptions are logged with traceback and returned as a generic 500 with `request_id`.
- Security headers on API and frontend (`nosniff`, `DENY` framing, referrer policy, HSTS in production).
- OpenAPI docs disabled in production.
- Secrets only via environment; startup fails if `REPINTEL_JWT_SECRET` is shorter than 32 chars.
- Containers run as non-root.
- `LIKE` patterns from user search are escaped.

## Not yet implemented (tracked)

| Item | Milestone |
|---|---|
| TOTP MFA, email verification, password reset | 1.1 |
| Session list & remote revoke UI | 1.1 |
| Registration with an invite token (no new org created) | 2 |
| Secret manager integration (Vault / cloud KMS) | 8 |
| SSRF-safe fetcher, egress allowlist for workers | 3 / 6 |
| Prompt-injection defences | 7 |
| Encryption at rest, backups, retention jobs | deployment (8) |

## Legal

Algerian Law 18-07 on personal data protection applies to users' and authors' data. Hosting region, cross-border transfer and data-retention policy **need qualified legal review** before production. See ROADMAP open questions.

## Reporting a vulnerability

Email security@<your-domain> (to be set). Do not open public issues for vulnerabilities.
