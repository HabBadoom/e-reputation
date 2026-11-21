"""HMAC request signing for Scanner → AnyPulse calls.

Each request carries the Scanner account, the link it acts through, a Unix timestamp and an
HMAC-SHA256 over a canonical string (method, path with query, timestamp, account, link and the
SHA-256 of the body). AnyPulse recomputes it with the shared key, rejects stale timestamps,
then checks in the database that the link is active and belongs to that account.
"""

import hashlib
import hmac
import time

HEADER_ACCOUNT = "x-scanner-account"
HEADER_LINK = "x-scanner-link"
HEADER_TIMESTAMP = "x-scanner-timestamp"
HEADER_SIGNATURE = "x-scanner-signature"
VERSION = "v1"
MAX_SKEW_SECONDS = 300


def canonical(*, method: str, path: str, timestamp: str, account_id: str, link_id: str,
              body: bytes) -> bytes:
    return "\n".join([VERSION, method.upper(), path, timestamp, account_id, link_id,
                      hashlib.sha256(body).hexdigest()]).encode()


def sign(key: bytes, **parts: str | bytes) -> str:
    mac = hmac.new(key, canonical(**parts), hashlib.sha256).hexdigest()  # type: ignore[arg-type]
    return f"{VERSION}={mac}"


def signed_headers(key: bytes, *, method: str, path: str, account_id: str, link_id: str,
                   body: bytes, now: float | None = None) -> dict[str, str]:
    ts = str(int(now if now is not None else time.time()))
    return {
        HEADER_ACCOUNT: account_id,
        HEADER_LINK: link_id,
        HEADER_TIMESTAMP: ts,
        HEADER_SIGNATURE: sign(key, method=method, path=path, timestamp=ts,
                               account_id=account_id, link_id=link_id, body=body),
    }


def verify(key: bytes, signature: str, *, method: str, path: str, timestamp: str,
           account_id: str, link_id: str, body: bytes, now: float | None = None) -> bool:
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW_SECONDS:
        return False
    expected = sign(key, method=method, path=path, timestamp=timestamp, account_id=account_id,
                    link_id=link_id, body=body)
    return hmac.compare_digest(expected.encode(), (signature or "").encode())
