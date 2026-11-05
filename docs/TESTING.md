# Testing

## Suites

| Suite | What it proves | Command |
|---|---|---|
| `backend/tests/sql/test_rls_isolation.sql` | Tenant isolation **in the database itself**, run as the API role: fail-closed without context, no cross-tenant read/write/move, composite FKs, append-only audit, app role can't disable RLS or edit RBAC, context doesn't leak across transactions | `make test-sql` |
| `backend/tests/test_tenant_isolation.py` | Mandatory Tenant A / Tenant B test through the API: lists, direct IDs, search, aliases, members, invitations, audit, dashboard, tenant switching, forged/unsigned JWTs | `make test-api` |
| `backend/tests/test_auth.py` | Registration, login, indistinguishable failures, refresh rotation + reuse detection, logout, cookie + CSRF flow, rate limiting, disabled users | 〃 |
| `backend/tests/test_rbac.py` | Viewer/analyst/admin/owner permissions, escalation rules, last-owner protection, immediate revocation | 〃 |
| `backend/tests/test_brands.py` | Brand CRUD, multi-script aliases, duplicates, LIKE escaping, pagination, script heuristic | 〃 |

Tests use a real PostgreSQL (no SQLite, no mocks of RLS). Each test truncates data tables via the owner role.

## Running locally

```bash
docker compose up -d postgres redis
cd backend && uv sync
export REPINTEL_DATABASE_URL=postgresql+psycopg://repintel_app:repintel_app@localhost:5432/repintel
export REPINTEL_MIGRATION_DATABASE_URL=postgresql+psycopg://repintel_owner:repintel_owner@localhost:5432/repintel
uv run alembic upgrade head
make test
```

## Without Alembic installed

`scripts/render_migration_sql.py` renders a migration to plain SQL (`upgrade` or `downgrade`) so the schema and RLS suite can be exercised with `psql` alone. This is how the RLS suite was first verified.

## Verification status of Milestone 1

| Check | Status |
|---|---|
| Migration applies, downgrades to empty, re-applies (real Postgres 16) | ✅ run |
| RLS suite, 10 checks (+ mutation test: weakening a policy makes it fail) | ✅ run |
| Alias script heuristic, 11 cases | ✅ run |
| Frontend: all 20 TS/TSX files parse; `i18n.ts` and `api.ts` pass `tsc --strict`; fr/ar dictionaries match en keys | ✅ run |
| API pytest suites, `ruff`, full frontend typecheck/lint/build | ⏳ first run in CI — the authoring environment had no access to PyPI/npm |

## Planned

Integration tests for queues/connectors (M2–3), NLP benchmark `ALGERIAN_NLP_BENCHMARK` (M4), browser tests (M6), prompt-injection and hallucination-resistance evals (M7), Playwright E2E from registration to dashboard (M3, once mentions exist).
