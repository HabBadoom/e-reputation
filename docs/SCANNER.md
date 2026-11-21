# Aynpulse Scanner

Status: **Phases 1 (foundation) and 2 (links to AnyPulse) implemented.** Screenshot upload,
extraction, URL collection and transmission are not built yet; their buttons are shown disabled
with a note.

> Scanner collects. AnyPulse understands, analyses and reports.

Scanner is the collection module of AnyPulse (this repository, named `RepIntel` in code). Users
add screenshots, URLs or spreadsheet imports to a collection, review what was extracted and
send the collection to AnyPulse. All analysis (language, sentiment, emotions, intents, topics,
risks, crises, summaries, recommendations, reports) happens in AnyPulse; users sign in to
AnyPulse to see it.

## Decisions (2026-11-21)

| Decision | Choice |
|---|---|
| Codebase | Module inside the AnyPulse repository, with its own web app (`scanner/`) and tables (`scanner_*`), talking to AnyPulse through an ingest API (Phase 7) |
| Accounts | **Separate Scanner accounts**, one account type for everyone, not AnyPulse users |
| Users | Scanner users are **collection agents**: they add screenshots and URLs, never see analyses and never choose a brand (RepIntel's AI attributes brands later) |
| AnyPulse link | **Automatic**: each Scanner account gets its own AnyPulse organization at sign-up, linked for good unless a **platform administrator** revokes it. No consent step, no invitation |
| Login | Required before the first upload; no anonymous collection |
| Priority | Scanner before the Phase 2 & 3 plan (sprints 9–15) |

The Phase 0 audit and integration plan is kept outside the repository (project documents).

## What Scanner must never do

No sentiment, emotion, intent or topic classification; no reputation scores; no crisis
detection; no summaries, recommendations or reports; no analytical history. Recognising what a
comment *says* (OCR, layout) is collection; deciding whether it is *negative* is analysis and
belongs to AnyPulse.

## Architecture

```
scanner/                       Next.js app (port 3001): login, register, collect, history
backend/app/scanner/           auth (accounts, tokens), deps (request context), sessions, audit
backend/app/modules/scanner/   HTTP router /api/v1/scanner/*
backend/migrations/versions/0010_scanner_foundation.py
```

Same FastAPI process, database cluster and CI as AnyPulse. The Scanner web app only proxies
`/api/v1/scanner/*`; it cannot reach the rest of the AnyPulse API from its origin.

## Accounts, tokens and isolation

- **Accounts** (`scanner_accounts`): email, Argon2id password hash, full name, locale. One
  account type for everyone (no individual/professional split). Independent of AnyPulse `users`: the
  same email may exist in both, with different passwords and no link.
- **Tokens**: 15-minute access JWT with `typ=scanner_access` and audience `aynpulse-scanner`;
  opaque refresh token (14 days) stored hashed, rotated on every use, reuse revokes the family.
  AnyPulse refuses Scanner tokens and Scanner refuses AnyPulse tokens.
- **Cookies** (browsers): `sc_access`, `sc_refresh` (path `/api/v1/scanner/auth`), `sc_csrf`
  (double submit). Never the AnyPulse `ri_*` cookies. Domain: `REPINTEL_SCANNER_COOKIE_DOMAIN`
  (host-only by default).
- **Row-level security**: every Scanner table has RLS enabled and forced, keyed on
  `app.scanner_account_id` (transaction-local). The DB session sets either the AnyPulse context
  (`app.tenant_id`, `app.user_id`) or the Scanner context, never both: `set_scanner_context()`
  clears the AnyPulse one. Unset context → no rows.
- **Pre-context lookups**: `scanner_auth_find_account_by_email()` and
  `scanner_auth_find_refresh_token()` (SECURITY DEFINER, narrow columns).
- **Audit**: `scanner_audit_logs`, append-only for the app role (account created, sign-in,
  collection created or discarded).

## Link to AnyPulse (phase 2)

Every Scanner account is linked automatically to **its own AnyPulse organization**:

1. Right after sign-up, Scanner calls the signed endpoint `POST /integrations/scanner/provision`.
   AnyPulse creates an organization named after the account (default plan and trial) and the
   link (`scanner_links`). The organization has no AnyPulse member: Scanner users are collection
   agents and are not invited to AnyPulse.
2. If AnyPulse is unreachable at sign-up, the account is still created; provisioning runs again
   the next time Scanner checks the account's access (`GET /scanner/anypulse`). It is idempotent.
3. The link stays active indefinitely. Only a **platform administrator** (admin console, "Scanner"
   tab, two-step verification) revokes it, with a reason shown to the agent, and may restore it.
   Revocation is audited in the organization's log. A revoked agent keeps using Scanner but
   cannot send collections (phase 7); the collection screen says so.

Database guards: a link can only be inserted with both the Scanner account and the new
organization in context, and only for a brand-new organization (no member, created minutes
ago), so no code path can attach a Scanner account to an existing customer organization. One
organization per account and one account per organization. Only `admin_scanner_set_access()`
(platform administrators) changes a link.

A link gives no read access to anything in AnyPulse. From phase 7 it lets Scanner send
collections to the organization; **brand attribution is done afterwards by RepIntel's AI**.

### Signed integration

Scanner reaches AnyPulse only through `app/integrations/anypulse/client.py`, which signs each
call (HMAC-SHA256 over method, path, timestamp, Scanner account, link and body hash; headers
`X-Scanner-Signature: v1=…`, `X-Scanner-Account`, `X-Scanner-Link` (empty before
provisioning), `X-Scanner-Timestamp`, ±5 min). AnyPulse verifies the signature and checks the
account (and, from phase 7, the link) in the database before acting. Both run in the same
process today; the contract stays the same if Scanner is deployed separately. Key:
`REPINTEL_SCANNER_INTEGRATION_SECRET` (required in staging/production when Scanner is enabled;
derived from the JWT secret in development/tests).

## Collection sessions

A session groups the user's successive additions until they are sent. Each account has **at
most one open session** (`draft`, `collecting`, `ready`; partial unique index).
`GET /scanner/sessions/current` returns it or creates it, so the add-item screen always comes
back to the same collection. Statuses: `draft`, `collecting`, `ready`, `validating`, `queued`,
`transmitting`, `transmitted`, `acknowledged`, `failed`, `retrying`, `cancelled`.

Session counters are **collection metrics only**: items, screenshots, URLs, imports, items to
review, ready, failed.

Items (`scanner_collection_items`) hold the source type (`screenshot`, `url`, `import`),
detected platform, content type, position, processing status and the URL for URL items. They
are created from Phase 3 on.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/scanner/auth/register` | `{email, password (≥12), full_name, locale}`; 409 `email_taken`; rate-limited |
| POST | `/scanner/auth/login` | `{email, password}`; rate-limited per IP and per email |
| POST | `/scanner/auth/refresh` | Body `{refresh_token}` or the `sc_refresh` cookie; rotates |
| POST | `/scanner/auth/logout` | Revokes the refresh family; access tokens stop working immediately |
| GET | `/scanner/auth/me` | Account profile |
| GET | `/scanner/sessions/current` | The open collection, created on first use |
| GET | `/scanner/sessions` | History, newest first, `limit` / `offset` |
| GET / PATCH | `/scanner/sessions/{id}` | PATCH `{title}` |
| POST | `/scanner/sessions/{id}/cancel` | Open collections only (409 `session_not_open`) |
| GET | `/scanner/anypulse` | `{state: active\|revoked\|pending, revoked_at, revoke_reason}`: whether collections can be sent; provisions first if needed. No AnyPulse data |

AnyPulse side, signed Scanner calls only (`/api/v1/integrations/scanner/*`, no AnyPulse session):

| Method | Path | Notes |
|---|---|---|
| POST | `/provision` | Create (once) the account's organization and link; `{link_id, organization_name, status, created}`. Collection submission comes in phase 7 |

Platform administration (`/api/v1/admin/scanner/*`, platform admin + MFA):

| Method | Path | Notes |
|---|---|---|
| GET | `/admin/scanner/accounts` | Accounts, organization, access status, reason, collections |
| POST | `/admin/scanner/accounts/{account_id}/revoke` | `{reason}` (shown to the user); audited in the organization |
| POST | `/admin/scanner/accounts/{account_id}/restore` | Restores access |

Another account's session answers 404, like a missing one. Token mode for API clients:
`X-Auth-Mode: token` on register/login/refresh, then `Authorization: Bearer`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `REPINTEL_SCANNER_ENABLED` | `false` | Mounts `/api/v1/scanner/*`. On in development (`docker-compose.yml`) and tests; keep off in production until the Scanner web app is deployed |
| `REPINTEL_SCANNER_BASE_URL` | `http://localhost:3001` | Public URL of the Scanner web app |
| `REPINTEL_SCANNER_COOKIE_DOMAIN` | unset | Cookie domain for `sc_*` cookies (host-only when unset) |
| `REPINTEL_SCANNER_MAX_ITEMS_PER_SESSION` | `200` | Upper bound per collection (used from Phase 3) |
| `REPINTEL_SCANNER_INTEGRATION_SECRET` | unset | HMAC key for Scanner → AnyPulse calls (≥ 32 chars). Required in staging/production when Scanner is enabled |
| `REPINTEL_ANYPULSE_API_URL` | `http://localhost:8000` | Where Scanner's integration client reaches the AnyPulse API |

Local development: `docker compose up --build`, then http://localhost:3001 (Scanner) and
http://localhost:3000 (AnyPulse).

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Audit, integration plan, decisions | Done |
| 1 | Foundation: accounts, auth, sessions, RLS, web app shell, CI, docs | Done |
| 2 | Automatic link to the account's own AnyPulse organization; platform-admin revocation; signed integration client | Done |
| 3 | Screenshot-first upload: drag and drop, camera/gallery, preview, reorder, duplicates, private object storage, return to the add-item screen | Planned |
| 4 | Extraction: validation, EXIF strip, preprocessing, OCR + vision, platform detection, threads, dedup, confidence, review screen | Planned |
| 5 | URL connectors (YouTube official API first; Apify behind the legal-review gate) and Excel import | Planned |
| 6 | Normalisation, history, retention | Planned |
| 7 | Transmission to AnyPulse: ingest API, idempotency, retries, acknowledgement, deep link | Planned |
| 8–9 | Hardening, production deployment (Caddy host for Scanner, prod compose service) | Planned |

## Known limitations (Phase 1)

- No upload, extraction, URL collection or transmission yet; those actions are visibly disabled.
- Signed integration calls are protected against tampering and stale replays (±5 min) but carry
  no nonce: a captured call could be replayed within that window. Only read-only calls exist
  today; collection submission (phase 7) adds an idempotency key.
- Anyone can create a Scanner account, and so an AnyPulse organization on the default plan
  (as with AnyPulse sign-up). Sign-up is rate-limited; e-mail verification for Scanner accounts
  is not built yet.
- **Open question for phase 7:** with one organization per agent, collected mentions land in
  the agent's organization, while the brands they are about (Ooredoo, Mobilis…) belong to
  customer organizations. How RepIntel's AI routes them to the right customer must be decided
  before transmission is built. AnyPulse mentions also require a brand today.
- No email verification or password reset for Scanner accounts yet (AnyPulse's flows exist and
  will be adapted).
- Production compose and Caddy do not serve the Scanner web app yet (Phase 9);
  `REPINTEL_SCANNER_ENABLED` stays `false` there.
