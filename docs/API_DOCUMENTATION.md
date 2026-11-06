# API

Base path `/api/v1`. Interactive docs (non-production): `/api/docs`, schema `/api/openapi.json`.

## Conventions

- **Auth**: browser → httpOnly cookies + `X-CSRF-Token` header (value of the `ri_csrf` cookie) on POST/PATCH/DELETE. API client → send `X-Auth-Mode: token` on login/register/refresh to receive tokens in the body, then `Authorization: Bearer <access_token>`.
- **Tenant**: always the tenant in your token. Switch with `POST /auth/switch-tenant`.
- **Errors**: `{"error": {"code", "message", "request_id", "details?"}}`. Codes include `unauthorized`, `token_expired`, `membership_revoked`, `forbidden`, `csrf_failed`, `not_found`, `conflict`, `duplicate`, `last_owner`, `email_taken`, `email_unverified`, `invalid_token`, `invalid_mfa_code`, `mfa_enabled`, `already_verified`, `session_revoked`, `plan_limit_reached` (402), `run_in_progress`, `invalid_invitation`, `validation_error`, `rate_limited`, `internal_error`.
- **Pagination**: `limit` + `offset`, response `{items, total, limit, offset}`.
- **Request IDs**: send `X-Request-ID` to correlate; always echoed back.
- Resources in another tenant return **404**, identical to non-existent ones.

## Endpoints

| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/auth/register` | public, rate-limited | Creates user + organization (owner), or joins one with `invitation_token` instead of `organization_name` |
| POST | `/auth/login` | public, rate-limited | Optional `tenant_id` |
| POST | `/auth/login/mfa` | MFA challenge token | `{mfa_token, code}`; code = 6 digits or recovery code. When MFA is on, `/auth/login` returns `{mfa_required: true, mfa_token, expires_in}` instead of a session |
| POST | `/auth/email/verify` | public | `{token}` from the email link |
| POST | `/auth/email/resend` | authenticated, 5/h | 409 if already verified |
| POST | `/auth/password/forgot` | public, rate-limited | Always 202 |
| POST | `/auth/password/reset` | public | `{token, password}`; revokes all sessions |
| POST | `/auth/password/change` | authenticated | `{current_password, new_password}`; returns a fresh session |
| GET | `/auth/mfa` | authenticated | `{enabled, recovery_codes_remaining}` |
| POST | `/auth/mfa/setup` | authenticated | `{secret, otpauth_uri}` (not yet active) |
| POST | `/auth/mfa/enable` | authenticated | `{code}` → `{recovery_codes}` (shown once) |
| POST | `/auth/mfa/disable` | authenticated | `{password, code}` |
| POST | `/auth/mfa/recovery-codes` | authenticated | `{code}` → new `{recovery_codes}` |
| POST | `/auth/refresh` | refresh token | Rotates; reuse revokes family |
| POST | `/auth/logout` | — | Revokes refresh family, clears cookies |
| GET | `/auth/me` | authenticated | User, current tenant, permissions, all tenants |
| POST | `/auth/switch-tenant` | member of target | New token pair |
| GET / PATCH | `/organization` | `tenant:read` / `tenant:update` | |
| GET | `/roles` | authenticated | |
| GET | `/members` | `members:read` | |
| PATCH | `/members/{membership_id}` | `members:manage` | `{role}`; rank rules apply |
| DELETE | `/members/{membership_id}` | `members:manage` (or self = leave) | Last owner protected |
| GET / POST | `/invitations` | `members:manage` | POST returns `token` **once** |
| DELETE | `/invitations/{id}` | `members:manage` | Revoke |
| POST | `/invitations/accept` | authenticated, verified email must match | Joins and switches to that tenant |
| GET / POST | `/brands` | `brands:read` / `brands:write` | `q` searches name + aliases; `status` filter |
| GET / PATCH / DELETE | `/brands/{id}` | read / write / `brands:delete` | |
| POST | `/brands/{id}/aliases` | `brands:write` | `script` auto-guessed if omitted |
| DELETE | `/brands/{id}/aliases/{alias_id}` | `brands:write` | |
| GET | `/dashboard/summary` | `brands:read` | Real counts; unbuilt capabilities flagged `available: false` |
| GET | `/audit-logs` | `audit:read` | |
| GET / DELETE | `/auth/sessions` | authenticated | List own sessions; DELETE = sign out all others |
| DELETE | `/auth/sessions/{id}` | authenticated | Sign out one session (immediate) |
| GET | `/organization/plan` | `tenant:read` | Plan, limits (`null` = unlimited) and usage |
| GET / POST | `/brands/{id}/products` | `brands:read` / `brands:write` | `{name, category, description, aliases[]}` |
| PATCH / DELETE | `/brands/{id}/products/{pid}` | `brands:write` | |
| GET / POST | `/brands/{id}/competitors` | read / write | `{name, website, aliases[], notes}`; plan limit per brand |
| PATCH / DELETE | `/brands/{id}/competitors/{cid}` | `brands:write` | |
| GET / POST | `/brands/{id}/keywords` | read / write | `{expression, kind: include\|exclude, language?}` |
| DELETE | `/brands/{id}/keywords/{kid}` | `brands:write` | |
| GET / POST | `/monitoring-projects` | `monitoring:read` / `monitoring:write` | `{name, brand_id, frequency_minutes, include_competitors, languages[], status}` |
| GET / PATCH / DELETE | `/monitoring-projects/{id}` | read / write | Pausing clears `next_run_at`; resuming schedules now |
| POST | `/monitoring-projects/{id}/runs` | `monitoring:write` | 202, queues a manual run; 409 if one is queued/running |
| GET | `/monitoring-projects/{id}/runs` | `monitoring:read` | Latest runs with `summary` |
| GET | `/monitoring-projects/{id}/query-plan` | `monitoring:read` | Exact terms/exclusions a run will search |
| GET | `/mentions` | `mentions:read` | Filters: `brand_id, project_id, entity_type, verification, domain, since, until, q, include_reposts, sentiment, language, about_target`; each item has an `analysis` block |
| GET | `/mentions/{id}` | `mentions:read` | Full provenance (canonical URL, connector, provider fields, source) |
| GET | `/mentions/export.csv` | `mentions:read` | Same filters, max 5,000 rows, formula-neutralised |
| GET / POST | `/sources` | `sources:read` / `sources:write` | POST `{kind: rss\|website, url, name?, category?, language?}` checks immediately; websites return discovered feeds |
| POST | `/sources/{id}/check` | `sources:write` | Reachability, robots.txt, feed validity, feed discovery |
| PATCH / DELETE | `/sources/{id}` | `sources:write` | Enable/disable, rename, categorise |
| GET | `/connectors` | `sources:read` | Capabilities, limitations, configured?, compliance status |
| PATCH | `/mentions/{id}/analysis` | `nlp:correct` | `{sentiment?, sarcasm?, about_target?, intents?}`; stored beside the AI output |
| POST | `/mentions/analyze` | `nlp:correct` | Queue analysis of pending/failed/blocked mentions (max 500) |
| GET | `/nlp/status` | authenticated | Per-task validation (latest benchmark vs thresholds) |
| GET | `/nlp/expressions` | authenticated | Seed Darija expression dictionary |
| GET | `/nlp/corpus/stats`, `/nlp/corpus/queue` | platform admin | Corpus counts; gold items awaiting verification |
| POST | `/nlp/corpus/sample` | platform admin | `{n, split}` script-balanced sampling into gold |
| POST | `/nlp/corpus/{id}/verify`, `/reject` | platform admin | `{labels: {sentiment, sarcasm, about_target, language, intents}}` |
| GET / POST | `/nlp/benchmark` | platform admin | Runs (metrics) / start a run |
| GET | `/healthz`, `/readyz` | public | Liveness; readiness checks DB + Redis |

## Example (API client)

```bash
curl -s -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -H 'x-auth-mode: token' -d '{"email":"me@example.dz","password":"…"}'
# → {"access_token":"…","refresh_token":"…","expires_in":900,"tenant_id":"…"}
curl -s localhost:8000/api/v1/brands -H "authorization: Bearer $ACCESS"
```
