# Aynpulse Scanner

Status: **Phases 1 (foundation), 2 (AnyPulse access) and 3 (screenshot upload) implemented.**
Extraction (OCR), URL collection and transmission to AnyPulse are not built yet; their actions
are shown disabled with a note.

> Scanner collects. AnyPulse understands, analyses and reports.

Scanner is the collection module of AnyPulse (this repository, named `RepIntel` in code). Its
users are **collection agents**: they add screenshots (and later URLs or spreadsheet imports)
to a collection and send it to AnyPulse. They never see analyses and never choose a brand.

## Decisions (2026-11-21)

| Decision | Choice |
|---|---|
| Codebase | Module inside the AnyPulse repository, with its own web app (`scanner/`) and tables (`scanner_*`) |
| Users | **Collection agents**: they add screenshots and URLs, never see analyses, never choose a brand |
| Accounts | **Separate Scanner accounts**, one account type for everyone, not AnyPulse users |
| AnyPulse access | **Automatic** from sign-up, indefinitely. Only a **platform administrator** revokes it (with a reason shown to the agent) or restores it. No consent step, no invitation, no AnyPulse organization per agent |
| Destination (option A) | The collections of all agents go to a **central RepIntel space**; RepIntel's AI attributes each one to a brand, hence to a customer, afterwards (transmission: phase 7) |
| Login | Required before the first upload; no anonymous collection |
| Storage | Private S3-compatible object storage: **RustFS** in development and CI (MinIO's community edition and images were withdrawn in 2025–2026), an S3-compatible bucket at the EU host in production |
| Priority | Scanner before the Phase 2 & 3 plan (sprints 9–15) |

The Phase 0 audit and integration plan is kept outside the repository (project documents).

## What Scanner must never do

No sentiment, emotion, intent or topic classification; no reputation scores; no crisis
detection; no summaries, recommendations or reports; no analytical history; no brand
attribution. Recognising what a comment *says* (OCR, layout) is collection; deciding whether it
is *negative*, or which brand it is about, belongs to AnyPulse.

## Architecture

```
scanner/                       Next.js app (port 3001): login, register, collect, history
backend/app/scanner/           auth, deps (request context), sessions, uploads, images, storage, audit
backend/app/modules/scanner/   HTTP router /api/v1/scanner/*
backend/migrations/versions/0010_scanner_foundation.py, 0011_scanner_access.py, 0012_scanner_uploads.py
```

Same FastAPI process, database cluster and CI as AnyPulse. The Scanner web app only proxies
`/api/v1/scanner/*`; it cannot reach the rest of the AnyPulse API from its origin.

## Accounts, tokens and isolation

- **Accounts** (`scanner_accounts`): email, Argon2id password hash, full name, locale. One
  account type for everyone. Independent of AnyPulse `users`: the same email may exist in both,
  with different passwords and no link.
- **Tokens**: 15-minute access JWT with `typ=scanner_access` and audience `aynpulse-scanner`;
  opaque refresh token (14 days) stored hashed, rotated on every use, reuse revokes the family.
  AnyPulse refuses Scanner tokens and Scanner refuses AnyPulse tokens.
- **Cookies** (browsers): `sc_access`, `sc_refresh` (path `/api/v1/scanner/auth`), `sc_csrf`
  (double submit). Never the AnyPulse `ri_*` cookies. Domain: `REPINTEL_SCANNER_COOKIE_DOMAIN`
  (host-only by default).
- **Row-level security**: every Scanner table has RLS enabled and forced, keyed on
  `app.scanner_account_id` (transaction-local). The DB session sets either the AnyPulse context
  (`app.tenant_id`, `app.user_id`) or the Scanner context, never both. Unset context → no rows.
- **Audit**: `scanner_audit_logs`, append-only for the app role (account created, sign-in,
  collection created or discarded, screenshots added, item removed, access revoked/restored).

## AnyPulse access (phase 2)

`scanner_accounts.anypulse_access` is `active` from creation (a trigger enforces it whatever the
INSERT says) and stays so indefinitely. The API role cannot UPDATE the access columns (column
grants); only `admin_scanner_set_access()` can, for a **platform administrator**, from the admin
console's **Scanner** tab (two-step verification). Revocation records when, by whom and why; the
reason is shown to the agent on the collection screen. A revoked agent keeps using Scanner but
will not be able to send collections (phase 7). Each change is written to the account's Scanner
audit trail.

## Collection sessions

A session groups the agent's successive additions until they are sent. Each account has **at
most one open session** (`draft`, `collecting`, `ready`; partial unique index).
`GET /scanner/sessions/current` returns it or creates it, so the add-item screen always comes
back to the same collection. Statuses: `draft`, `collecting`, `ready`, `validating`, `queued`,
`transmitting`, `transmitted`, `acknowledged`, `failed`, `retrying`, `cancelled`.

Session counters are **collection metrics only**: items, screenshots, URLs, imports, items to
review, ready, failed.

## Screenshots (phase 3)

The collection screen opens on the upload zone: drag and drop, file picker (several files) and,
on phones, the camera. Each file uploads on its own with a progress bar; a failed upload can be
retried; the zone stays ready for the next screenshot and the collection below shows every
screenshot in order, with move up/down and remove.

On the server each file is:

1. **Limited** before decoding: size (`REPINTEL_SCANNER_UPLOAD_MAX_BYTES`, 10 MB), pixels
   (40 M) and side (12,000 px), files per request (20), uploads per hour (600), items per
   collection (200). Pillow's own decompression-bomb check is also applied.
2. **Identified from its bytes** (never from the file name or the client's MIME type): PNG,
   JPEG or WebP only. Anything else is refused (`unsupported_type`), including SVG and PDF.
3. **Decoded and re-encoded** in the same format after applying the EXIF orientation, **without
   any metadata** (EXIF incl. GPS location, XMP, ICC, PNG text). The stored file is ours, not the
   uploaded bytes. Animated images keep their first frame.
4. **Fingerprinted**: SHA-256 of the stored bytes (an exact duplicate in the same collection is
   refused and points to the existing item) and a 64-bit difference hash (a visually
   near-identical screenshot is accepted but **flagged** `possible_duplicate_of`, never merged —
   grouping screenshots of one discussion is extraction's job, phase 4).
5. **Stored privately** under an application-made key `scanner/<account>/<session>/<file>.<ext>`
   and attached to a new `screenshot` item (`pending` until extraction). One bad file never
   blocks the others: the answer lists each file's outcome (`added`, `duplicate`, `rejected` with
   a code).

Images are read back only through `GET /scanner/files/{id}/content`, to their owner (RLS),
with `Cache-Control: private`, `nosniff` and a sandboxing CSP. The bucket never allows anonymous
access (checked in CI). When storage is down, the upload fails as a whole (503
`storage_unavailable`) and nothing half-done is kept. Removing an item deletes its object after
the database commit.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/scanner/auth/register` | `{email, password (≥12), full_name, locale}`; 409 `email_taken`; rate-limited |
| POST | `/scanner/auth/login` | `{email, password}`; rate-limited per IP and per email |
| POST | `/scanner/auth/refresh` | Body `{refresh_token}` or the `sc_refresh` cookie; rotates |
| POST | `/scanner/auth/logout` | Revokes the refresh family; access tokens stop working immediately |
| GET | `/scanner/auth/me` | Account profile |
| GET | `/scanner/anypulse` | `{state: active\|revoked, revoked_at, revoke_reason}`: may this agent send collections? No AnyPulse data |
| GET | `/scanner/sessions/current` | The open collection, created on first use |
| GET | `/scanner/sessions` | History, newest first, `limit` / `offset` |
| GET / PATCH | `/scanner/sessions/{id}` | PATCH `{title}` |
| POST | `/scanner/sessions/{id}/cancel` | Open collections only (409 `session_not_open`) |
| POST | `/scanner/sessions/{id}/screenshots` | Multipart `files` (PNG/JPEG/WebP) → `{results: [{filename, status, code, item_id}], items, session}`; 413 `too_many_files`, 409 `session_not_open`, 503 `storage_unavailable` |
| GET | `/scanner/sessions/{id}/items` | Items in order, with file metadata and read-back URL |
| PUT | `/scanner/sessions/{id}/items/order` | `{item_ids}`: every item exactly once (409 `invalid_order`) |
| DELETE | `/scanner/items/{id}` | Removes the item and its file (open collections only) |
| GET | `/scanner/files/{id}/content` | The sanitised image, owner only |

Another account's session, item or file answers 404, like a missing one. Token mode for API
clients: `X-Auth-Mode: token` on register/login/refresh, then `Authorization: Bearer`.

Platform administration (`/api/v1/admin/scanner/*`, platform admin + MFA):

| Method | Path | Notes |
|---|---|---|
| GET | `/admin/scanner/accounts` | Accounts, access status, reason, collections |
| POST | `/admin/scanner/accounts/{account_id}/revoke` | `{reason}` (shown to the agent) |
| POST | `/admin/scanner/accounts/{account_id}/restore` | Restores access |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `REPINTEL_SCANNER_ENABLED` | `false` | Mounts `/api/v1/scanner/*`. On in development and tests; keep off in production until the Scanner web app is deployed |
| `REPINTEL_SCANNER_BASE_URL` | `http://localhost:3001` | Public URL of the Scanner web app |
| `REPINTEL_SCANNER_COOKIE_DOMAIN` | unset | Cookie domain for `sc_*` cookies (host-only when unset) |
| `REPINTEL_SCANNER_MAX_ITEMS_PER_SESSION` | `200` | Items per collection |
| `REPINTEL_SCANNER_UPLOAD_MAX_BYTES` | `10485760` | Per file |
| `REPINTEL_SCANNER_UPLOAD_MAX_PIXELS` / `_MAX_SIDE` | `40000000` / `12000` | Image dimension limits |
| `REPINTEL_SCANNER_UPLOAD_MAX_FILES_PER_REQUEST` | `20` | Files per upload request |
| `REPINTEL_SCANNER_UPLOADS_PER_HOUR` | `600` | Files per account per hour |
| `REPINTEL_STORAGE_BACKEND` | `filesystem` | `s3` required in staging/production when Scanner is enabled; `filesystem` for local tests only |
| `REPINTEL_STORAGE_ENDPOINT_URL` | unset | S3-compatible endpoint (e.g. `http://rustfs:9000`); unset = AWS |
| `REPINTEL_STORAGE_REGION` / `_BUCKET` | `us-east-1` / `repintel-scanner` | The bucket must be private |
| `REPINTEL_STORAGE_ACCESS_KEY` / `_SECRET_KEY` | unset | Credentials scoped to that bucket |
| `REPINTEL_STORAGE_FILESYSTEM_ROOT` | `/tmp/repintel-storage` | Filesystem backend only |

Local development: `docker compose up --build`, then http://localhost:3001 (Scanner) and
http://localhost:3000 (AnyPulse). The `rustfs` service provides S3-compatible storage on
port 9000; the development bucket is created automatically.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Audit, integration plan, decisions | Done |
| 1 | Foundation: accounts, auth, sessions, RLS, web app shell, CI, docs | Done |
| 2 | AnyPulse access granted at sign-up; platform-admin revocation | Done |
| 3 | Screenshot-first upload: drag and drop, camera, progress, retry, reorder, remove, duplicates, sanitisation, private storage | **Done** |
| 4 | Extraction: preprocessing, OCR + vision, platform detection, threads, dedup, confidence, review screen | Next |
| 5 | URL connectors (YouTube official API first; Apify behind the legal-review gate) and Excel import | Planned |
| 6 | Normalisation, history, retention and cleanup of stored files | Planned |
| 7 | Transmission to the central RepIntel space: ingest API, idempotency, retries, acknowledgement; brand attribution by RepIntel's AI | Planned |
| 8–9 | Hardening, production deployment (Caddy host for Scanner, prod compose service, bucket) | Planned |

## Known limitations

- No extraction, URL collection or transmission yet; those actions are visibly disabled.
- Stored screenshots are not yet deleted after a retention period, and objects left behind by
  a failed database commit are not swept yet (phase 6).
- Re-encoding JPEG/WebP at quality 92 slightly changes pixels; text stays legible for OCR.
- Anyone can create a Scanner account (as with AnyPulse sign-up). Sign-up is rate-limited;
  e-mail verification for Scanner accounts is not built yet.
- **For phase 7:** AnyPulse mentions require a brand today; the central space must hold
  collected content until RepIntel's AI attributes it to a brand and customer.
