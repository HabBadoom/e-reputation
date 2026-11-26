#!/usr/bin/env bash
# Creates .env.production from .env.production.example with every generated secret filled in
# (database and Redis passwords, JWT secret, Fernet key). Provider keys (Brevo, OpenAI,
# Serper…) are left for you to paste. Refuses to overwrite an existing file.
#
#   scripts/init-env.sh <app domain> <scanner domain> <version> [staging|production]
#   e.g. scripts/init-env.sh preprod.aynpulse.com scanner-preprod.aynpulse.com 0.11.0 staging
set -euo pipefail
cd "$(dirname "$0")/.."

[ $# -ge 3 ] || { sed -n '6,7p' "$0"; exit 2; }
APP_DOMAIN=$1 SCANNER_DOMAIN=$2 VERSION=${3#v} ENVIRONMENT=${4:-production}
case "$ENVIRONMENT" in staging|production) ;; *) echo "environment: staging or production"; exit 2;; esac
[ -e .env.production ] && { echo ".env.production already exists: not touched"; exit 1; }

umask 077
python3 - "$APP_DOMAIN" "$SCANNER_DOMAIN" "$VERSION" "$ENVIRONMENT" <<'PY'
import base64, os, re, secrets, sys

app, scanner, version, env = sys.argv[1:]
token = lambda: secrets.token_urlsafe(48)
password = lambda: secrets.token_hex(24)   # no characters that need quoting in URLs
values = {
    "GHCR_OWNER": "habbadoom",
    "REPINTEL_VERSION": version,
    "REPINTEL_ENV": env,
    "REPINTEL_DOMAIN": app,
    "SCANNER_DOMAIN": scanner,
    "POSTGRES_PASSWORD": password(),
    "REPINTEL_OWNER_DB_PASSWORD": password(),
    "REPINTEL_APP_DB_PASSWORD": password(),
    "REDIS_PASSWORD": password(),
    "REPINTEL_JWT_SECRET": token(),
    "REPINTEL_ENCRYPTION_KEYS": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "REPINTEL_METRICS_TOKEN": token(),
    "REPINTEL_EMAIL_FROM_ADDRESS": "no-reply@" + app.split(".", 1)[-1] if "." in app else "",
    "REPINTEL_CRAWLER_USER_AGENT": f"AynPulseBot/1.0 (+https://{app}/bot)",
    "REPINTEL_SCANNER_ENABLED": "true",
    "REPINTEL_STORAGE_ENDPOINT_URL": "https://s3.fr-par.scw.cloud",
    "REPINTEL_STORAGE_REGION": "fr-par",
    "REPINTEL_SCANNER_BROWSER_ENABLED": "true",
}
out = []
for line in open(".env.production.example", encoding="utf-8"):
    m = re.match(r"^([A-Z0-9_]+)=", line)
    if m and m.group(1) in values:
        line = f"{m.group(1)}={values.pop(m.group(1))}\n"
    out.append(line)
for k, v in values.items():   # keys absent from an older example file
    out.append(f"{k}={v}\n")
with open(".env.production", "w", encoding="utf-8") as f:
    f.writelines(out)
PY
chmod 600 .env.production
echo ".env.production created (mode 600). Still to fill in:"
grep -nE '^(REPINTEL_BREVO_API_KEY|REPINTEL_OPENAI_API_KEY|REPINTEL_SERPER_API_KEY|REPINTEL_SCANNER_EXTRACTION_MODEL|REPINTEL_NLP_MODELS|REPINTEL_NLP_ARBITER_MODEL|REPINTEL_STORAGE_BUCKET|REPINTEL_STORAGE_ACCESS_KEY|REPINTEL_STORAGE_SECRET_KEY|ACME_EMAIL|REPINTEL_PLATFORM_NOTIFY_EMAILS)=' .env.production
