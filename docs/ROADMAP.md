# Roadmap

Each milestone ends with passing tests, migrations, docs and a recorded list of limitations.

| Milestone | Scope | Status |
|---|---|---|
| **0 — Inspection & design** | Repo inspection, architecture, data model, security model, roadmap | ✅ Done |
| **1 — Foundation** | Monorepo, Docker Compose, config, Postgres + RLS, Redis, FastAPI, Next.js, auth (register/login/refresh/logout), multi-tenancy, RBAC, audit log, rate limiting, Alembic, CI, cross-tenant tests. Brands CRUD included as the first tenant-scoped resource to prove isolation end to end. | ✅ Done |
| **1.1 — Auth hardening** | TOTP MFA + recovery codes, password reset & change, email verification (Brevo), MFA secrets encrypted at rest | ✅ Done (session list & remote revoke moved to M2) |
| **2 — Brands & monitoring** | Session list & revoke, email sending via worker with retries, invitations by email, products, competitors, keywords, monitoring projects, RabbitMQ worker + scheduler, entitlements skeleton, dashboard aggregates | Planned |
| **3 — Source intelligence** | Connector interface, RSS, website reader (SSRF-safe fetcher), one search provider, source registry + coverage states, collection pipeline, dedup (hash + near-dup), provenance, OpenSearch index. Agent Reach adapter after licence/behaviour review. | Planned |
| **4 — Algerian NLP** | Annotation workflow, `ALGERIAN_NLP_BENCHMARK`, language/Arabizi/code-switch detection, normalizer, LLM-backed sentiment/intent/sarcasm with calibration and `UNCERTAIN`. Support claimed only per measured benchmark results. | Planned |
| **5 — Reputation intelligence** | Entity resolution, sentiment analytics, trends, crisis detection, competitor comparison, alert rules, PDF/CSV/XLSX reports | Planned |
| **6 — Browser fleet** | Worker registry, scheduler, mission state machine, context isolation, egress controls, CAPTCHA detection → human intervention UI | Planned |
| **7 — Multi-agent** | LangGraph orchestrator + agents over typed service interfaces, prompt-injection test suite | Planned |
| **8 — Commercialisation** | Billing provider abstraction, plans/entitlements, usage metering, admin console, production deployment | Planned |

## Decisions (2026-11-05)

| Topic | Decision | Consequence for the build |
|---|---|---|
| Search provider | **Deferred** | M3 ships the `SearchProvider` interface + RSS/website connectors; no search vendor is wired in until chosen. |
| Hosting | **EU region** | Requires a legal opinion on cross-border transfer under Law 18-07 (and possibly ANPDP authorisation) **before** real customer data is stored. Pick EU cloud region in M8. |
| Billing | **Manual invoicing** | M8 builds plans, entitlements, usage metering and invoice records (PDF, bank transfer / purchase order). No payment-gateway integration for now; provider interface kept for later. |
| NLP annotation | **Automatic by the SaaS** (revised 2026-11-05) | M4 builds an auto-annotation pipeline: several independent LLM "annotators" (different models/prompts) label each text; agreement → accepted label with confidence, disagreement → automatic arbitration or `UNCERTAIN`. Corrections users make on mentions feed back into the corpus. A small human-verified **gold set** is still required to measure accuracy (the benchmark can't be graded by the same models it evaluates); verified by the founder (target 300–500 texts, one-click confirm/correct UI). |

## Open questions (remaining)

1. **Search provider(s)** — still open; needed before source discovery goes beyond RSS/known sites.
2. **Legal review** of EU hosting under Law 18-07 (transfer conditions, ANPDP formalities).
3. **Agent Reach** — commercial use must be confirmed against its licence and each underlying platform's terms before enabling.
4. **Product name** — `RepIntel` is a placeholder.
