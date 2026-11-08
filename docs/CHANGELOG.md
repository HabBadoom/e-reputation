# Changelog

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
