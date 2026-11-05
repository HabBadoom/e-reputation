# Roadmap

Each milestone ends with passing tests, migrations, docs and a recorded list of limitations.

| Milestone | Scope | Status |
|---|---|---|
| **0 — Inspection & design** | Repo inspection, architecture, data model, security model, roadmap | ✅ Done |
| **1 — Foundation** | Monorepo, Docker Compose, config, Postgres + RLS, Redis, FastAPI, Next.js, auth (register/login/refresh/logout), multi-tenancy, RBAC, audit log, rate limiting, Alembic, CI, cross-tenant tests. Brands CRUD included as the first tenant-scoped resource to prove isolation end to end. | ✅ Done |
| 1.1 — Auth hardening | TOTP MFA, password reset by email, email verification, session list & revoke | Next |
| **2 — Brands & monitoring** | Invitations, products, competitors, keywords, monitoring projects, RabbitMQ worker + scheduler, entitlements skeleton, dashboard aggregates | Planned |
| **3 — Source intelligence** | Connector interface, RSS, website reader (SSRF-safe fetcher), one search provider, source registry + coverage states, collection pipeline, dedup (hash + near-dup), provenance, OpenSearch index. Agent Reach adapter after licence/behaviour review. | Planned |
| **4 — Algerian NLP** | Annotation workflow, `ALGERIAN_NLP_BENCHMARK`, language/Arabizi/code-switch detection, normalizer, LLM-backed sentiment/intent/sarcasm with calibration and `UNCERTAIN`. Support claimed only per measured benchmark results. | Planned |
| **5 — Reputation intelligence** | Entity resolution, sentiment analytics, trends, crisis detection, competitor comparison, alert rules, PDF/CSV/XLSX reports | Planned |
| **6 — Browser fleet** | Worker registry, scheduler, mission state machine, context isolation, egress controls, CAPTCHA detection → human intervention UI | Planned |
| **7 — Multi-agent** | LangGraph orchestrator + agents over typed service interfaces, prompt-injection test suite | Planned |
| **8 — Commercialisation** | Billing provider abstraction, plans/entitlements, usage metering, admin console, production deployment | Planned |

## Open questions (need a human decision)

1. **Search provider(s)** for Phase 3 — which commercial search API(s) and budget? (No provider is assumed.)
2. **Hosting region** — Algerian data-protection law (Law 18-07) may constrain cross-border transfer of personal data. Needs legal review before choosing a cloud region.
3. **Billing in Algeria** — which local payment provider (e.g. CIB/Edahabia via SATIM) vs Stripe for international?
4. **Annotation workforce** for the Darija/Arabizi corpus — in-house or contracted, and target corpus size.
5. **Agent Reach** — commercial use must be confirmed against its licence and each underlying platform's terms before enabling.
6. **Product name** — `RepIntel` is a placeholder.
