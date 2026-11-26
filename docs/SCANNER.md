# AynPulse Scanner

Status: **Phases 1 to 7 implemented**: foundation, AynPulse access, screenshots (built-in
browser, whole-thread capture, upload), extraction by a vision model with review and validation,
pasted links, retention, and sending to AynPulse (central space, attribution to brands,
administrators' review queue). Phase 9 (deployment) is ready; phase 8 (hardening) remains.

> Scanner collects. AynPulse understands, analyses and reports.

Scanner is the collection module of AynPulse (this repository, named `RepIntel` in code). Its
users are **collection agents**: they add screenshots (and later URLs or spreadsheet imports)
to a collection and send it to AynPulse. They never see analyses and never choose a brand.

## Decisions (2026-11-21)

| Decision | Choice |
|---|---|
| Codebase | Module inside the AynPulse repository, with its own web app (`scanner/`) and tables (`scanner_*`) |
| Users | **Collection agents**: they add screenshots and URLs, never see analyses, never choose a brand |
| Accounts | **Separate Scanner accounts**, one account type for everyone, not AynPulse users |
| AynPulse access | **Automatic** from sign-up, indefinitely. Only a **platform administrator** revokes it (with a reason shown to the agent) or restores it. No consent step, no invitation, no AynPulse organization per agent |
| Destination (option A) | The collections of all agents go to a **central RepIntel space**; RepIntel's AI attributes each one to a brand, hence to a customer, afterwards (transmission: phase 7) |
| Login | Required before the first upload; no anonymous collection |
| Main collection action | The **built-in browser**: the agent opens Facebook (or another network) inside Scanner, signs in with a **dedicated collection account** and captures what they see. Importing screenshots already taken is the fallback. Credentials are handled by each agent; the signed-in session is kept encrypted (cookies only, never the password) |
| Extraction (D5) | A **vision AI model** (OpenAI, model id from configuration) reads each screenshot: posts, comments, replies, authors, times, counters, text inside images. Images are sent to the provider. Chosen by the product owner on 2026-11-23 |
| Validation before sending (phase 7) | Not required: the agent may send screenshots that were not validated; they reach AynPulse marked as not validated (decided 2026-11-24) |
| Storage | Private S3-compatible object storage: **RustFS** in development and CI (MinIO's community edition and images were withdrawn in 2025–2026), an S3-compatible bucket at the EU host in production |
| Priority | Scanner before the Phase 2 & 3 plan (sprints 9–15) |

The Phase 0 audit and integration plan is kept outside the repository (project documents).

## What Scanner must never do

No sentiment, emotion, intent or topic classification; no reputation scores; no crisis
detection; no summaries, recommendations or reports; no analytical history; no brand
attribution. The built-in browser does **no CAPTCHA solving, no anti-detection or fingerprint
spoofing and no automated browsing**: every action comes from the agent, and the agent completes
any verification a site asks for.

Spec override (decided by the product owner, 2026-11-21): the master prompt's rule "never
bypass authentication or platform restrictions" is lifted **only** in so far as agents sign in
to dedicated social-media accounts created for collection inside Scanner's browser, and Scanner
keeps their signed-in session. Nothing else from that rule is relaxed. Recognising what a comment *says* (OCR, layout) is collection; deciding whether it
is *negative*, or which brand it is about, belongs to AynPulse.

## Architecture

```
scanner/                       Next.js app (port 3001): login, register, collect, history
backend/app/scanner/           auth, deps (request context), sessions, uploads, images, storage, audit
backend/app/scanner/browser/   built-in browser: session (Chromium), profiles (encrypted state), server (WebSocket)
backend/app/modules/scanner/   HTTP router /api/v1/scanner/*
backend/migrations/versions/0010_scanner_foundation.py … 0013_scanner_browser.py
```

The built-in browser runs as its own service, `scanner-browser`
(`uvicorn app.scanner.browser.server:app --port 8100`), from the backend image.

Same FastAPI process, database cluster and CI as AynPulse. The Scanner web app only proxies
`/api/v1/scanner/*`; it cannot reach the rest of the AynPulse API from its origin.

## Accounts, tokens and isolation

- **Accounts** (`scanner_accounts`): email, Argon2id password hash, full name, locale. One
  account type for everyone. Independent of AynPulse `users`: the same email may exist in both,
  with different passwords and no link.
- **Tokens**: 15-minute access JWT with `typ=scanner_access` and audience `aynpulse-scanner`;
  opaque refresh token (14 days) stored hashed, rotated on every use, reuse revokes the family.
  AynPulse refuses Scanner tokens and Scanner refuses AynPulse tokens.
- **Cookies** (browsers): `sc_access`, `sc_refresh` (path `/api/v1/scanner/auth`), `sc_csrf`
  (double submit). Never the AynPulse `ri_*` cookies. Domain: `REPINTEL_SCANNER_COOKIE_DOMAIN`
  (host-only by default).
- **Row-level security**: every Scanner table has RLS enabled and forced, keyed on
  `app.scanner_account_id` (transaction-local). The DB session sets either the AynPulse context
  (`app.tenant_id`, `app.user_id`) or the Scanner context, never both. Unset context → no rows.
- **Audit**: `scanner_audit_logs`, append-only for the app role (account created, sign-in,
  collection created or discarded, screenshots added, item removed, access revoked/restored).

## AynPulse access (phase 2)

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

## Built-in browser (phase 3, main action)

The collection screen opens on **"Ouvrir Facebook"**. A real Chromium starts on our servers
for this agent and is shown live in the page (JPEG frames over a WebSocket):

- **Phone**: standard phone emulation (mobile layout, touch). Tap = tap, drag = scroll, a
  hidden field opens the phone keyboard (Arabic and French input work), plus Erase/Enter
  buttons. **Computer**: 1280×800, mouse click, wheel scroll, physical keyboard.
- Address bar, back / forward / reload. Links that open a new tab open in the same view.
  Downloads, file pickers, permissions (camera, location, notifications) and dialogs are off.
- **"Capturer l'écran"** takes a sharp PNG of what the agent sees (device resolution on phones)
  and adds it to the current collection through exactly the same pipeline as an imported
  screenshot (limits, sanitisation, duplicates, private storage), recording the page address
  (`source_url`) and its network (`platform`, from the host name only). Capturing the same
  screen twice is reported as a duplicate.
- **Session kept**: when the browser closes (or after each capture), its cookies and local
  storage are saved **encrypted** (`REPINTEL_ENCRYPTION_KEYS`, Fernet) in
  `scanner_browser_profiles`, one row per agent, readable only by that agent (RLS). Passwords
  are never stored by Scanner. "Oublier mes sessions connectées" deletes the row.
- One live browser per agent (a second tab takes over), a service-wide limit
  (`REPINTEL_SCANNER_BROWSER_MAX_SESSIONS`, `browser_busy` beyond it), closed after
  `REPINTEL_SCANNER_BROWSER_IDLE_SECONDS` without action.

Access: the WebSocket accepts only the Scanner app's Origin and a valid `sc_access` cookie;
the agent's session and account are re-checked every 30 s (logout or disabling closes the
browser), and the collection must be the agent's own open one. Keystrokes and page contents
are never logged; captures are audited (`screenshots.added`, `via: built_in_browser`).

Network: every request of the page goes through a filter that allows only http(s) to public
addresses (same rule as the HTTP fetcher; `file:`, private, loopback and cloud-metadata
addresses are refused). In production the service also sits on the `browser` network behind
the egress firewall; Caddy reaches it over an internal `scanner-edge` network.

Risks the product owner accepted: platforms may restrict or ban dedicated accounts used this
way (their terms generally forbid automated or shared access); the agents' sessions and what
they type pass through our servers. Keep the dedicated accounts separate from anyone's personal
account, and report bans rather than trying to evade them.

## Screenshots import (phase 3, fallback)

Below the browser, "Ou importez des captures déjà prises" opens the upload zone: drag and drop, file picker (several files) and,
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

## Sending to AynPulse (phase 7)

The agent presses **"Envoyer ma collecte à AynPulse"** (confirmation shows how many screenshots,
and how many are not validated). Refused while a screenshot is still being read, with no
screenshot, or when the agent's AynPulse access is revoked. The collection closes (`queued`) and
a new one starts; the history shows `queued → transmitting → transmitted` (or `failed`, with
"Renvoyer").

The `scanner.transmit` job (worker), for each screenshot:

1. **Attribution** (decisions 2026-11-25). Text used: page or group name, dialog title, and
   every post, comment and reply read (authors included), duplicates excluded.
   - First the **names, aliases and include keywords of every organisation's active brands**
     (`scanner_brand_catalogue()`; brands with the same name are one group): every group named
     in the text is matched (`method = keywords`).
   - If none is named and `REPINTEL_SCANNER_ATTRIBUTION_MODEL` is set, the language model is
     given the text and the list of followed brand names and answers which ones the content is
     about; at or above `REPINTEL_SCANNER_ATTRIBUTION_MIN_CONFIDENCE` (0.7) it is delivered
     (`method = ai`), otherwise it goes to review with the model's suggestion.
   - Otherwise: **review queue** (`to_review`). A screenshot with no text read is `empty`.
2. **Delivery** to **every organisation following a matched brand** (a brand's exclude
   keywords still apply per organisation): one AynPulse **mention per post, comment or reply**,
   `collection_method = scanner`, connector `aynpulse_scanner`, verification
   **`scanner_validated` or `scanner_unvalidated`** (whether the agent validated the
   screenshot), author as publisher, estimated publication time, and full provenance in
   `raw.scanner` (collection, item, entry, kind, parent, page or group, counters, link,
   agent). AynPulse's language analysis is queued for the new mentions. Delivery is idempotent
   (`canonical_url = scanner:entry:<id>`, unique per organisation and brand).
3. The images' deletion date is set to 30 days after sending (phase 6).

**Review queue** (AynPulse admin console → Scanner → "Scanner — à attribuer", platform
administrators only): the image, the text read, why it is there and the model's suggestion; the
administrator ticks followed brands (with how many organisations follow each) and delivers, or
discards. Decisions are recorded (`decided_by`, audit `attribution.decided`).

Scanner agents never see brands, organisations or analyses: only that their collection was sent.

## Retention (phase 6, decision D9)

Decided by the product owner on 2026-11-25:

| What | How long | Then |
|---|---|---|
| Images of a **sent** collection | 30 days after sending (`retention_until`, set by phase 7) | Images deleted from storage; items, extracted text and history stay |
| A collection **never sent** (open or cancelled) | 30 days without activity (adding, capturing, reviewing, validating) | Collection, images and text deleted; the agent sees the date in a banner from 7 days before |

Run by the existing daily maintenance job (`platform.daily` → `app.scanner.retention.run`): the
SECURITY DEFINER function `scanner_retention_accounts(idle_days)` lists the accounts concerned
(its only cross-account read; executable by the API role only), then each account is purged in
its own transaction and Scanner context (RLS). Objects are deleted from storage before their
rows: a storage failure leaves the rows for the next run. Each deletion is audited
(`files.retention_deleted`, `collection.expired`). Settings:
`REPINTEL_SCANNER_RETENTION_SENT_DAYS`, `_IDLE_DAYS` (30) and `_WARNING_DAYS` (7).

## Capture the whole thread

Next to "Capturer l'écran", **"Capturer tout le fil"** (asked by the product owner on
2026-11-24), also while a pasted link is active (the captures are then attached to it). The agent
opens a post and its comments, clicks once in the comments (that click chooses what scrolls: a
comment dialog, the photo viewer's comment column, otherwise the page), then starts it. Scanner
captures the screen, scrolls that area down by three quarters of its height with the mouse
wheel, waits for it to load (`REPINTEL_SCANNER_THREAD_SETTLE_MS`, 1.2 s) and again, until the
area stops moving (end of the thread), two captures are identical, the limit
(`REPINTEL_SCANNER_THREAD_MAX_CAPTURES`, 20) is reached, or the agent presses "Arrêter". Each
capture is stored and read like a single one; comments repeated in overlapping captures are
linked as duplicates by extraction. The agent watches it happen; other actions wait until it
ends.

Limits kept on purpose: it acts only on the page the agent opened and asked for; it clicks
nothing (no automatic "Voir plus" or "Afficher plus de commentaires": the agent opens them
first), opens no other page and does not hide that it is automated. Fully automatic collection
across pages was declined: dedicated accounts would be detected and banned quickly, platform
terms forbid it, and it raises personal-data questions (Algerian law 18-07); content
connectors (phase 5b) are the route for that.

## Links (phase 5)

Under the import zone, **"Coller un lien"**: the agent pastes a link (a post or comment received
on WhatsApp, for example). Decided by the product owner on 2026-11-24: the link is **recorded
and opened**.

- Recorded as a `url` item of the collection. Tracking parameters (`fbclid`, `mibextid`,
  `utm_*`, `__cft__`, `si`…) and the fragment are removed; the same page pasted again
  (`m.`/`www.`/`web.` variants, parameter order, trailing slash) is not added twice
  (`url_key`, unique per collection) and simply opens again. Only `http`/`https` addresses,
  without user name or password.
- Opened in the built-in browser (already signed in to the dedicated account). While a link is
  active, a banner shows it and every capture taken is attached to it (`link_item_id`);
  "Détacher" stops that. The link card shows how many captures were taken from it and
  "Ouvrir pour capturer"; a linked capture shows "depuis le lien n°…".
- A link becomes `ready` with its first capture. Removing a link keeps its captures.
- Scanner never fetches or scrapes the page itself: what is collected is what the agent
  captures. Connectors that fetch content (YouTube API, Apify, Excel import) stay in the plan
  (D6).

## Extraction (phase 4)

Each screenshot (uploaded or captured in the built-in browser) is queued as a `scanner.extract`
job when extraction is configured (`REPINTEL_OPENAI_API_KEY` and
`REPINTEL_SCANNER_EXTRACTION_MODEL`). The `worker` service runs it in the agent's Scanner context.

1. **Image**: the sanitised file, as PNG. Screenshots taller than 2.6 × their width (full-page
   captures) are cut into overlapping parts (at most 6) so small comment text stays readable.
2. **Vision model** (`app/scanner/extraction/vision.py`): Chat Completions with Structured
   Outputs (strict JSON schema, `schema.py`). The prompt asks for a verbatim transcription in the
   original script (Arabic, Darija in Arabic or Latin letters, French, English, emojis), the
   foreground only when a dialog or photo viewer is open, no interface text, and explicitly **no
   translation, summary, opinion or sentiment**. Prompt version `scanner-extract-v1` is stored
   with each result.
3. **Checking** (`schema.parse`): unknown references are dropped, never guessed; empty or
   sticker-only comments are skipped; replies are linked to their comment and comments to their
   post (`parent_id`).
4. **Dates** (`dates.py`): the capture time comes from the file name when it carries one
   (Windows "Capture d'écran 2026-11-23 064458", WhatsApp, Android, iOS, Scanner's own captures),
   else the upload time. Relative labels ("9 h", "1 j", "2 sem.", "Hier à 14:05", "21 novembre",
   Arabic forms) become `published_at` with a precision (minute … year); the label itself is kept.
5. **Duplicates across screenshots** (`text.py`): the same author + text (after normalising Arabic
   letter variants, diacritics, punctuation, emojis and spacing) in another screenshot of the
   collection is linked with `duplicate_of` and not counted. A copy cut by the screen edge or by
   "voir plus" is linked to the full one, whichever came first.
6. **Status**: `ready`, or `needs_review` when confidence is below
   `REPINTEL_SCANNER_EXTRACTION_MIN_CONFIDENCE`, the image is not a social network or no text
   was found ("unreadable parts" is shown but does not send to review on its own); `failed` with `error_code` (`quota_exceeded`,
   `auth_failed`, `model_not_found`, …). Temporary provider errors are retried (4 attempts).

Script (`arabic`, `latin`, `mixed`) is detected from the letters only; no language, topic or
sentiment is attached in Scanner.

**Review screen** (`/collect/items/{id}`): the image next to the entries (post, comments,
indented replies), with time, counters and flags (cut by "voir plus", cut by the edge, already in
another screenshot, corrected). The agent fixes an author or a text (only those two fields can be
changed, enforced by column grants) or deletes an entry that is not content, then presses **"Valider"**: the screenshot is marked
checked (`validated_at`, status `ready`) and the next screenshot still to check opens. The
collection screen shows "n captures validées sur N" and a link to the next one. "Relire la
capture" reads it again, replaces the entries and clears the validation.

### Extraction benchmark

First run (2026-11-23, prompt v2, 104 real captures of the product owner, 50 hand-checked
entries):

| Model | Entries found | Exact authors | Exact texts | Char. errors | Avg. time | Cost / 1,000 captures |
|---|---|---|---|---|---|---|
| gpt-6.1-sol | 48/50 | 45 | 37 | 1.0 % | 19 s | ≈ 16 $ |
| gpt-6.1-terra | 48/50 | 45 | 35 | 0.8 % | 18 s | ≈ 8 $ |
| gpt-6-luna | 48/50 | 42 | 16 | 3.7 % | 23 s | ≈ 1.5 $ |

gpt-6.1-terra was recommended for accuracy; the product owner chose **gpt-6-luna** (2026-11-24)
for cost (≈ 5× cheaper), accepting more letter changes, corrected on the review screen when
needed. With luna, `REPINTEL_SCANNER_EXTRACTION_MIN_CONFIDENCE=0.65` sends about 10 % of
captures to review (0.75 would send about 30 %).

Compare models on real screenshots before choosing one (no database, nothing stored in Scanner):

```bash
docker compose run --rm --no-deps -v "$PWD/captures:/captures" migrate \
  python -m app.scanner.extraction.bench /captures \
  --model gpt-6.1-terra --model gpt-6-luna \
  --price gpt-6.1-terra=1,5 --price gpt-6-luna=0.10,0.50
```

It processes every model on an image before the next image, rewrites the report every 10 results (Ctrl+C stops cleanly) and reuses results already obtained with the same prompt version (`--fresh` to redo them). It writes `captures/rapport-extraction/index.html` (each image next to what each model read,
duration, tokens, estimated cost per 1,000 captures) and the raw JSON per image. With
`captures/attendu/<image name>.json` (`{"entries": [{"author": "...", "text": "..."}]}`, written
by hand), each model is scored: entries found / missed / extra, exact authors and texts,
character error rate. Prices are in USD per million tokens, from the provider's pricing page.
Expected-text files contain real names: keep them out of git.

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/scanner/auth/register` | `{email, password (≥12), full_name, locale}`; 409 `email_taken`; rate-limited |
| POST | `/scanner/auth/login` | `{email, password}`; rate-limited per IP and per email |
| POST | `/scanner/auth/refresh` | Body `{refresh_token}` or the `sc_refresh` cookie; rotates |
| POST | `/scanner/auth/logout` | Revokes the refresh family; access tokens stop working immediately |
| GET | `/scanner/auth/me` | Account profile |
| GET | `/scanner/anypulse` | `{state: active\|revoked, revoked_at, revoke_reason}`: may this agent send collections? No AynPulse data |
| GET | `/scanner/sessions/current` | The open collection, created on first use |
| GET | `/scanner/sessions` | History, newest first, `limit` / `offset` |
| GET / PATCH | `/scanner/sessions/{id}` | PATCH `{title}` |
| POST | `/scanner/sessions/{id}/cancel` | Open collections only (409 `session_not_open`) |
| POST | `/scanner/sessions/{id}/screenshots` | Multipart `files` (PNG/JPEG/WebP) → `{results: [{filename, status, code, item_id}], items, session}`; 413 `too_many_files`, 409 `session_not_open`, 503 `storage_unavailable` |
| GET | `/scanner/sessions/{id}/items` | Items in order, with file metadata and read-back URL |
| PUT | `/scanner/sessions/{id}/items/order` | `{item_ids}`: every item exactly once (409 `invalid_order`) |
| DELETE | `/scanner/items/{id}` | Removes the item and its file (open collections only) |
| GET | `/scanner/files/{id}/content` | The sanitised image, owner only |
| GET | `/scanner/extraction` | `{enabled}` |
| GET | `/scanner/items/{id}/entries` | Entries read from the screenshot, in reading order |
| POST | `/scanner/items/{id}/extract` | Read again (replaces entries and corrections); 409 `extraction_disabled`; rate-limited |
| POST | `/scanner/sessions/{id}/send` | Phase 7: send to AynPulse (409 `nothing_to_send`, `still_reading`; 403 `anypulse_access_revoked`) |
| POST | `/scanner/sessions/{id}/resend` | Retry a `failed` sending (409 `not_failed`) |
| POST | `/scanner/sessions/{id}/links` | `{url}` → `{item_id, url, platform, created}`: record a pasted link (once per collection); 422 `bad_url` |
| POST | `/scanner/items/{id}/validate` | `{validated: true|false}` (default true): the agent confirms the reading; 409 `not_extracted` before it is read |
| POST | `/scanner/sessions/{id}/extract` | Queue every screenshot still `pending` or `failed` → `{queued}` |
| PATCH | `/scanner/entries/{id}` | `{author_name?, text?}`: agent correction (`edited_by_agent`) |
| DELETE | `/scanner/entries/{id}` | Remove an entry that is not content (204) |
| GET | `/scanner/browser` | `{enabled, ws_url, has_saved_session, saved_at}` |
| DELETE | `/scanner/browser/profile` | Forget the saved browser session (204) |
| WS | `/scanner/browser/ws` | scanner-browser service. Client → `start {session_id, width, height, mobile, url?, link_item_id?}`, `link {item_id|null}`, `capture_thread`, `stop_thread` (server → `thread_progress {n, result}`, `thread_done {added, duplicates, rejected, reason}`), `navigate {url}`, `click {x, y}`, `scroll {dx, dy}`, `type {text}`, `key {key}` (whitelist), `back`, `forward`, `reload`, `capture`. Server → binary JPEG frames, `ready`, `url {url, title, platform}`, `captured {result}`, `error {code}`, `closed {reason}`. Close codes 4401 (not signed in), 4403 (wrong origin) |

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
| `REPINTEL_SCANNER_BROWSER_ENABLED` | `false` | Shows the built-in browser in the Scanner app |
| `REPINTEL_SCANNER_BROWSER_PUBLIC_URL` | empty | WebSocket URL for clients; empty = same origin (production, behind Caddy) |
| `REPINTEL_SCANNER_BROWSER_ALLOWED_ORIGINS` | origin of `SCANNER_BASE_URL` | Origins allowed to open the WebSocket |
| `REPINTEL_SCANNER_BROWSER_MAX_SESSIONS` | `4` | Live browsers per service instance (≈ 500 MB RAM each) |
| `REPINTEL_SCANNER_BROWSER_IDLE_SECONDS` | `600` | Closes an unused browser |
| `REPINTEL_SCANNER_BROWSER_MAX_STATE_BYTES` | `2000000` | Saved session size cap (local storage dropped first) |
| `REPINTEL_SCANNER_ATTRIBUTION_MODEL` | empty | Language model routing screenshots that name no followed brand; empty = straight to the review queue |
| `REPINTEL_SCANNER_ATTRIBUTION_MIN_CONFIDENCE` | `0.7` | Below: review queue |
| `REPINTEL_SCANNER_THREAD_MAX_CAPTURES` | `20` | "Capturer tout le fil": captures per run |
| `REPINTEL_SCANNER_THREAD_SETTLE_MS` | `1200` | Wait after each scroll for comments to load |
| `REPINTEL_SCANNER_EXTRACTION_MODEL` | empty | Vision model id (OpenAI); empty = extraction off, screenshots stay `pending` |
| `REPINTEL_SCANNER_EXTRACTION_DETAIL` | `high` | Image detail sent to the model |
| `REPINTEL_SCANNER_EXTRACTION_MAX_OUTPUT_TOKENS` | `8000` | Long comment threads need room |
| `REPINTEL_SCANNER_EXTRACTION_MIN_CONFIDENCE` | `0.75` | Below: `needs_review` |
| `REPINTEL_SCANNER_EXTRACTION_TIMEZONE` | `Africa/Algiers` | Local time of screenshot file names and of dates without a zone |

Local development: `docker compose up --build`, then http://localhost:3001 (Scanner) and
http://localhost:3000 (AynPulse). The `rustfs` service provides S3-compatible storage on
port 9000; the development bucket is created automatically. `scanner-browser` listens on
port 8100 (the Scanner app connects to it directly in development). Extraction runs in the `worker` service: to try Scanner alone,
`docker compose up --build scanner-web scanner-browser worker`.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | Audit, integration plan, decisions | Done |
| 1 | Foundation: accounts, auth, sessions, RLS, web app shell, CI, docs | Done |
| 2 | AynPulse access granted at sign-up; platform-admin revocation | Done |
| 3 | Screenshots: built-in browser with saved session (main action) and upload (drag and drop, camera, progress, retry, reorder, remove, duplicates, sanitisation, private storage) | **Done** |
| 4 | Extraction by a vision model: parts for tall images, threads, dates, duplicates across screenshots, confidence, review and correction screen, benchmark | **Done** |
| 5 | Pasted links recorded and opened in the built-in browser, captures attached to them | **Done** |
| 5b | URL connectors (YouTube official API first; Apify behind the legal-review gate) and Excel import | Planned (D6) |
| 6 | Retention: sent images 30 days, unsent collections 30 days without activity (warning 7 days before) | **Done** |
| 7 | Sending to AynPulse: attribution (names, aliases, keywords, optional model), delivery to every organisation following the brand, validated/unvalidated marking, admin review queue | **Done** |
| 8 | Hardening | Planned |
| 9 | Production deployment: Caddy host for Scanner with the WebSocket route, `scanner-web` and `scanner-browser` under `--profile scanner`, Scaleway bucket ([DEPLOYMENT_SCALEWAY.md](DEPLOYMENT_SCALEWAY.md)) | Ready, preproduction pending |

## Known limitations

- Extraction: a reply whose parent comment is in another screenshot is attached to the post, not
  to that comment. Text hidden behind "voir plus" is not fetched: open the comment, then capture.
  Correcting a text does not re-run duplicate detection.

- Built-in browser: frames are JPEG at quality 60 (captures are full-quality PNG). Mobile
  keyboards that do not report Backspace use the Erase button. No copy/paste from the phone's
  clipboard into the page yet. Video and audio are not streamed.

- Stored screenshots are not yet deleted after a retention period, and objects left behind by
  a failed database commit are not swept yet (phase 6).
- Re-encoding JPEG/WebP at quality 92 slightly changes pixels; text stays legible for OCR.
- Anyone can create a Scanner account (as with AynPulse sign-up). Sign-up is rate-limited;
  e-mail verification for Scanner accounts is not built yet.
- **For phase 7:** AynPulse mentions require a brand today; the central space must hold
  collected content until RepIntel's AI attributes it to a brand and customer.
