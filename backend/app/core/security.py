"""Password hashing, token issuance and verification."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from app.core.config import get_settings
from app.core.errors import Unauthorized

# Argon2id, RFC 9106 "second recommended" profile: m=64 MiB, t=3, p=4.
_ARGON2 = {"length": 32, "iterations": 3, "lanes": 4, "memory_cost": 64 * 1024}


def hash_password(password: str) -> str:
    kdf = Argon2id(salt=secrets.token_bytes(16), **_ARGON2)
    return kdf.derive_phc_encoded(password.encode())


# Verified against when the email does not exist, so response time doesn't reveal it.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        Argon2id.verify_phc_encoded(password.encode(), password_hash or _DUMMY_HASH)
    except (InvalidKey, ValueError):
        return False
    return password_hash is not None


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    jti: str


def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "jti": secrets.token_hex(8),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=s.access_token_ttl_seconds)).timestamp()),
        "typ": "access",
    }
    token = jwt.encode(payload, s.jwt_secret.get_secret_value(), algorithm=s.jwt_algorithm)
    return token, s.access_token_ttl_seconds


def decode_access_token(token: str) -> AccessClaims:
    s = get_settings()
    try:
        p = jwt.decode(
            token,
            s.jwt_secret.get_secret_value(),
            algorithms=[s.jwt_algorithm],
            options={"require": ["sub", "tid", "exp", "jti"]},
        )
        if p.get("typ") != "access":
            raise Unauthorized("Invalid token type")
        return AccessClaims(uuid.UUID(p["sub"]), uuid.UUID(p["tid"]), p["jti"])
    except jwt.ExpiredSignatureError as e:
        raise Unauthorized("Access token expired", code="token_expired") from e
    except (jwt.PyJWTError, ValueError, KeyError) as e:
        raise Unauthorized("Invalid access token") from e


def new_refresh_token() -> tuple[str, str]:
    """Return (plaintext, sha256-hex). Only the hash is stored."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_eq(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())
