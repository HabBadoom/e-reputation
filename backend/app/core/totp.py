"""TOTP (RFC 6238) with HMAC-SHA1, 6 digits, 30 s steps — what authenticator apps
(Google Authenticator, Microsoft Authenticator, Aegis, 1Password…) expect.

Standard library only. `verify` returns the matched time step so the caller can reject
replays (a step <= the last accepted one).
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

DIGITS = 6
PERIOD = 30
WINDOW = 1  # accept one step before/after for clock drift


def new_secret() -> str:
    """160-bit random secret, base32 without padding (RFC 4226 recommends >= 128 bits)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    s = secret_b32.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10**digits).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // PERIOD)


def code_at(secret_b32: str, now: float | None = None) -> str:
    return hotp(_key(secret_b32), current_step(now))


def verify(secret_b32: str, code: str, *, now: float | None = None,
           after_step: int = 0) -> int | None:
    """Return the matching time step, or None. Steps <= after_step are rejected (replay)."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    key = _key(secret_b32)
    step = current_step(now)
    for s in range(step - WINDOW, step + WINDOW + 1):
        if s > after_step and hmac.compare_digest(hotp(key, s), code):
            return s
    return None


def provisioning_uri(secret_b32: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    params = urlencode({"secret": secret_b32, "issuer": issuer, "algorithm": "SHA1",
                        "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{params}"
