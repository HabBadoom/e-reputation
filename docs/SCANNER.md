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
| Accounts | **Separate Scanner accounts**, one account type for everyone, not AnyPulse users. Linking a Scanner account to an AnyPulse organization is required before transmission (Phase 2/7) |
| Login | Required before the first upload; no anonymous collection |
| Destination | Every collection is attached to an AnyPulse brand at transmission |
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

## Links to AnyPulse organizations (phase 2)

A Scanner account sends collections only to AnyPulse organizations that approved it.

1. In Scanner (**Organisations AnyPulse**), the user asks for a link. Scanner creates a link
   request (random token, stored hashed, valid `REPINTEL_SCANNER_LINK_REQUEST_TTL_SECONDS`,
   15 min by default) and opens `{REPINTEL_APP_BASE_URL}/connect/scanner?request=…` in AnyPulse.
2. In AnyPulse, a member signs in (normal AnyPulse login) and sees which Scanner account asks
   (name and email) and for which organization (their current one). An **owner or admin**
   (permission `scanner:link`) approves or declines. Approval runs in the database function
   `scanner_link_approve()`, which re-checks the AnyPulse context and the permission itself.
   The token is single-use.
3. The link (`scanner_links`) is visible to its two parties only: the Scanner account (it sees
   the organization's name) and the organization (it sees the account's name and email, in
   **Scanner** in the AnyPulse menu). Either side can revoke it; a revoked link stays revoked.

What a link allows: list the organization's active **brand names** (to choose a destination)
and, from phase 7, send collections. It never gives read access to mentions, analyses or reports.

### Signed integration

Scanner reaches AnyPulse only through `app/integrations/anypulse/client.py`, which signs each
call (HMAC-SHA256 over method, path, timestamp, Scanner account, link and body hash; header
`X-Scanner-Signature: v1=…`, `X-Scanner-Account`, `X-Scanner-Link`, `X-Scanner-Timestamp`,
±5 min). AnyPulse verifies the signature, then checks in the database that the link is
active, belongs to that account and that the organization is active, and only then switches to
the organization's context. Both run in the same process today; the contract stays the same if
Scanner is deployed separately. Key: `REPINTEL_SCANNER_INTEGRATION_SECRET` (required in
staging/production when Scanner is enabled; derived from the JWT secret in development/tests).

### Collection destination

Each open collection gets a destination: an active link of the account and one of its
organization's active brands (decision D3). Scanner checks the brand against the live list from
AnyPulse, and a database trigger checks it again. A collection whose link was revoked shows it
and asks for another destination.

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
| GET / PATCH | `/scanner/sessions/{id}` | PATCH `{title?, link_id?, brand_id?}`; `link_id: null` clears the destination; 409 `invalid_destination`, `link_revoked`, `session_not_open` |
| POST | `/scanner/sessions/{id}/cancel` | Open collections only (409 `session_not_open`) |
| POST | `/scanner/links/start` | Link request → `{authorize_url, expires_at}`; 10 per hour per account |
| GET | `/scanner/links` | Own links (organization name, status, who revoked) |
| POST | `/scanner/links/{id}/revoke` | 409 `link_revoked` if already revoked |
| GET | `/scanner/links/{id}/brands` | Active brands of the linked organization, via the signed client; 503 `anypulse_unavailable` |

AnyPulse side (`/api/v1/integrations/scanner/*`, normal AnyPulse auth unless noted):

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/link-requests/inspect` | any member | `{token}` → requesting account, status (`pending/approved/declined/expired`), organization, `can_decide` |
| POST | `/link-requests/approve` | `scanner:link` | Links the account to the **current** organization; 404 `invalid_link_request` |
| POST | `/link-requests/decline` | `scanner:link` | 204 |
| GET | `/links` | `tenant:read` | The organization's Scanner links |
| POST | `/links/{id}/revoke` | `scanner:link` | |
| GET | `/links/{id}/brands` | **signed** Scanner call | `{items: [{id, name}]}`; 401 `invalid_signature`, 409 `link_revoked` |

Another account's session answers 404, like a missing one. Token mode for API clients:
`X-Auth-Mode: token` on register/login/refresh, then `Authorization: Bearer`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `REPINTEL_SCANNER_ENABLED` | `false` | Mounts `/api/v1/scanner/*`. On in development (`docker-compose.yml`) and tests; keep off in production until the Scanner web app is deployed |
| `REPINTEL_SCANNER_BASE_URL` | `http://localhost:3001` | Public URL of the Scanner web app |
| `REPINTEL_SCANNER_COOKIE_DOMAIN` | unset | Cookie domain for `sc_*` cookies (host-only when unset) |
| `REPINTEL_SCANNER_MAX_ITEMS_PER_SESSION` | `200` | Upper bound per collection (used from Phase 3) |
| `REPINTEL_SCANNER_LINK_REQUEST_TTL_SECONDS` | `900` | Validity of a link request |
| `REPINTEL_SCANNER_INTEGRATION_SECRET` | unset | HMAC key for Scanner → AnyPulse calls (≥ 32 chars). Required in staging/production when Scanner is enabled |
| `REPINTEL_ANYPULSE_API_URL` | `http://localhost:8000` | Where Scanner's integration client reaches the AnyPulse API |

Local development: `docker compose up --build`, then http://localhost:3001 (Scanner) and
http://localhost:3000 (AnyPulse).

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Audit, integration plan, decisions | Done |
| 1 | Foundation: accounts, auth, sessions, RLS, web app shell, CI, docs | Done |
| 2 | Link a Scanner account to an AnyPulse organization (consent by owner/admin); brand picker; signed integration client | Done |
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
- A link request URL is a bearer secret for 15 minutes: whoever holds it and is an owner/admin
  of an organization can approve it for that organization. The consent screen shows which
  Scanner account asks and warns to approve only expected requests.
- No email verification or password reset for Scanner accounts yet (AnyPulse's flows exist and
  will be adapted).
- Production compose and Caddy do not serve the Scanner web app yet (Phase 9);
  `REPINTEL_SCANNER_ENABLED` stays `false` there.
