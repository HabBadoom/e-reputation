# Changelog

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
