# Security

## Tenant isolation

Defence in depth, each layer independently tested:

| Layer | Mechanism | Test |
|---|---|---|
| Database | RLS `ENABLE` + `FORCE` on every tenant table; API role is `NOBYPASSRLS`; context unset ⇒ zero rows | `tests/sql/test_rls_isolation.sql` (10 checks) |
| Connection | Context set with `set_config(…, true)` (transaction-local) at the start of *every* transaction | SQL check 10 |
| References | Composite FKs `(tenant_id, parent_id)` so a row can't point at another tenant's parent | SQL check 5 |
| API | Tenant taken from the signed token only; membership + role re-read every request; explicit `tenant_id` filters in queries | `tests/test_tenant_isolation.py` |
| Privileges | App role: DML only, no DDL, read-only RBAC tables, append-only audit log | SQL checks 6–7 |

Pre-authentication lookups (login by email, refresh token, invitation token) use three `SECURITY DEFINER` functions with a pinned `search_path` that return only the columns those flows need. `EXECUTE` is revoked from `PUBLIC`.

**Known nuance:** the `memberships` policy also exposes a user's *own* memberships in other tenants (needed for the tenant switcher). Every membership query in application code therefore filters by `tenant_id` explicitly; `test_multi_tenant_user_sees_only_current_tenant` guards this.

## Authentication

- Argon2id (m=64 MiB, t=3, p=4), constant-time path for unknown emails, identical error for bad email and bad password.
- Access JWT (HS256, 15 min) carries `sub`, `tid`, `jti`, `typ`. `alg=none` and foreign signatures are rejected (tested).
- Refresh tokens: random 48 bytes, stored as SHA-256, rotated on every use. Presenting a rotated-out token revokes the whole family (theft detection, tested).
- Browser: `httpOnly; Secure; SameSite=Lax` cookies, refresh cookie scoped to `/api/v1/auth`. Double-submit CSRF token required on unsafe methods (tested). Tokens are never returned in the body to browsers.
- API clients: `X-Auth-Mode: token` returns tokens in the body; send `Authorization: Bearer`.
- Rate limiting: login per-email (10/min) and per-IP (30/min); register per-IP (20/h). Redis-backed and **fails closed** if Redis is down.
- Disabling a user, removing a member or changing a role takes effect on the next request (tested), not at token expiry.
- **Sessions**: every access token carries its session id (`sid` = refresh-token family). Each request checks the session is still live, so logout, "sign out this device", "sign out everywhere else", password change/reset take effect immediately (tested). Users can list and revoke their sessions; they can't see or revoke anyone else's (RLS, tested).

## Account security (Milestone 1.1)

