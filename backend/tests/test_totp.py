"""RFC 6238 Appendix B test vectors (SHA1 seed "12345678901234567890").

The RFC lists 8-digit codes; the 6-digit code is the last 6 digits (same truncation).
"""

import base64

import pytest

from app.core import totp

SEED = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize("t,expected8", [
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130"),
])
def test_rfc6238_vectors(t, expected8):
    key = base64.b32decode(SEED + "=" * (-len(SEED) % 8))
    assert totp.hotp(key, totp.current_step(t), digits=8) == expected8
    assert totp.code_at(SEED, now=t) == expected8[-6:]


def test_verify_window_and_replay():
    t = 1_700_000_000
    code = totp.code_at(SEED, now=t)
    step = totp.verify(SEED, code, now=t)
    assert step == totp.current_step(t)
    assert totp.verify(SEED, code, now=t + 30) == step          # one step of drift is fine
    assert totp.verify(SEED, code, now=t + 90) is None          # too old
    assert totp.verify(SEED, code, now=t, after_step=step) is None  # replay rejected
    assert totp.verify(SEED, "12345", now=t) is None
    assert totp.verify(SEED, "abcdef", now=t) is None


def test_new_secret_and_uri():
    s = totp.new_secret()
    assert len(s) == 32 and s.isalnum()
    uri = totp.provisioning_uri(s, "amine@example.dz", "RepIntel")
    assert uri.startswith("otpauth://totp/RepIntel%3Aamine%40example.dz?")
    assert f"secret={s}" in uri and "issuer=RepIntel" in uri
