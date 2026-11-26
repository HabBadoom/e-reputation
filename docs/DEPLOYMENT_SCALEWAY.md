# Preproduction on Scaleway

Decisions of 2026-11-26: product name **AynPulse**, domain **aynpulse.com**, host
**Scaleway** (Paris). The preproduction server runs the release images with
`REPINTEL_ENV=staging`; it becomes the template for production (`app.aynpulse.com`,
`scanner.aynpulse.com`, `REPINTEL_ENV=production`). General rules are in
[DEPLOYMENT.md](DEPLOYMENT.md).

| | Preproduction |
|---|---|
| AynPulse | `https://preprod.aynpulse.com` |
| Scanner | `https://scanner-preprod.aynpulse.com` |
| Instance | POP2-4C-16G (4 dedicated vCPU, 16 GB), Paris, Ubuntu 24.04, 100 GB block volume |
| Object storage | private bucket `aynpulse-preprod-scanner`, region `fr-par`, endpoint `https://s3.fr-par.scw.cloud` |
| E-mail | Brevo, sender `no-reply@aynpulse.com` (domain authenticated in Brevo) |

## 1. Scaleway

1. Project `aynpulse-preprod`.
2. **Security group**: inbound policy *drop*; allow TCP 22 from your own address only, TCP 80,
   TCP 443 and UDP 443 from anywhere. Outbound: accept.
3. **Instance** POP2-4C-16G in PAR, image Ubuntu 24.04, block volume 100 GB, a flexible
   public IPv4, your SSH public key, the security group above. Enable daily snapshots.
4. **Bucket** `aynpulse-preprod-scanner` in Paris, private (the default). The application
   never makes objects public; screenshots are served through the API.
5. **IAM**: an application `aynpulse-preprod-storage` with a policy granting
   `ObjectStorageFullAccess` on the project only, and an API key for it (access key +
   secret key → `REPINTEL_STORAGE_ACCESS_KEY` / `REPINTEL_STORAGE_SECRET_KEY`).

## 2. DNS (at the registrar of aynpulse.com)

| Type | Name | Value |
|---|---|---|
| A | `preprod` | public IPv4 of the instance |
| A | `scanner-preprod` | public IPv4 of the instance |

Plus the records Brevo asks for to authenticate `aynpulse.com` (code TXT, DKIM, DMARC).

## 3. Server

```bash
ssh root@<IP>
apt update && apt -y full-upgrade && apt -y install unattended-upgrades git
curl -fsSL https://get.docker.com | sh
git clone https://github.com/HabBadoom/e-reputation.git /srv/repintel
cd /srv/repintel && git checkout v0.11.0
scripts/init-env.sh preprod.aynpulse.com scanner-preprod.aynpulse.com 0.11.0 staging
nano .env.production      # provider keys, bucket, ACME_EMAIL, models (listed by the script)
```

Egress firewall as a systemd unit (see DEPLOYMENT.md §6; the script also allows DNS to the
host's resolvers, which are private addresses on Scaleway):

```bash
cat > /etc/systemd/system/repintel-egress.service <<'EOF'
[Unit]
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/srv/repintel/scripts/egress-firewall.sh
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now repintel-egress
```

Start:

```bash
C="docker compose -f docker-compose.prod.yml --env-file .env.production --profile scanner"
$C pull && $C up -d
$C logs -f migrate api     # Ctrl+C once the API answers
```

If `pull` is refused, the GHCR packages are private: `docker login ghcr.io -u HabBadoom` with a
classic token that has only `read:packages`.

## 4. First use

1. Create your account on `https://preprod.aynpulse.com/register`, enable two-step
   verification, then make it a platform administrator:
   ```bash
   $C exec postgres psql -U postgres -d repintel \
     -c "UPDATE users SET is_platform_admin = true WHERE email = 'you@…'"
   ```
2. Create a Scanner account on `https://scanner-preprod.aynpulse.com`.

## 5. Checks

- `https://preprod.aynpulse.com/readyz` → `{"ready": true, …}`;
  `https://scanner-preprod.aynpulse.com/readyz` → 404 (only the Scanner API is exposed there).
- `https://preprod.aynpulse.com/bot` shows the crawler page named in the user agent.
- Scanner: open the built-in browser, sign in to the dedicated Facebook account, capture a
  post, check that the text is extracted, validate, send; the mention appears in AynPulse.
- Egress: `$C exec worker python -c "import socket;print(socket.gethostbyname('api.openai.com'))"`
  answers; `$C exec browser-worker python -c "import urllib.request;urllib.request.urlopen('http://169.254.169.254', timeout=3)"`
  times out.

## 6. Upgrades

```bash
cd /srv/repintel && git fetch --tags && git checkout vX.Y.Z
sed -i 's/^REPINTEL_VERSION=.*/REPINTEL_VERSION=X.Y.Z/' .env.production
$C pull && $C up -d
```
