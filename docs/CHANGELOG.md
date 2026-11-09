# Changelog

## 0.9.0 — Milestone 8: Commercialisation and production readiness (2026-11-09)

### Added
- Migration 0009: plan catalogue (`description`, `is_public`, `sort_order`; tiers starter, professional, business, agency, enterprise created **inactive and without prices**), `plan_prices`, new entitlements (monthly mentions, AI calls, reports; webhooks; concurrent browser missions; retention days), `billing_accounts` with Algerian identifiers (RC, NIF, NIS, article d'imposition), `invoices` / `invoice_lines` with gapless yearly numbering and immutable issued invoices, `usage_events`, `plan_change_requests`, subscription status `expired`; admin-only writes enforced by RLS and triggers; `admin_tenants()`, `admin_platform_stats()`, `platform_metrics()`, `expire_trials()`, daily maintenance scheduling; browser concurrency per organisation from the plan; permission `billing:read`.
- Usage metering in the same transaction as the work (mentions, search queries, AI calls and tokens, reports, browser missions, agent tasks, e-mails) and monthly quotas: collection stops storing at the mention quota, AI analysis leaves mentions pending at the AI quota, reports refused at the report quota; webhook limit. Expired or cancelled subscriptions stop new work (402) while data stays readable.
- Manual invoicing: drafts (free lines or from the subscription's price), issue with seller/buyer snapshots, VAT and stamp duty, amount in words in French, paid / void, PDF in French, Arabic (RTL) or English via headless Chromium.
- Customer **Billing** page: plan and usage bars, plan catalogue with prices or "price on request", plan change request (e-mailed to the platform team), billing details, invoices with PDF.
- Platform **Administration** console (platform admins with two-step verification): platform figures, organisations, subscription editor, plan requests, invoices, plan limits and prices, connector legal reviews; every change audited in the organisation's own log.
- Daily maintenance job: trial expiry with e-mail to owners, retention purge per plan (ceiling `REPINTEL_RETENTION_MAX_DAYS`), usage history pruning.
- `/metrics` (Prometheus, token-protected, aggregates only) and optional Sentry.
- Production deployment: `docker-compose.prod.yml` (Caddy TLS, internal data network, separate egress networks), `scripts/egress-firewall.sh`, `scripts/backup.sh` / `restore.sh` (verified, optional age encryption), `infra/postgres/prod-init.sh`, `.env.production.example`, release workflow building images to GHCR with SBOM and provenance; CI backup → restore round-trip.

### Changed
- Settings links to Billing; the "upcoming" navigation section is gone (all milestones delivered).
- Collection, NLP, agents, reports and browser missions check the subscription and quotas before starting.

### Needs human validation
- Invoice template and VAT/stamp duty (accountant); EU hosting under law 18-07 and connector approvals (lawyer); tier limits and prices (you).

## 0.8.0 — Milestone 7: Multi-agent system (2026-11-09)

### Added
- Migration 0008: `ai_tasks`, `agent_runs`, `ai_actions`, `approval_requests` (RLS); triggers that refuse executing approval-required actions without an approval decided by an allowed member, and decided approvals from changing; `brands.response_tone` / `response_guidelines`; `ai:read`, `ai:run`, `ai:approve`; plan quota `max_ai_tasks_per_day`.
- Orchestrator and 13 agents (monitoring, collection, discovery, language, entity, sentiment, reputation, trend, crisis, competitor, response, reporting, compliance) with typed outputs, tool allowlists, permission ceiling, timeouts and versions; findings labelled fact / inference / limitation.
- Compliance policy for actions: low-risk actions run within the member's rights, sensitive ones wait for human approval and are re-checked at execution.
- Response agent drafting replies in Darija (Arabic or Latin script), Arabic, French or English in the brand's tone; output validation; never published.
- Free-text requests routed to an allowlisted playbook only.
- API `/ai/*`, worker job `ai.task`; UI "AI agents" (tasks, steps, findings, actions, approvals with editable reply drafts, agent catalog and rules), "Draft a reply" on mentions, brand reply tone and guidelines.
- Prompt-injection evaluation suite (French, Darija/Arabizi, Arabic, mixed; compromised-model simulations).

### Changed
- "Run now" for monitoring projects shares one service function with the Monitoring agent.

## 0.7.0 — Milestone 6: Browser fleet (2026-11-08)

### Added
- Migration 0007: `browser_workers`, `browser_domain_policies`, `browser_domain_state`, `browser_missions` (state machine trigger + audit trigger), `browser_sessions`, `browser_session_events`, `captcha_events`; claim, heartbeat, recovery and back-off functions; `sources.render_mode` and challenge counters; mention states `pending_render`, `blocked_by_challenge` and `verified_with`; `browser:read` / `browser:run`; plan quota `max_browser_missions_per_day`; `browser` connector compliance record.
- Browser worker (`python -m app.browser.worker`, compose `browser-worker`) with Playwright Chromium, isolated contexts, address checks on every request and redirect hop, health and cleanup.
- Automatic challenge detection on static fetches and rendered pages, autonomous response without bypass.
- Collection hands JavaScript pages to the fleet; sources can be set to automatic / HTML only / always browser; "check with browser".
- APIs `/browser/missions`, `/browser/challenges`, `/browser/fleet`, `/sources/{id}/browser-check`; UI "Browser fleet" page, source reading mode and challenge info, plan usage.

### Decided
- CAPTCHAs and anti-bot checks are never solved or bypassed; no human intervention flow (founder's choice for full autonomy). See `CAPTCHA_INTERVENTIONS.md`.

## 0.6.0 — Milestone 5: Reputation intelligence (2026-11-08)

### Added
- Migration 0006: `webhooks`, `alert_rules`, `alerts`, `crisis_events`, `trend_events`, `reports` (RLS), `schedule_alert_evaluations()`, `request_alert_evaluation()`, `alerts:read/manage`, `reports:read/create` permissions.
- Analytics: daily series by sentiment, reputation index with minimum sample and previous period, share of voice, products, emerging topics — on dated, de-duplicated, on-topic mentions (`docs/REPUTATION_METHODOLOGY.md`).
- Potential-crisis lifecycle per brand (open, escalate, resolve after 48 h quiet).
- Alert rules (6 kinds, thresholds, minimum severity, brand scope), recommended defaults, de-duplication and flood cap; channels in-app, e-mail digest (fr/ar/en) and HMAC-signed webhooks with retries; evaluation every 15 min and after each analysis batch.
- Reports in PDF (sandboxed headless Chromium), Excel, CSV and HTML, in French, Arabic (RTL) and English, with rule-based recommendations and explicit data limitations.
- UI: Analytics page, Alerts (feed, crises, rules, webhooks), Reports, notification bell, dashboard reputation/crisis/alert tiles.

### Changed
- Worker image installs Chromium and Latin/Arabic fonts; CI installs Chromium for PDF tests.

## 0.5.0 — Milestone 4: Algerian language intelligence (2026-11-06)

### Added
- Migration 0005: `mention_analysis`, `language_annotations`, `language_evaluation_runs`, `dialect_expressions` (30 seed entries), protected `users.is_platform_admin`, `tenants.nlp_corpus_opt_in`, `openai` compliance record, `nlp:correct` permission.
- Deterministic layer: script, heuristic language, Darija markers, code-switching, Darija/Arabizi normaliser with traceability.
- OpenAI provider (structured outputs) and ensemble: 3 annotators (models × prompt variants), majority vote, arbitration, UNCERTAIN; injection-safe prompts; outputs re-validated.
- Analysis jobs after each monitoring run; backlog analysis; corrections stored beside AI labels; sentiment/language/homonym filters; CSV columns.
- Corpus with opt-in and purge; gold-set sampling and verification screen; `ALGERIAN_NLP_BENCHMARK` runs; validation status per task drives "not validated" labels in the UI.
- UI: analysis badges and correction form on mentions, sentiment card on the dashboard, corpus opt-in in settings, "Language (NLP)" page (status, gold review, benchmark, dictionary).

## 0.4.0 — Milestone 3: Source intelligence (2026-11-06)

### Added
- Migration 0004: `connector_policies`, `sources`, `mentions`; `mentions:read`, `sources:read`, `sources:write` permissions.
- Connectors: Serper (Google news + web, `gl=dz`, `hl` per project language), RSS/Atom, web page reader; unified interface and compliance gate.
- Collection pipeline in monitoring runs: exclusions, verification on the real page (robots.txt honoured), excerpt around the match, publication dates with their origin, URL canonicalisation, repost detection, source auto-registration with coverage states; already-known URLs are not re-fetched.
- SSRF-safe fetcher, Arabic-aware term matching, HTML/RSS parsing (stdlib only).
- APIs and UI: Mentions inbox (filters, provenance, CSV export), Sources (add, check, enable, feed discovery, connectors and their compliance status), dashboard statistics.

### Changed
- Monitoring runs now end `completed`, `skipped` (with reason) or `failed`; summary includes per-connector results.
- Dashboard: mentions/sources available; sentiment explicitly unavailable until Milestone 4.

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
