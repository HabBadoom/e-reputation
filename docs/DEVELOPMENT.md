# Development

## Prerequisites

Docker, Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 22.

## Run the stack

```bash
cp .env.example .env    # then set REPINTEL_JWT_SECRET (≥32 chars)
docker compose up --build
```

Or run services natively against compose's Postgres/Redis:

```bash
docker compose up -d postgres redis
cd backend && uv sync && uv run alembic upgrade head
REPINTEL_JWT_SECRET=… REPINTEL_COOKIE_SECURE=false \
REPINTEL_DATABASE_URL=postgresql+psycopg://repintel_app:repintel_app@localhost:5432/repintel \
  uv run uvicorn app.main:app --reload
cd ../frontend && npm install && npm run dev
```

**First install:** commit the generated `backend/uv.lock` and `frontend/package-lock.json`, then switch CI to `uv sync --frozen` / `npm ci`.

## Environment variables

All backend settings use the `REPINTEL_` prefix (`app/core/config.py`).

| Variable | Default | Notes |
|---|---|---|
| `REPINTEL_ENV` | `development` | `development` / `test` / `staging` / `production` |
| `REPINTEL_JWT_SECRET` | — (required) | ≥32 chars; startup fails otherwise |
| `REPINTEL_DATABASE_URL` | app role @ localhost | Must be the NOBYPASSRLS role |
| `REPINTEL_MIGRATION_DATABASE_URL` | owner role @ localhost | Alembic + tests only |
| `REPINTEL_DB_APP_ROLE` | `repintel_app` | Role granted privileges by migrations |
| `REPINTEL_REDIS_URL` | `redis://localhost:6379/0` | Rate limiting |
| `REPINTEL_RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | From M2 |
| `REPINTEL_ACCESS_TOKEN_TTL_SECONDS` | `900` | |
| `REPINTEL_REFRESH_TOKEN_TTL_SECONDS` | `1209600` | 14 days |
| `REPINTEL_COOKIE_SECURE` | `true` | Set `false` only for http://localhost |
| `REPINTEL_COOKIE_DOMAIN` | unset | |
| `REPINTEL_CORS_ORIGINS` | `[]` | Not needed when using the Next.js proxy |
| `REPINTEL_LOGIN_RATE_LIMIT_PER_MINUTE` | `10` | Per email; per-IP is 3× |
| `REPINTEL_REGISTER_RATE_LIMIT_PER_HOUR` | `20` | Per IP |
| `REPINTEL_LOG_LEVEL` | `INFO` | JSON logs to stdout |
| `REPINTEL_BACKEND_URL` (frontend, build time) | `http://localhost:8000` | Target of the `/api/*` rewrite |

## Adding a tenant-scoped table (checklist)

1. Migration: `tenant_id uuid NOT NULL REFERENCES tenants`, composite FK `(tenant_id, parent_id)` to parents, `UNIQUE (tenant_id, id)` if it will be a parent.
2. `ENABLE` + `FORCE ROW LEVEL SECURITY`, `tenant_isolation` policy, `GRANT` to the app role.
3. Add it to `DATA_TABLES` in `tests/conftest.py` and to the RLS SQL suite.
4. Add cross-tenant cases to `test_tenant_isolation.py`.
5. In code, filter by `ctx.tenant_id` explicitly as well; never take tenant IDs from request bodies.
