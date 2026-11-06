"""Encryption at rest for small secrets (e.g. MFA seeds). Fernet = AES-128-CBC + HMAC-SHA256.

Supports key rotation: REPINTEL_ENCRYPTION_KEYS="new,old" encrypts with `new` and still
decrypts values written with `old`.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


@lru_cache
def _box() -> MultiFernet:
    raw = get_settings().encryption_keys.get_secret_value()
    return MultiFernet([Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()])


def encrypt(plaintext: str) -> str:
    return _box().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _box().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Unable to decrypt value (wrong or rotated-out key)") from e
