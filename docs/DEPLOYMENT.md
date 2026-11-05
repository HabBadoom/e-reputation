# Deployment

> Production deployment is Milestone 8. This records the requirements the code already assumes.

- **Database**: PostgreSQL 16 with pgvector. Create `repintel_owner` (BYPASSRLS, migrations only) and `repintel_app` (NOBYPASSRLS, API) with passwords from the secret manager. Run `infra/postgres/create-db.sh`, then `alembic upgrade head` as owner. The API must never get owner credentials.
- **Secrets**: `REPINTEL_JWT_SECRET` and DB/Redis/RabbitMQ credentials from a secret manager (Vault or cloud KMS-backed), injected as env vars. Rotate the JWT secret by deploying it and accepting a forced re-login.
- **TLS** at the edge; `REPINTEL_COOKIE_SECURE=true`; HSTS is sent when `REPINTEL_ENV=production`.
- **Proxy headers**: the API trusts `X-Forwarded-*` (`--forwarded-allow-ips`); restrict this to your load balancer's addresses in production so rate-limit IPs can't be spoofed.
- **Redis** is required in every non-test environment: login rate limiting fails closed without it.
- **Images**: `backend/Dockerfile`, `frontend/Dockerfile` (Next.js standalone). Both run as UID 10001. Set `REPINTEL_BACKEND_URL` as a **build arg** for the frontend (rewrites are resolved at build time).
- **Health**: `/healthz` (liveness), `/readyz` (DB + Redis).
- **Region / data residency**: undecided, pending legal review under Algerian Law 18-07 (see ROADMAP).
