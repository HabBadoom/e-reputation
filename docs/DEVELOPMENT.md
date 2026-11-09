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
uv run python -m app.jobs.worker      # in another terminal: emails + monitoring scheduler
cd ../frontend && npm install && npm run dev
```

Without the worker, emails and monitoring runs stay queued (visible with `SELECT * FROM job_stats()` as the owner role).

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
| `REPINTEL_ENCRYPTION_KEYS` | — (required) | Fernet key(s), comma-separated; first encrypts, all decrypt (rotation) |
| `REPINTEL_APP_BASE_URL` | `http://localhost:3000` | Used to build links in emails |
| `REPINTEL_EMAIL_PROVIDER` | `console` | `brevo` (required in staging/production), `console` (dev: logs emails), `memory` (tests) |
| `REPINTEL_BREVO_API_KEY` | — | Required when provider is `brevo`. Secret: env/secret manager only |
| `REPINTEL_BREVO_API_URL` | `https://api.brevo.com/v3/smtp/email` | |
| `REPINTEL_EMAIL_FROM_ADDRESS` / `_NAME` | `no-reply@example.com` / `RepIntel` | Sender must be verified in Brevo |
| `REPINTEL_EMAIL_RATE_LIMIT_PER_HOUR` | `5` | Verification resends / reset requests per address |
| `REPINTEL_EMAIL_VERIFY_TTL_SECONDS` | `172800` | 48 h |
| `REPINTEL_PASSWORD_RESET_TTL_SECONDS` | `3600` | 1 h |
| `REPINTEL_MFA_CHALLENGE_TTL_SECONDS` | `300` | Time to enter the code after the password |
| `REPINTEL_DEFAULT_PLAN` | empty | Plan key for new organizations; empty = plan flagged `is_default` (`trial`) |
| `REPINTEL_TRIAL_DAYS` | `14` | Recorded on the subscription (not enforced yet) |
| `REPINTEL_SERPER_API_KEY` | — | Enables the Serper connector. Secret |
| `REPINTEL_SEARCH_COUNTRY` | `dz` | Serper `gl` |
| `REPINTEL_SEARCH_RESULTS_PER_QUERY` | `20` | Serper `num` |
| `REPINTEL_SEARCH_ENDPOINTS` | `["news","search"]` | JSON list |
| `REPINTEL_MAX_SEARCH_QUERIES_PER_RUN` | `30` | Cost cap per run |
| `REPINTEL_CRAWLER_USER_AGENT` | `RepIntelBot/0.1 (+https://example.com/bot)` | Set a real info URL |
| `REPINTEL_CRAWLER_ROBOTS_TOKEN` | `RepIntelBot` | robots.txt user-agent |
| `REPINTEL_FETCH_TIMEOUT_SECONDS` / `_MAX_BYTES` | `10` / `2097152` | |
| `REPINTEL_VERIFY_PAGES` | `true` | Check search hits on the real page |
| `REPINTEL_MAX_PAGE_FETCHES_PER_RUN` | `25` | |
| `REPINTEL_MAX_FEED_ITEMS_PER_SOURCE` | `200` | |
| `REPINTEL_OPENAI_API_KEY` | — | Enables AI analysis. Secret |
| `REPINTEL_NLP_MODELS` | `[]` | JSON list of model ids, cycled over annotators (required with the key) |
| `REPINTEL_NLP_ARBITER_MODEL` | first of `NLP_MODELS` | |
| `REPINTEL_NLP_ANNOTATORS` | `3` | 2–5 |
| `REPINTEL_NLP_BATCH_SIZE` | `25` | Mentions per analysis job |
| `REPINTEL_NLP_MIN_MACRO_F1` / `_MIN_COVERAGE` / `_MIN_GOLD_ITEMS` | `0.70` / `0.60` / `100` | Validation thresholds |
| `REPINTEL_NLP_BENCHMARK_MAX_ITEMS` | `500` | |
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
| `REPINTEL_ANALYTICS_TIMEZONE` | `Africa/Algiers` | Calendar days for analytics, alerts and reports |
| `REPINTEL_REPUTATION_MIN_SAMPLE` | `10` | Analysed mentions needed before showing the reputation index |
| `REPINTEL_TREND_BASELINE_DAYS` | `28` | History used as the usual level for spikes and topics |
| `REPINTEL_CRISIS_QUIET_HOURS` | `48` | A potential crisis is resolved after this long undetected |
| `REPINTEL_ALERT_EVAL_EVERY_MINUTES` | `15` | Periodic evaluation of alert rules |
| `REPINTEL_ALERTS_PER_RULE_PER_RUN` | `20` | Flood guard |
| `REPINTEL_REPORT_RENDER_TIMEOUT_SECONDS` | `60` | PDF rendering timeout |
| `REPINTEL_REPORT_MAX_BYTES` | `20000000` | Largest stored report |
| `REPINTEL_BROWSER_ENABLED` | `true` | Hand JavaScript pages to the browser fleet |
| `REPINTEL_BROWSER_WORKER_MAX_CONTEXTS` | `2` | Isolated contexts per worker process |
| `REPINTEL_BROWSER_WORKER_NAME` | hostname | Name shown in the fleet view |
| `REPINTEL_BROWSER_TENANT_CONCURRENCY` | `2` | Missions running at once per organization |
| `REPINTEL_BROWSER_MISSIONS_PER_RUN` | `20` | Render missions a monitoring run may create |
| `REPINTEL_BROWSER_HEARTBEAT_SECONDS` / `_STALE_SECONDS` | `10` / `120` | Liveness; silent workers' missions are re-queued |
| `REPINTEL_BROWSER_MIN_FREE_MEMORY_MB` | `300` | Below this, a worker takes no new mission |
| `REPINTEL_BROWSER_RESTART_AFTER_MISSIONS` | `100` | Browser recycled periodically |
| `REPINTEL_BROWSER_RESTRICTED_AFTER_CHALLENGES` | `3` | Challenges in a row before a source is marked restricted |
| `REPINTEL_BROWSER_EXTRA_ARGS` | `[]` | Extra Chromium flags (e.g. an egress proxy) |
| `REPINTEL_AGENT_MODEL` | first of `REPINTEL_NLP_MODELS` | Model for the Response agent and request routing |
| `REPINTEL_AGENT_STEP_TIMEOUT_SECONDS` / `_TASK_TIMEOUT_SECONDS` | `60` / `600` | Agent deadlines |
| `REPINTEL_AGENT_MAX_RESPONSE_DRAFTS` | `3` | Drafts per crisis task |
| `REPINTEL_AI_APPROVAL_EXPIRY_HOURS` | `72` | Pending approvals expire after this |
| `REPINTEL_BACKEND_URL` (frontend, build time) | `http://localhost:8000` | Target of the `/api/*` rewrite |

## Adding a tenant-scoped table (checklist)

1. Migration: `tenant_id uuid NOT NULL REFERENCES tenants`, composite FK `(tenant_id, parent_id)` to parents, `UNIQUE (tenant_id, id)` if it will be a parent.
2. `ENABLE` + `FORCE ROW LEVEL SECURITY`, `tenant_isolation` policy, `GRANT` to the app role.
3. Add it to `DATA_TABLES` in `tests/conftest.py` and to the RLS SQL suite.
4. Add cross-tenant cases to `test_tenant_isolation.py`.
5. In code, filter by `ctx.tenant_id` explicitly as well; never take tenant IDs from request bodies.
