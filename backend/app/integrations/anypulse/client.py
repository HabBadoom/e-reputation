"""Signed HTTP client Scanner uses to talk to AnyPulse on behalf of one link."""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.anypulse.signing import signed_headers

log = logging.getLogger("repintel.scanner.integration")

# Tests replace this with a factory returning an in-process client (e.g. Starlette TestClient).
client_factory: Callable[[], httpx.Client] | None = None


class AnyPulseUnavailable(Exception):
    """AnyPulse could not be reached or failed (network error, timeout, 5xx)."""


class AnyPulseRejected(Exception):
    """AnyPulse refused the call (4xx): revoked link, bad signature, unknown resource."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


class AnyPulseClient:
    """Calls made on behalf of one Scanner account (and its link, once it exists)."""

    def __init__(self, *, account_id: uuid.UUID, link_id: uuid.UUID | None = None):
        self.account_id = str(account_id)
        self.link_id = str(link_id) if link_id else ""

    def _request(self, method: str, path: str, payload: Any | None = None) -> Any:
        s = get_settings()
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = signed_headers(s.scanner_integration_key, method=method, path=path,
                                 account_id=self.account_id, link_id=self.link_id, body=body)
        if body:
            headers["content-type"] = "application/json"
        owned = client_factory is None
        http = httpx.Client(base_url=s.anypulse_api_url, timeout=10.0) if owned \
            else client_factory()  # type: ignore[misc]
        try:
            r = http.request(method, path, content=body or None, headers=headers)
        except httpx.HTTPError as e:
            log.warning("anypulse_unreachable", extra={"error": type(e).__name__})
            raise AnyPulseUnavailable("AnyPulse is unreachable") from e
        finally:
            if owned:
                http.close()
        if r.status_code >= 500:
            log.warning("anypulse_error", extra={"status": r.status_code})
            raise AnyPulseUnavailable(f"AnyPulse answered {r.status_code}")
        data = r.json() if r.content else None
        if r.status_code >= 400:
            err = (data or {}).get("error", {}) if isinstance(data, dict) else {}
            raise AnyPulseRejected(r.status_code, err.get("code", "rejected"),
                                   err.get("message", "Refused by AnyPulse"))
        return data

    def provision(self) -> dict:
        """Create (once) the AnyPulse organization of this Scanner account and link it."""
        return self._request("POST", "/api/v1/integrations/scanner/provision")
