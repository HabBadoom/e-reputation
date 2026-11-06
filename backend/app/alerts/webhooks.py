"""Signed webhook delivery.

Each call carries:
  X-RepIntel-Event: alert.created
  X-RepIntel-Delivery: <alert id>           (idempotency key for the receiver)
  X-RepIntel-Timestamp: <unix seconds>
  X-RepIntel-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>
Receivers should recompute the signature with their secret, compare in constant time and
reject timestamps older than a few minutes (replay protection). See docs/API_DOCUMENTATION.md.
"""

import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from app.collection.fetcher import FetchError, safe_post
from app.core.config import get_settings
from app.core.crypto import decrypt
from app.db.models import Alert, Webhook


class WebhookDeliveryError(Exception):
    pass


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def alert_payload(alert: Alert, event: str = "alert.created") -> dict[str, Any]:
    return {
        "event": event,
        "id": str(alert.id),
        "tenant_id": str(alert.tenant_id),
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "alert": {
            "kind": alert.kind, "severity": alert.severity, "title": alert.title,
            "body": alert.body, "brand_id": str(alert.brand_id) if alert.brand_id else None,
            "status": alert.status, "data": alert.data,
            "url": f"{get_settings().app_base_url.rstrip('/')}/alerts",
        },
    }


def post(hook: Webhook, payload: dict[str, Any], delivery_id: uuid.UUID | str,
         event: str = "alert.created") -> int:
    """Send one signed call. Returns the HTTP status; raises WebhookDeliveryError on failure
    (network error, blocked address, redirect or non-2xx)."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ts = int(time.time())
    headers = {
        "content-type": "application/json",
        "x-repintel-event": event,
        "x-repintel-delivery": str(delivery_id),
        "x-repintel-timestamp": str(ts),
        "x-repintel-signature": sign(decrypt(hook.secret_enc), ts, body),
    }
    try:
        r = safe_post(hook.url, body, headers)
    except FetchError as e:
        hook.last_status, hook.last_error = None, e.reason
        hook.last_delivery_at = datetime.now(UTC)
        raise WebhookDeliveryError(e.reason) from e
    hook.last_status, hook.last_delivery_at = r.status, datetime.now(UTC)
    if not 200 <= r.status < 300:
        hook.last_error = f"HTTP {r.status}"
        raise WebhookDeliveryError(f"HTTP {r.status}")
    hook.last_error = None
    return r.status
