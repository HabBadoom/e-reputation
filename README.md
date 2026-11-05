# RepIntel — Reputation Intelligence Platform

Multi-tenant SaaS for monitoring brand reputation, built first for Algeria (French, Arabic, Darija, Arabizi) with an architecture meant for international expansion. `RepIntel` is a placeholder name.

**Current state: Milestone 1 (Foundation).** Accounts, organizations, team roles, brands with multi-script aliases, audit log, French/Arabic (RTL)/English UI, enforced tenant isolation. **No content is collected yet**: the dashboard says so explicitly instead of showing sample numbers. See [ROADMAP](docs/ROADMAP.md).

## Quick start

```bash
cp .env.example .env
python -c "import secrets;print('REPINTEL_JWT_SECRET='+secrets.token_urlsafe(48))" >> .env
docker compose up --build
```

- App: http://localhost:3000 (register, then add a brand)
- API docs: http://localhost:8000/api/docs
- RabbitMQ UI: http://localhost:15672 (used from Milestone 2)

## Repository layout

```
backend/    FastAPI API (Python 3.12, SQLAlchemy 2, Alembic)
  app/core      config, security, errors, logging, rate limiting
  app/db        models, sessions, per-transaction tenant context
  app/modules   auth · tenancy · brands · audit · dashboard
  migrations/   schema, RLS policies, grants (authoritative)
  tests/        pytest API tests + tests/sql RLS suite
frontend/   Next.js 15 + React 19 + Tailwind + React Query, i18n fr/ar/en
infra/      Postgres role/bootstrap scripts
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
| [CONNECTORS](docs/CONNECTORS.md) · [BROWSER_FLEET](docs/BROWSER_FLEET.md) · [CAPTCHA_INTERVENTIONS](docs/CAPTCHA_INTERVENTIONS.md) · [ALGERIAN_NLP](docs/ALGERIAN_NLP.md) · [AI_AGENTS](docs/AI_AGENTS.md) | Designs for later milestones (not implemented yet) |
