# Architecture

> Status: Phase 0 complete, Phase 1 (Foundation) implemented. Sections marked **(planned)** describe the target design and are not yet built.

## 1. Phase 0 findings

| Item | Finding |
|---|---|
| Existing repository | Empty workspace. No code, schema or configuration to reuse. Project initialised from scratch. |
| Framework | None existing → stack from the master prompt adopted without deviation. |
| Reusable components | None in-repo. External: FastAPI, SQLAlchemy 2, Alembic, Next.js 15, Playwright (Phase 6), Agent Reach (Phase 3, after licence/behaviour review). |
| Security weaknesses | N/A (greenfield). Risks are designed out below. |
| Spec contradiction | §9 of the master prompt says the AI *"must automatically solve or bypass CAPTCHA"*, which contradicts the §9 workflow and rule 22.12. **Interpreted as "must not".** Only human intervention is implemented. |

## 2. Target architecture — modular monolith

```
                ┌──────────────────────────────┐
  Browser ────► │ Next.js (frontend/)          │  /api/* rewritten to backend (same origin → httpOnly cookies)
                └──────────────┬───────────────┘
                               │ HTTPS
                ┌──────────────▼───────────────┐
                │ FastAPI API  (backend/app)   │  versioned /api/v1, auth, RBAC, tenant context
                │  modules: auth · tenancy ·   │
                │  brands · audit · (sources,  │
                │  collection, nlp, analysis,  │
                │  alerts, reports, billing)   │
                └──┬──────────┬──────────┬─────┘
                   │          │          │ publish jobs (planned)
           ┌───────▼──┐  ┌────▼───┐  ┌───▼──────┐      ┌───────────────────┐
           │PostgreSQL│  │ Redis  │  │ RabbitMQ │────► │ Workers (planned) │
           │ + RLS    │  │ limits │  └──────────┘      │ collection · NLP  │
           │(pgvector)│  │ cache  │                    │ browser fleet     │
           └──────────┘  └────────┘                    └─────────┬─────────┘
                                                                 │
                                      OpenSearch (planned) ◄─────┘ Object storage (planned)
```

One deployable API process plus independently deployable workers. Modules talk through service functions, never through each other's tables. Workers are separate processes because collection, NLP and browser work have different scaling and isolation needs from the API.

## 3. Backend module layout

```
backend/app/
  core/        config, security (hashing, JWT), logging, errors, rate limiting
  db/          engine, session + tenant context, base model
  modules/
    auth/      register, login, refresh, logout, me, switch-tenant
    tenancy/   tenants, memberships, roles, permissions, RBAC dependency
    brands/    brands + aliases (first tenant-scoped business resource)
    audit/     append-only audit log
  api/v1/      router assembly
migrations/    Alembic; RLS policies live in migrations, not in ORM code
tests/
```

## 4. Multi-tenancy (defence in depth)

1. **Database roles.** Migrations run as `repintel_owner` (owns tables). The API connects as `repintel_app`: non-superuser, `NOBYPASSRLS`, DML only.
2. **Row-Level Security** is `ENABLE`d and `FORCE`d on every tenant-scoped table. Policies compare `tenant_id` to `current_setting('app.tenant_id', true)::uuid`. With no context set, the setting is NULL and **no rows match** (fail-closed).
3. **Per-transaction context.** Every request opens a transaction and runs `set_config('app.tenant_id', …, true)` (transaction-local), so a pooled connection can never leak context to the next request.
4. **API authorisation.** The tenant comes from the signed token, never from a client-supplied header or body. Membership and role are re-read from the DB on every request, so revocation is immediate.
5. **Cross-tenant tests** at both the DB level (raw SQL as `repintel_app`) and the API level.

Global tables (`users`, `roles`, `permissions`, `role_permissions`) are not tenant-scoped. `users` still has RLS: a user sees only themselves and members of the current tenant. Email lookup at login goes through a narrow `SECURITY DEFINER` function that returns only the fields login needs.

## 5. Authentication

- Argon2id password hashing.
- Short-lived access JWT (default 15 min) containing `sub`, `tid`, `jti`. Opaque refresh token (default 14 days), stored **hashed**, rotated on every use; reuse of a revoked token revokes the whole family.
- Browser clients: tokens in `httpOnly; Secure; SameSite=Lax` cookies + double-submit CSRF token on unsafe methods. API clients: `Authorization: Bearer`.
- Login/registration rate-limited (Redis fixed window, in-memory fallback in tests only).
- MFA (TOTP) — **planned, Milestone 1.1.**

## 6. AI, connectors, browser fleet **(planned)**

- **LLM provider abstraction**: `LLMProvider` protocol, OpenAI first; model IDs from config. Every AI output stored with `model`, `prompt_version`, `analysis_version`, and a calibrated confidence or `UNCERTAIN`.
- **Connectors** implement the §7 interface. Each declares capabilities, access method, permission status and limitations; the Compliance policy engine (deterministic code, not an LLM) decides whether a connector may run for a tenant.
- **Agent Reach**: wrapped behind an allowlisted adapter (fixed subcommands, validated args, subprocess with timeout/memory limits, no shell). Enabled per source only after licence + platform-terms review recorded in `source_access_status`.
- **Browser fleet**: Playwright workers in their own network segment (egress allowlist, metadata endpoints and RFC1918 blocked). One browser context per mission; authenticated profiles keyed by tenant and never reused across tenants. CAPTCHA → mission `WAITING_FOR_HUMAN`; no automated solving.
- **Agents (LangGraph)**: agents call typed service functions, never the DB or shell directly. Fetched web content is passed as data in a delimited channel and cannot change tool permissions.

## 7. Key decisions

| Decision | Choice | Why |
|---|---|---|
| Sync vs async SQLAlchemy | Sync (psycopg 3), FastAPI runs it in threadpool | Simpler transaction/RLS handling; adequate for MVP load. Revisit if profiling says so. |
| Tenant in token vs header | Token (`tid`), switch via endpoint | Client can't forge tenant; one less check to forget. |
| Search | Postgres FTS first, OpenSearch from Phase 3 | Avoid running OpenSearch before there's content to index. |
| Queue | RabbitMQ (in compose now, used from Phase 2) | Per spec. |
| IDs | UUIDv4 | No enumeration; safe across tenants. |
