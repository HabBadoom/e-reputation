# Roadmap

Each milestone ends with passing tests, migrations, docs and a recorded list of limitations.

| Milestone | Scope | Status |
|---|---|---|
| **0 — Inspection & design** | Repo inspection, architecture, data model, security model, roadmap | ✅ Done |
| **1 — Foundation** | Monorepo, Docker Compose, config, Postgres + RLS, Redis, FastAPI, Next.js, auth (register/login/refresh/logout), multi-tenancy, RBAC, audit log, rate limiting, Alembic, CI, cross-tenant tests. Brands CRUD included as the first tenant-scoped resource to prove isolation end to end. | ✅ Done |
| **1.1 — Auth hardening** | TOTP MFA + recovery codes, password reset & change, email verification (Brevo), MFA secrets encrypted at rest | ✅ Done (session list & remote revoke moved to M2) |
| **2 — Brands & monitoring** | Products, competitors, keywords, monitoring projects + scheduler, background worker (Postgres queue, retries, dead-letter), emails via queue, invitation emails & sign-up through invitations, session list & revoke (immediate), plan entitlements, dashboard aggregates | ✅ Done |
| **3 — Source intelligence** | Connector interface + compliance gate, Serper (search & news), RSS/Atom, web page verification & feed discovery (SSRF-safe, robots.txt), source registry with coverage states, collection pipeline with provenance, URL + repost dedup, mentions inbox & CSV export. **Deferred:** OpenSearch (Postgres filtering is enough at current volume), Agent Reach (licence review first) | ✅ Done |
| **4 — Algerian NLP** | Deterministic layer (script, language, Darija markers, code-switching, normaliser, seed lexicon), OpenAI ensemble (3 annotators + arbiter, strict outputs, injection-safe), automatic corpus with opt-in, gold-set verification screen, `ALGERIAN_NLP_BENCHMARK` (macro-F1, P/R, confusion, coverage) gating validated tasks, corrections | ✅ Done (calibrated probabilities after the first gold set) |
| **5 — Reputation intelligence** | Sentiment analytics and reputation index, trends and emerging topics, crisis lifecycle, competitor share of voice, alert rules (in-app, e-mail digest, signed webhooks), PDF/XLSX/CSV/HTML reports in fr/ar/en with rule-based recommendations | ✅ Done (scheduled report delivery by e-mail, entity resolution beyond aliases → later) |
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

0. **Plan limits** — only a `trial` plan (placeholder limits: 2 brands, 2 projects, 3 members, every 6 h) and an internal `unlimited` plan exist. Real tiers (Starter → Enterprise) and their limits need to be defined.

1. **Search provider** — Serper chosen (2026-11-06). **Legal review of Serper / Google-derived results is required before production** (blocked there until `connector_policies` is approved).
2. **Legal review** of EU hosting under Law 18-07 (transfer conditions, ANPDP formalities).
3. **OpenAI data processing** — DPA and Law 18-07 review before production (blocked there until `connector_policies('openai')` is approved).
4. **Agent Reach** — commercial use must be confirmed against its licence and each underlying platform's terms before enabling.
5. **Product name** — `RepIntel` is a placeholder.
