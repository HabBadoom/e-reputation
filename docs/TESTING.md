# Testing

## Suites

| Suite | What it proves | Command |
|---|---|---|
| `backend/tests/sql/test_rls_isolation.sql` | Tenant isolation **in the database itself**, run as the API role: fail-closed without context, no cross-tenant read/write/move, composite FKs, append-only audit, app role can't disable RLS or edit RBAC, context doesn't leak across transactions | `make test-sql` |
| `backend/tests/test_tenant_isolation.py` | Mandatory Tenant A / Tenant B test through the API: lists, direct IDs, search, aliases, members, invitations, audit, dashboard, tenant switching, forged/unsigned JWTs | `make test-api` |
| `backend/tests/test_auth.py` | Registration, login, indistinguishable failures, refresh rotation + reuse detection, logout, cookie + CSRF flow, rate limiting, disabled users | 〃 |
| `backend/tests/test_rbac.py` | Viewer/analyst/admin/owner permissions, escalation rules, last-owner protection, immediate revocation | 〃 |
| `backend/tests/sql/test_rls_user_tables.sql` | Tokens, MFA secrets and recovery codes are visible/writable only by their owner; token lookup is purpose-bound | `make test-sql` |
| `backend/tests/test_totp.py` | RFC 6238 Appendix B vectors, drift window, replay rejection | `make test-api` |
| `backend/tests/test_account_security.py` | Verification (localized email, single use, expiry, resend, invitation gate), reset (no enumeration, single use, sessions revoked), change password, MFA (challenge, replay, recovery codes, attempt limit, disable, encryption at rest, revoked member) | 〃 |
| `backend/tests/sql/test_rls_monitoring.sql` | Catalog/monitoring/subscription isolation, composite FKs, plans read-only, jobs unreachable directly, tenant-bound enqueue, scheduler once-only, no double claim, retry → dead-letter | `make test-sql` |
| `backend/tests/test_monitoring.py` | Catalog CRUD and validation, viewer restrictions, cross-tenant 404s on every catalog/monitoring route, manual run → honest `skipped` result, scheduler (due/paused/resume), query plan | `make test-api` |
| `backend/tests/test_platform.py` | Queue retries/backoff/dead-letter/unknown kind, transactional enqueue, email retry on provider failure, sessions list/revoke/immediate logout, plan limits (402), subscriptions, invitation email + sign-up through invitation | 〃 |
| `backend/tests/test_query_plan.py` | Query plan grouping, dedupe, exclusions, search string | 〃 |
| `backend/tests/sql/test_rls_collection.sql` | Sources/mentions isolation, cross-tenant source links refused, connector approval not writable by tenants | `make test-sql` |
| `backend/tests/test_collection.py` | Full run with mocked Serper + pages + RSS: verification, robots, exclusions, reposts, invalid URLs, provenance, registry states, no duplicates on re-run, filters, CSV injection, auth failure, production compliance gate, sources API, feed discovery, SSRF via sources, isolation, viewer rights | `make test-api` |
| `backend/tests/test_fetcher.py` | SSRF: private/metadata/IPv6-mapped/mixed DNS, ports, schemes, credentials, malicious redirects, size cap, redirect loops; robots.txt semantics | 〃 |
| `backend/tests/test_collection_utils.py` | URL canonicalisation, Arabic/French matching, excerpts, hashes, dates (absolute, relative in en/fr/ar), HTML extraction, RSS/Atom | 〃 |
| `backend/tests/sql/test_rls_nlp.sql` | Analysis isolation, corpus opt-in enforcement, corpus unreadable by non-admins, platform-admin flag protected, opt-out purge | `make test-sql` |
| `backend/tests/test_nlp_basics.py` | Script/language/Darija/code-switching detection, normaliser traceability, metrics (macro-F1, confusion, coverage, multi-label) | `make test-api` |
| `backend/tests/test_nlp_ensemble.py` | Majority, arbitration, UNCERTAIN, invalid outputs rejected, fatal errors, injection framing, lexicon hints | 〃 |
| `backend/tests/test_nlp.py` | Mocked OpenAI end to end: 3 annotators, arbitration, not-configured/production gate, auth failure stops batch, corrections/filters/permissions/isolation, corpus opt-in & purge, gold review, benchmark metrics, validation flags, analysis after collection | 〃 |
| `backend/tests/sql/test_rls_intelligence.sql` | Alerts, rules, webhooks, crises, reports isolated; no cross-tenant webhook wiring; https only; alert de-duplication; evaluation scheduled once per tenant, on-demand evaluation bound to the caller's tenant | `make test-sql` |
| `backend/tests/test_analytics_stats.py` | Reputation index, Poisson z, spikes, emerging topics, crisis assessment, share of voice | `make test-api` |
| `backend/tests/test_intelligence.py` | Overview counts (reposts/homonyms excluded, min sample), crisis open → no duplicates → resolved, undated/old mentions don't alert, competitor spike, min severity, rule validation & RBAC, ack/resolve, signed webhook delivery + HMAC check + failure recorded + SSRF refusal, reports CSV/XLSX/HTML/PDF, period validation, isolation | 〃 |
| `backend/tests/sql/test_rls_browser.sql` | Missions/events/challenges isolated, no cross-tenant source, missions must start CREATED, illegal transitions refused, every transition audited, fleet tables admin-read-only, resource-aware claim (priority, one per domain, cooldown), exponential back-off, recovery (requeue / fail / expire) | `make test-sql` |
| `backend/tests/test_browser_detect.py` | Each challenge family detected; ordinary pages (invisible reCAPTCHA, static JS shell, article about CAPTCHAs, 404) are not | `make test-api` |
| `backend/tests/test_browser.py` | **Real Chromium** vs local server: JS page rendered and verified, internal requests refused, cleanup verified; static challenge → fallback, cooldown, no re-knock, restriction after 3; challenge in the browser stops the mission; redirect to an internal address blocked; permissions, isolation, cancel, fleet view; transitions identical in code and DB | 〃 |
| `backend/tests/sql/test_rls_agents.sql` | Tasks/runs/actions/approvals isolated; approval-required actions cannot execute without approval; viewers cannot approve; decisions are final | `make test-sql` |
| `backend/tests/test_agents.py` | All agents on seeded data (tool allowlists respected, no model for analysis), autonomous low-risk action, drafts wait for approval (RBAC, edit, reject, expiry), sensitive actions re-checked, honest degradation without AI provider, permission ceiling; **prompt-injection suite** | `make test-api` |
| `backend/tests/sql/test_rls_billing.sql` | Billing accounts, usage and requests isolated between tenants; members cannot create invoices, activate their subscription, set prices, list organisations or take an invoice number; expired trials detected (admin flows and immutability are in `test_billing.py`) | `make test-sql` |
| `backend/tests/test_billing.py` | Admin console needs the platform flag **and** MFA; billing RBAC; invoice lifecycle (totals, buyer/seller checks, gapless numbers, immutability in API and DB, paid, void, deleted drafts, PDF, customer view, audit in the customer's log); draft from the subscription price; plan catalogue, entitlements validation, plan requests + e-mail + subscription change; connector review; trial expiry (402, data readable, e-mail, idempotent); monthly quotas for reports, AI calls and mentions; webhook limit; retention purge per plan; daily job scheduled once; `/metrics` token | `make test-api` |
| CI "Backup and restore round-trip" | `scripts/backup.sh` → `scripts/restore.sh` into a new database: same tables, forced-RLS tables, policies, schema version and seed data; refuses a non-empty target; app role can use the copy | CI |
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
