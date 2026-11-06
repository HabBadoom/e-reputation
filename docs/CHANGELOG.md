# Changelog

## 0.3.0 — Milestone 2: Brands & monitoring (2026-11-06)

### Added
- Migration 0003: products, competitors, brand keywords, monitoring projects & runs, plans / entitlements / subscriptions, PostgreSQL job queue with scheduler function; `monitoring:read` / `monitoring:write` permissions.
- Background worker (`python -m app.jobs.worker`, compose service `worker`): scheduler tick, retries with exponential backoff, dead-letter, stale-job recovery.
- Emails (verification, reset, invitations) now go through the queue; invitation emails; sign-up through an invitation joins that organization without creating a new one.
- Monitoring projects: frequency, languages, competitors toggle, pause/resume, run now, run history, query-plan preview. Runs record what would be searched and end as "nothing collected" until connectors exist (Milestone 3).
- Sessions: list, sign out one device, sign out everywhere else; logout and revocation are immediate.
- Plan limits from the database (brands, projects, members incl. pending invitations, fastest frequency, competitors and keywords per brand), plan & usage view.
- UI: brand catalog (products, competitors, keywords), Monitoring page, sessions, plan & usage, translated error messages.

### Changed
- Access tokens carry `sid`; tokens without it are rejected (clients refresh transparently).
- Dashboard summary adds catalog/project counts and the last run.
- **Deviation from spec**: job queue in PostgreSQL instead of RabbitMQ (see ARCHITECTURE.md decisions).

## 0.2.0 — Milestone 1.1: Account security (2026-11-06)

### Added
- Migration 0002: `user_tokens`, `user_mfa`, `mfa_recovery_codes` (own-row RLS), `users.email_verified_at`, `auth_find_user_token()`.
- Transactional email via **Brevo** behind a provider interface (console for dev, memory for tests); templates in French, Arabic (RTL) and English.
- Email verification (sent at registration, resend, required to accept invitations).
- Password reset by email and password change; both revoke other sessions.
- TOTP two-step verification with QR setup, replay protection, 10 single-use recovery codes, secrets encrypted at rest (Fernet, rotatable keys).
- UI: login MFA step, forgot/reset/verify pages, "My account" page, unverified-email banner.
- Tests: RFC 6238 vectors, user-table RLS suite, account-security API suite.

### Changed
- `/auth/login` may return an MFA challenge instead of a session.
- `/auth/me` includes `email_verified` and `mfa_enabled`.
- New required setting `REPINTEL_ENCRYPTION_KEYS`.

## 0.1.0 — Milestone 1: Foundation (2026-11-05)

### Added
- Monorepo: FastAPI backend, Next.js frontend, Docker Compose (Postgres+pgvector, Redis, RabbitMQ, optional OpenSearch), GitHub Actions CI.
- Schema migration 0001: tenants, users, RBAC (4 roles, 10 permissions), memberships, refresh tokens, invitations, append-only audit log, brands, brand aliases. RLS forced on all tenant tables; restricted API role; narrow SECURITY DEFINER auth lookups.
- Auth: register, login, refresh rotation with reuse detection, logout, switch tenant; cookie + CSRF for browsers, bearer for API clients; Argon2id; rate limiting.
- Organization settings, members (role changes with rank rules), invitations with one-time links.
- Brands with multi-script aliases (Arabic, Latin, Arabizi, Tifinagh), search, pagination.
- Dashboard summary with honest "not available yet" states for mentions/sentiment/sources.
- UI in French, Arabic (RTL) and English.
- Tests: SQL RLS suite (run), API suites for isolation/auth/RBAC/brands (CI).

### Notes
- Master prompt §9 said the AI "must automatically solve or bypass CAPTCHA"; treated as a typo for "must not", consistent with the rest of §9 and rule 22.12.
- `argon2-cffi` replaced by `cryptography`'s Argon2id (one fewer native dependency).
