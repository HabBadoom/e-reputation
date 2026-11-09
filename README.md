# RepIntel — Reputation Intelligence Platform

Multi-tenant SaaS for monitoring brand reputation, built first for Algeria (French, Arabic, Darija, Arabizi) with an architecture meant for international expansion. `RepIntel` is a placeholder name.

**Current state: all 8 milestones delivered (v0.9.0).** Monitoring projects collect public mentions (Serper web/news search targeted at Algeria, RSS, page verification, a headless browser fleet for JavaScript pages that detects anti-bot checks and backs off instead of bypassing them) with full provenance. Darija/Arabizi-aware language analysis (3 AI annotators + arbitration, UNCERTAIN when unsure, benchmark against a human-verified gold set), reputation index, trends, crisis detection, competitor share of voice, alerts (in-app, e-mail, signed webhooks) and PDF/XLSX/CSV/HTML reports in French, Arabic and English. Thirteen AI agents analyse and propose actions; sensitive ones and every public reply wait for a human. Plans with quotas and usage metering, manual invoicing (Algerian identifiers), a platform admin console, and a production stack for an EU host. Strict tenant isolation enforced by PostgreSQL row-level security throughout. Nothing is invented: missing data is shown as missing. See [ROADMAP](docs/ROADMAP.md).

## Quick start

```bash
cp .env.example .env
python -c "import secrets;print('REPINTEL_JWT_SECRET='+secrets.token_urlsafe(48))" >> .env
python -c "import base64,os;print('REPINTEL_ENCRYPTION_KEYS='+base64.urlsafe_b64encode(os.urandom(32)).decode())" >> .env
docker compose up --build
```

- App: http://localhost:3000 (register, then add a brand)
- API docs: http://localhost:8000/api/docs
- Production: see [DEPLOYMENT](docs/DEPLOYMENT.md) (`docker-compose.prod.yml`)

## Repository layout

```
backend/    FastAPI API (Python 3.12, SQLAlchemy 2, Alembic)
  app/core      config, security, errors, logging, rate limiting
  app/db        models, sessions, per-transaction tenant context
  app/modules   HTTP routers: auth · tenancy · brands · monitoring · sources · mentions ·
                analytics · alerts · reports · browser · ai · billing · admin · …
  app/collection · app/nlp · app/analytics · app/alerts · app/reports · app/browser ·
  app/agents · app/billing   domain services
  migrations/   schema, RLS policies, grants (authoritative)
  tests/        pytest API tests + tests/sql RLS suite
frontend/   Next.js 15 + React 19 + Tailwind + React Query, i18n fr/ar/en
infra/      Postgres bootstrap (dev and production), Caddy
scripts/    backup / restore, egress firewall
docs/       architecture, security, data model, roadmap, …
```

## Documentation

| | |
|---|---|
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Phase 0 findings, target architecture, decisions |
| [SECURITY](docs/SECURITY.md) | Tenant isolation, auth, threat model |
| [DATA_MODEL](docs/DATA_MODEL.md) | Tables, ER diagram, RLS policies |
| [API_DOCUMENTATION](docs/API_DOCUMENTATION.md) | Endpoints and conventions |
| [DEVELOPMENT](docs/DEVELOPMENT.md) · [TESTING](docs/TESTING.md) · [DEPLOYMENT](docs/DEPLOYMENT.md) | Working on the code |
| [ROADMAP](docs/ROADMAP.md) · [CHANGELOG](docs/CHANGELOG.md) | Plan and history |
| [CONNECTORS](docs/CONNECTORS.md) · [BROWSER_FLEET](docs/BROWSER_FLEET.md) · [CAPTCHA_INTERVENTIONS](docs/CAPTCHA_INTERVENTIONS.md) · [ALGERIAN_NLP](docs/ALGERIAN_NLP.md) · [AI_AGENTS](docs/AI_AGENTS.md) | Subsystem designs and decisions |