- **Email verification**: link valid 48 h, single use, a new link invalidates the previous one. Accepting an invitation requires a verified address (otherwise someone could register with another person's email and take the seat they were invited to).
- **Password reset**: `/password/forgot` always answers 202 with the same body (no account enumeration); link valid 1 h, single use, purpose-bound (a verification token can't reset a password). Reset revokes **all** sessions and pending MFA challenges and is written to the audit log of every organization the user belongs to.
- **Password change**: requires the current password; revokes all other sessions and issues a fresh pair for the current one.
- **TOTP MFA** (RFC 6238, SHA-1/6 digits/30 s, ±1 step): secret 160 bits, **encrypted at rest** with Fernet (`REPINTEL_ENCRYPTION_KEYS`, rotation supported), stored in its own user-scoped table. Replay guard: a code's time step must be newer than the last accepted one (row-locked). Login with MFA returns a 5-minute single-use challenge instead of a session; 5 attempts per challenge, 30 per IP per 5 min. Removing a member between password and code blocks the login.
- **Recovery codes**: 10 × 50-bit codes, stored as SHA-256, single use, shown once; regenerate or disable MFA only with a valid code (disable also needs the password).
- All tokens in emails are 256-bit random values stored only as SHA-256 hashes, looked up through `auth_find_user_token` (SECURITY DEFINER, bound to purpose).
- RLS on `user_tokens`, `user_mfa`, `mfa_recovery_codes`: own rows only (tested in `tests/sql/test_rls_user_tables.sql`).
- **Brevo API key**: environment / secret manager only; never logged (error logs record only status and Brevo's error message).

## Background jobs (Milestone 2)

- The `jobs` table is not readable or writable by the app role; only SECURITY DEFINER functions touch it. `enqueue_job` refuses to create a job for a tenant other than the current one.
- The worker runs each job with the DB context set to the job's tenant (no user), so RLS still applies to everything a handler reads or writes.
- Email jobs are marked sensitive: their payload (which may contain a sign-in link) is wiped once delivered.
- Delivery is at-least-once (a job whose worker dies is re-queued after 15 min); handlers are written to tolerate repeats.

## Collection (Milestone 3)

- **SSRF**: every third-party URL goes through `app/collection/fetcher.py`: http/https only, ports 80/443, no credentials in URLs, every redirect hop re-checked, hostnames must resolve **only** to public addresses (metadata endpoints, loopback, RFC 1918, link-local, CGNAT, IPv4-mapped IPv6 refused), 2 MB / 10 s caps. Tested in `tests/test_fetcher.py`. Residual DNS-rebinding risk → egress firewall for workers (M6).
- **Untrusted content**: third-party text is stored as data and rendered escaped (React); links open with `rel="noopener noreferrer nofollow ugc"`; CSV export neutralises formulas (`=`, `+`, `-`, `@`).
- **XML**: feeds parsed with the stdlib (no external entities; expat entity-expansion limits); size-capped.
- **robots.txt** honoured; no login, paywall, CAPTCHA or anti-bot bypass.
- **Compliance gate**: connectors whose legal review is pending are blocked in staging/production (`connector_policies`).
- **Data minimisation**: excerpts, not full pages.
- **Serper API key**: environment only, sent only to `google.serper.dev`, never logged.

## Language analysis (Milestone 4)

- Prompt injection: untrusted text passed as escaped JSON in a `<data>` frame, explicit system rule, no tools, strict output schema re-validated server-side (tested: frame can't be broken, out-of-schema labels rejected).
- OpenAI calls gated by `connector_policies('openai')` (blocked in production until approved); only excerpt/title/brand are sent.
- Corpus: readable only by platform admins (RLS via `app_is_platform_admin()`); tenant contributions only through `corpus_add*` functions that enforce the opt-in; opt-out purges; tenant corrections never enter the gold test split.
- `users.is_platform_admin` cannot be set or changed by the app role (trigger, tested).

## Alerts, webhooks and reports (Milestone 5)

- Webhook URLs: `https://` only (DB CHECK + API), resolved and checked against private/reserved ranges at creation **and** at each delivery through the same SSRF-safe client as collection; redirects are not followed; response bodies capped.
- Webhook signing secrets: generated server-side, shown once, stored Fernet-encrypted, rotatable; requests signed with HMAC-SHA256 over `timestamp.body`.
- Alert rules can only reference the tenant's own brands and webhooks (composite FKs, tested).
- Alert e-mails go only to verified members holding a selected role; contents are escaped.
- PDF rendering: headless Chromium with JavaScript disabled, offline, every request aborted; the HTML is generated by our code with every value escaped. Reports are always served as attachments with `nosniff` and a `default-src 'none'` CSP; spreadsheet cells starting with `= + - @` are neutralised.
- Report generation and on-demand evaluation are rate-limited per tenant; at most 5 reports in progress.

## Browser fleet (Milestone 6)

- Missions need an authorization record (connector review, public address, domain policy, robots.txt, cooldown, quota) at creation and again before navigation; the state machine is enforced by a trigger and every transition audited.
- One fresh Chromium context per mission, destroyed afterwards (verified); no stored profiles or credentials, no logins, downloads, permissions or pop-ups.
- Every request and every redirect hop is checked against private/loopback/link-local/metadata addresses (tested with a redirect to `localhost` and calls to `10.0.0.1` and `169.254.169.254`). **Residual risk**: WebSockets and DNS rebinding are not covered in code → run workers behind an egress firewall (deployment checklist).
- Anti-bot challenges are detected and never bypassed; domains cool down for all tenants. See `CAPTCHA_INTERVENTIONS.md`.
- Worker functions are SECURITY DEFINER with fixed `search_path`; workers write tenant data only inside the tenant's RLS context.
- Fleet tables are readable by platform admins only; domain state contains no tenant data.

## AI agents (Milestone 7)

- Agents reach data only through allowlisted tools (service wrappers) in the tenant's RLS context; no DB session, shell, network client or arbitrary command.
- Effective rights = requesting member's current role ∩ a fixed ceiling (no members, billing, settings, approvals, platform administration); re-evaluated when the task runs.
- Sensitive actions require a human approval by `ai:approve`; enforced in code **and** by database triggers; re-checked at execution; approvals expire; every decision is audited.
- Prompt injection: capability separation (the agent reading untrusted text can only draft; the agent that chooses can only see the member's request and an enum), escaped `<data>` framing, strict schemas, output validation, action kinds fixed in code; CI evaluation suite in French, Darija/Arabizi and Arabic.
- Sending text to the AI provider stays gated by its compliance record (blocked in production until approved).

## Plans, billing and the admin console (Milestone 8)

- Limits are read from `plans`/`plan_entitlements` and enforced server-side (HTTP 402 `plan_limit_reached`); monthly quotas (mentions, AI calls, reports) from metered `usage_events`. An expired or cancelled subscription stops new work (402 `subscription_inactive`) while existing data stays readable.
- Tenants can read but never change their subscription, plans, prices, entitlements or invoices: RLS policies allow writes only when `app_is_platform_admin()`, and triggers refuse the application role otherwise. Issued invoices are immutable (only `paid`/`void` transitions); numbering is gapless per year (`next_invoice_number`, admin only); a draft deleted consumes no number.
- The platform admin flag is set by an operator in the database (the app role cannot change it). The `/admin` console requires it **and** two-step verification (`REPINTEL_ADMIN_REQUIRE_MFA`). Cross-tenant listings go through `SECURITY DEFINER` functions that re-check the flag; acting on an organisation switches the DB context to it and writes the change to **that organisation's** audit log.
- `/metrics` is disabled unless `REPINTEL_METRICS_TOKEN` is set, compares the token in constant time, returns only aggregates and is not routed by the edge proxy.
- Retention: a daily job deletes each organisation's mentions, reports, resolved alerts and browser traces beyond its plan's retention (capped by `REPINTEL_RETENTION_MAX_DAYS`).

## RBAC

Roles `owner (100) > admin (80) > analyst (50) > viewer (10)`; permissions are rows in `role_permissions`. Rules: nobody changes their own role; you can't grant above your rank; only owners grant `owner`; non-owners can't modify equal/higher members; the last owner can't be removed or demoted.

## Other controls

- Structured error envelope; unhandled exceptions are logged with traceback and returned as a generic 500 with `request_id`.
- Security headers on API and frontend (`nosniff`, `DENY` framing, referrer policy, HSTS in production).
- OpenAPI docs disabled in production.
- Secrets only via environment; startup fails if `REPINTEL_JWT_SECRET` is shorter than 32 chars.
- Containers run as non-root.
- `LIKE` patterns from user search are escaped.

## Not yet implemented (tracked)

| Item | Milestone |
|---|---|
| Optional org policy "MFA required for all members" | 3 |
| Separate DB role for the worker (today it shares the API role and calls the queue functions) | 6 |
| Secret manager integration (Vault / cloud KMS) — today an env file rendered by the operator | after launch |
| Red-team evaluation of a real model on the injection corpus (beyond CI simulations) | before production |
| Point-in-time recovery (WAL archiving); today nightly logical dumps + disk snapshots | after launch |

Done in Milestone 8: egress firewall for the fetching containers (`scripts/egress-firewall.sh`), backups with a CI restore test, retention jobs, encrypted disks (provider) and encrypted off-site dumps (age). See DEPLOYMENT.

## Legal

Algerian Law 18-07 on personal data protection applies to users' and authors' data. Hosting region, cross-border transfer and data-retention policy **need qualified legal review** before production. See ROADMAP open questions.

## Reporting a vulnerability

Email security@<your-domain> (to be set). Do not open public issues for vulnerabilities.
