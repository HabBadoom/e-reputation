# Deployment

Production runs as a Docker Compose stack on one Linux host in the EU
(`docker-compose.prod.yml`). The same images run on Kubernetes or a managed container
service; the requirements below are what matters, not Compose itself.

> **Before the first invoice.** The invoice template (mandatory mentions, numbering,
> RC/NIF/NIS/article d'imposition, VAT rate, stamp duty for cash payments, amount in words)
> follows common Algerian practice; have your accountant check it.
> `REPINTEL_BILLING_DEFAULT_TAX_RATE` (19 %) and stamp duty are settings.
>
> **Environments.** `REPINTEL_ENV=staging` for the preproduction server, `production` for the
> live one. In production a connector runs only once a platform administrator has set it to
> *approved* in **Administration → Connectors**; in staging every connector runs.

The preproduction server on Scaleway (AynPulse, decision of 2026-11-26) is described step by
step in [DEPLOYMENT_SCALEWAY.md](DEPLOYMENT_SCALEWAY.md).

## 1. Host

- Ubuntu 24.04 LTS (or Debian 12), 4 vCPU / 16 GB RAM / 160 GB SSD to start. The browser
  fleet is the memory-hungry part (≈ 600 MB per Chromium context).
- EU provider and region (e.g. Hetzner Falkenstein/Helsinki, OVHcloud Gravelines/Strasbourg,
  Scaleway Paris). Encrypted disks; snapshots enabled.
- Docker Engine ≥ 24 with the Compose plugin. Unattended security upgrades on.
- Firewall: only 22 (restricted to your IPs), 80 and 443 open. SSH by key only.
- DNS: an A (and AAAA if you have IPv6) record for `REPINTEL_DOMAIN` and for
  `SCANNER_DOMAIN` pointing to the host.

## 2. Images

Images are built and pushed to GHCR by `.github/workflows/release.yml` when a tag `v*` is
pushed (`git tag v0.9.0 && git push origin v0.9.0`), with SBOM and provenance attestations.
`GHCR_OWNER` is the lower-case GitHub owner. If the packages are private, the host needs
`docker login ghcr.io` with a token that has `read:packages` only.

Both images run as UID 10001. In production, containers are read-only with a `/tmp` tmpfs,
`no-new-privileges` and all capabilities dropped.

## 3. Secrets and configuration

Copy `.env.production.example` to `.env.production`, `chmod 600`, and fill it in — or better,
render it from a secret manager (Vault, Scaleway/OVH secret manager, SOPS-encrypted file).
Nothing secret is in git or in the images.

| Secret | Notes |
|---|---|
| `POSTGRES_PASSWORD` | superuser; used only for bootstrap and backups |
| `REPINTEL_OWNER_DB_PASSWORD` | owner role (BYPASSRLS) — given **only** to the `migrate` service |
| `REPINTEL_APP_DB_PASSWORD` | application role (NOBYPASSRLS) — api, workers |
| `REDIS_PASSWORD` | rate limiting (fails closed without Redis) |
| `REPINTEL_JWT_SECRET` | ≥ 32 chars; rotating it logs everyone out |
| `REPINTEL_ENCRYPTION_KEYS` | Fernet keys for MFA seeds; prepend a new key to rotate, keep old ones until re-encrypted |
| `REPINTEL_BREVO_API_KEY`, `REPINTEL_SERPER_API_KEY`, `REPINTEL_OPENAI_API_KEY` | provider keys; never pasted anywhere else |
| `REPINTEL_METRICS_TOKEN` | bearer token for `/metrics` |
| `REPINTEL_SENTRY_DSN` | optional |

Model ids (`REPINTEL_NLP_MODELS`, `REPINTEL_NLP_ARBITER_MODEL`, `REPINTEL_AGENT_MODEL`),
prices and plan limits are configuration or database data, never code.

## 4. First start

```bash
sudo scripts/egress-firewall.sh        # see §6; install as a systemd unit (below)
docker compose -f docker-compose.prod.yml --env-file .env.production --profile scanner pull
docker compose -f docker-compose.prod.yml --env-file .env.production --profile scanner up -d
docker compose -f docker-compose.prod.yml logs -f migrate api
```

`--profile scanner` starts the Scanner web app (`scanner-web`) and its built-in browser
(`scanner-browser`); leave it out, with `REPINTEL_SCANNER_ENABLED=false`, to run AynPulse
alone. Caddy serves AynPulse on `REPINTEL_DOMAIN` and Scanner on `SCANNER_DOMAIN`, where only
`/api/v1/scanner/*` reaches the API and `/api/v1/scanner/browser/ws` goes to `scanner-browser`.

On an empty volume, `infra/postgres/prod-init.sh` creates the two roles with the passwords
from the environment and a locked-down database; `migrate` then runs `alembic upgrade head`
as the owner role. Caddy obtains the TLS certificate automatically.

Then:

1. Register your own account through the web app and enable two-step verification.
2. Make it a platform administrator — an operator action the application role cannot do:
   ```bash
   docker compose -f docker-compose.prod.yml exec postgres \
     psql -U postgres -d repintel -c "UPDATE users SET is_platform_admin = true WHERE email = 'you@example.dz'"
   ```
3. In **Administration → Plans**: set limits and prices of the tiers (created inactive and
   without prices), then activate and publish the ones you sell. The `trial` plan is the
   default for new organisations (`REPINTEL_DEFAULT_PLAN`, `REPINTEL_TRIAL_DAYS`).
4. In **Administration → Connectors** (production only): set the connectors you use to
   *approved*.
5. Set the seller identity (`REPINTEL_BILLING_SELLER_*`) — issuing an invoice is refused
   until the name and NIF are set.

### Upgrades

```bash
sed -i 's/^REPINTEL_VERSION=.*/REPINTEL_VERSION=0.9.1/' .env.production
scripts/backup.sh …                    # always back up first (§7)
docker compose -f docker-compose.prod.yml --env-file .env.production pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Migrations are reversible (CI runs upgrade → downgrade → upgrade), but a downgrade after
real data was written is a restore, not an `alembic downgrade`.

## 5. Network layout

| Network | Members | Internet |
|---|---|---|
| `edge` | caddy, web, scanner-web, api | inbound 80/443 via Caddy only |
| `data` | postgres, redis, migrate, api, workers | **none** (`internal: true`) |
| `egress` (`ri-egress`) | api, worker | outbound, private ranges blocked |
| `browser` (`ri-browser`) | browser-worker, scanner-browser | outbound, private ranges blocked |
| `scanner-edge` | caddy, scanner-browser | **none** (`internal: true`): the WebSocket only |

Only Caddy publishes ports. The api trusts `X-Forwarded-For` only from Caddy's fixed address
(`172.30.0.10`), so clients cannot spoof the IP used for rate limiting. `/metrics` is not
reachable through Caddy.

## 6. Egress firewall

`scripts/egress-firewall.sh` inserts a `DOCKER-USER` rule for the `ri-egress` and
`ri-browser` bridges that drops traffic to private, loopback, link-local (including the cloud
metadata address 169.254.169.254), CGNAT, benchmark and multicast ranges, and blocks IPv6
forwarding from them. The application already resolves and checks every address and
re-checks each redirect; the firewall is the layer DNS rebinding cannot get around.

Run it after Docker starts, e.g. `/etc/systemd/system/repintel-egress.service`:

```ini
[Unit]
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/srv/repintel/scripts/egress-firewall.sh
[Install]
WantedBy=multi-user.target
```

Check it from inside: `docker compose … exec browser-worker python -c "import urllib.request;urllib.request.urlopen('http://169.254.169.254', timeout=3)"` must time out.

## 7. Backups and restore

- **Logical backups** with `scripts/backup.sh` (pg_dump custom format with the server's own major version, verified with
  `pg_restore --list`, SHA-256 alongside, local pruning after `BACKUP_KEEP_DAYS`).
  Set `BACKUP_AGE_RECIPIENT` to encrypt with [age](https://age-encryption.org) before the
  file leaves the host, then copy it to EU object storage in another region/provider
  (e.g. `rclone copy`). Keep the age private key offline.
- Nightly, from cron on the host:
  ```cron
  15 2 * * * cd /srv/repintel && docker compose -f docker-compose.prod.yml exec -T postgres env PGUSER=postgres BACKUP_DIR=/backups BACKUP_AGE_RECIPIENT=age1… sh -s < scripts/backup.sh && rclone copy backups/ remote:repintel-backups
  ```
  (`age` must then be available in the postgres container, or encrypt on the host after the
  dump: `./backups` is mounted there.)
- Provider disk **snapshots** daily as a second layer; for an RPO under 24 h add WAL
  archiving (e.g. pgBackRest or WAL-G to object storage).
- **Restore** into an empty database with `scripts/restore.sh <file> <db>` (refuses a
  non-empty target). Roles must exist first (`prod-init.sh`). CI runs a full backup →
  restore round-trip on every push and compares tables, policies and data.
- **Test a restore monthly** on a scratch host, and keep the procedure time (RTO) noted.

## 8. Monitoring

- **Health**: `/healthz` (liveness), `/readyz` (database + Redis) — used by Compose
  healthchecks; point an external uptime check (EU) at `https://<domain>/readyz`.
- **Metrics**: `/metrics` in Prometheus text format, enabled by `REPINTEL_METRICS_TOKEN`
  and scraped with `Authorization: Bearer <token>` from the private network (not exposed by
  Caddy). Aggregates only, no tenant data: organisations, subscriptions by status, jobs by
  status and age of the oldest due job, browser workers online and capacity, missions by
  status, AI tasks running, approvals pending, outstanding and overdue invoices.
  Suggested alerts (Grafana / Alertmanager):
  - `repintel_jobs_oldest_pending_seconds > 600` — workers stuck or down;
  - `repintel_jobs{status="dead"}` increasing — failing jobs (see the admin console / logs);
  - `repintel_browser_workers_online == 0` while missions are queued;
  - `repintel_invoices_overdue > 0` — follow up with customers.
- **Logs** are JSON on stdout (rotated by Docker); ship them to an EU log store if needed.
  They carry request ids, never request bodies or secrets.
- **Errors**: optional Sentry (`REPINTEL_SENTRY_DSN`, prefer the EU data region). Request
  bodies and personal data are not sent.

## 9. Scheduled work

The `worker` runs schedulers itself (no cron needed): due monitoring runs, alert
evaluation, re-queueing of stale jobs, and the daily `platform.daily` job (trial expiry
with e-mails to owners, retention purge per plan capped by `REPINTEL_RETENTION_MAX_DAYS`,
pruning of usage events older than 400 days). Only one `platform.daily` job is created per
23 hours, so several workers can run safely. Browser workers recover lost missions among
themselves.

## 10. Scaling

- More API capacity: `API_WORKERS`, then more hosts behind a load balancer (update the
  trusted proxy address).
- More collection throughput: `docker compose … up -d --scale worker=N`.
- More browser capacity: `--scale browser-worker=N` and `BROWSER_MAX_CONTEXTS`; each plan's
  `max_concurrent_browser_missions` keeps one organisation from taking the whole fleet.
- Postgres: move to a managed EU PostgreSQL 16 with pgvector when the host gets tight; keep
  the two roles and run `prod-init.sh`'s SQL there.
