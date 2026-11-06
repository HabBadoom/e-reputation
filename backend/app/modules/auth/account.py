"""Account security: email verification, password reset/change, TOTP MFA, recovery codes.

All rows touched here are user-scoped and protected by RLS on app.user_id. Pre-context
steps (clicking an email link, answering the MFA challenge) find their token through the
narrow SECURITY DEFINER function `auth_find_user_token`, then switch the DB context to that
user before reading or writing anything else.
"""

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core import crypto, totp
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, Unauthorized
from app.core.security import hash_password, hash_token, verify_password
from app.db.models import Membership, MfaRecoveryCode, RefreshToken, User, UserMfa, UserToken
from app.db.session import set_context
from app.modules.audit.service import record
from app.modules.notifications.email import EmailDeliveryError, get_email_provider
from app.modules.notifications.templates import password_reset_email, verification_email

log = logging.getLogger("repintel.account")

RECOVERY_CODE_COUNT = 10
_RC_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i


# ---------------------------------------------------------------- single-use tokens
def issue_user_token(db: Session, *, user_id: uuid.UUID, purpose: str, ttl_seconds: int,
                     tenant_id: uuid.UUID | None = None) -> str:
    """Create a token and invalidate older unused ones of the same purpose."""
    now = datetime.now(UTC)
    db.execute(update(UserToken).where(UserToken.user_id == user_id,
                                       UserToken.purpose == purpose,
                                       UserToken.used_at.is_(None)).values(used_at=now))
    raw = secrets.token_urlsafe(32)
    db.add(UserToken(user_id=user_id, purpose=purpose, token_hash=hash_token(raw),
                     tenant_id=tenant_id, expires_at=now + timedelta(seconds=ttl_seconds)))
    db.flush()
    return raw


@dataclass(frozen=True)
class FoundToken:
    id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None


def find_valid_token(db: Session, raw: str, purpose: str) -> FoundToken:
    """Look up an unused, unexpired token and switch the DB context to its user.
    Every failure gives the same error so tokens can't be probed."""
    row = db.execute(
        text("SELECT id, user_id, tenant_id, expires_at, used_at "
             "FROM auth_find_user_token(:h, :p)"), {"h": hash_token(raw), "p": purpose},
    ).one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= datetime.now(UTC):
        raise NotFound("This link is invalid or has expired", code="invalid_token")
    set_context(db, tenant_id=row.tenant_id, user_id=row.user_id)
    return FoundToken(row.id, row.user_id, row.tenant_id)


def mark_used(db: Session, token_id: uuid.UUID) -> None:
    db.execute(update(UserToken).where(UserToken.id == token_id)
               .values(used_at=datetime.now(UTC)))


def _send(msg_factory, *args) -> bool:  # noqa: ANN001
    try:
        get_email_provider().send(msg_factory(*args))
        return True
    except EmailDeliveryError:
        return False  # already logged by the provider


def _record_everywhere(db: Session, user_id: uuid.UUID, action: str,
                       request: Request | None) -> None:
    """Write a user-level security event into the audit log of every org the user is in."""
    tenant_ids = list(db.scalars(select(Membership.tenant_id)
                                 .where(Membership.user_id == user_id)))
    for tid in tenant_ids:
        set_context(db, tenant_id=tid, user_id=user_id)
        record(db, tenant_id=tid, actor_user_id=user_id, action=action, target_type="user",
               target_id=user_id, request=request)
    set_context(db, tenant_id=None, user_id=user_id)


# ---------------------------------------------------------------- email verification
def send_verification(db: Session, user: User) -> bool:
    raw = issue_user_token(db, user_id=user.id, purpose="email_verify",
                           ttl_seconds=get_settings().email_verify_ttl_seconds)
    return _send(verification_email, user.email, user.full_name, user.locale, raw)


def verify_email(db: Session, raw: str) -> uuid.UUID:
    tok = find_valid_token(db, raw, "email_verify")
    mark_used(db, tok.id)
    db.execute(update(User).where(User.id == tok.user_id, User.email_verified_at.is_(None))
               .values(email_verified_at=datetime.now(UTC)))
    return tok.user_id


# ---------------------------------------------------------------- passwords
def _revoke_all_sessions(db: Session, user_id: uuid.UUID) -> None:
    db.execute(update(RefreshToken)
               .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=datetime.now(UTC)))


def request_password_reset(db: Session, email: str) -> None:
    """Silent: the caller always answers 202 whether or not the account exists."""
    row = db.execute(text("SELECT id, status FROM auth_find_user_by_email(:e)"),
                     {"e": email.lower()}).one_or_none()
    if row is None or row.status != "active":
        log.info("password_reset_unknown_or_disabled")
        return
    set_context(db, tenant_id=None, user_id=row.id)
    user = db.get(User, row.id)
    assert user is not None
    raw = issue_user_token(db, user_id=user.id, purpose="password_reset",
                           ttl_seconds=get_settings().password_reset_ttl_seconds)
    _send(password_reset_email, user.email, user.full_name, user.locale, raw)


def reset_password(db: Session, raw: str, new_password: str, request: Request) -> None:
    tok = find_valid_token(db, raw, "password_reset")
    user = db.get(User, tok.user_id)
    if user is None or user.status != "active":
        raise NotFound("This link is invalid or has expired", code="invalid_token")
    mark_used(db, tok.id)
    user.password_hash = hash_password(new_password)
    # Receiving the link proves control of the mailbox.
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    _revoke_all_sessions(db, user.id)
    db.execute(update(UserToken).where(UserToken.user_id == user.id,
                                       UserToken.purpose == "mfa_login",
                                       UserToken.used_at.is_(None))
               .values(used_at=datetime.now(UTC)))
    _record_everywhere(db, user.id, "auth.password_reset", request)


def change_password(db: Session, user_id: uuid.UUID, current: str, new: str) -> None:
    user = db.get(User, user_id)
    assert user is not None
    if not verify_password(current, user.password_hash):
        raise Unauthorized("Current password is incorrect", code="invalid_credentials")
    user.password_hash = hash_password(new)
    _revoke_all_sessions(db, user_id)


# ---------------------------------------------------------------- MFA
def _mfa_row(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> UserMfa | None:
    q = select(UserMfa).where(UserMfa.user_id == user_id)
    if lock:
        q = q.with_for_update()
    return db.scalars(q).one_or_none()


def mfa_enabled(db: Session, user_id: uuid.UUID) -> bool:
    row = _mfa_row(db, user_id)
    return bool(row and row.enabled_at)


def recovery_codes_remaining(db: Session, user_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(MfaRecoveryCode)
                     .where(MfaRecoveryCode.user_id == user_id,
                            MfaRecoveryCode.used_at.is_(None))) or 0


def mfa_setup(db: Session, user: User) -> tuple[str, str]:
    row = _mfa_row(db, user.id, lock=True)
    if row and row.enabled_at:
        raise Conflict("Two-factor authentication is already enabled", code="mfa_enabled")
    secret = totp.new_secret()
    if row is None:
        row = UserMfa(user_id=user.id)
        db.add(row)
    row.pending_secret_enc = crypto.encrypt(secret)
    row.updated_at = datetime.now(UTC)
    db.flush()
    return secret, totp.provisioning_uri(secret, user.email, get_settings().email_from_name)


def _rc_normalize(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def _rc_hash(code: str) -> str:
    return hashlib.sha256(_rc_normalize(code).encode()).hexdigest()


def _new_recovery_codes(db: Session, user_id: uuid.UUID) -> list[str]:
    db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user_id).delete()
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_RC_ALPHABET) for _ in range(10))
        code = f"{raw[:5]}-{raw[5:]}"
        codes.append(code)
        db.add(MfaRecoveryCode(user_id=user_id, code_hash=_rc_hash(code)))
    db.flush()
    return codes


def mfa_enable(db: Session, user_id: uuid.UUID, code: str) -> list[str]:
    row = _mfa_row(db, user_id, lock=True)
    if row is None or row.pending_secret_enc is None:
        raise Conflict("Start the two-factor setup first", code="mfa_not_started")
    if row.enabled_at:
        raise Conflict("Two-factor authentication is already enabled", code="mfa_enabled")
    secret = crypto.decrypt(row.pending_secret_enc)
    step = totp.verify(secret, code)
    if step is None:
        raise Unauthorized("Invalid code", code="invalid_mfa_code")
    row.secret_enc, row.pending_secret_enc = row.pending_secret_enc, None
    row.enabled_at = datetime.now(UTC)
    row.last_step = step
    row.updated_at = datetime.now(UTC)
    return _new_recovery_codes(db, user_id)


def mfa_check(db: Session, user_id: uuid.UUID, code: str) -> str | None:
    """Verify a TOTP code (replay-protected) or an unused recovery code.
    Returns "totp", "recovery" or None."""
    row = _mfa_row(db, user_id, lock=True)  # row lock serialises concurrent attempts
    if row is None or row.enabled_at is None or row.secret_enc is None:
        return None
    step = totp.verify(crypto.decrypt(row.secret_enc), code, after_step=row.last_step)
    if step is not None:
        row.last_step = step
        return "totp"
    if len(_rc_normalize(code)) == 10:
        rc = db.scalars(select(MfaRecoveryCode)
                        .where(MfaRecoveryCode.user_id == user_id,
                               MfaRecoveryCode.code_hash == _rc_hash(code),
                               MfaRecoveryCode.used_at.is_(None))
                        .with_for_update()).one_or_none()
        if rc is not None:
            rc.used_at = datetime.now(UTC)
            return "recovery"
    return None


def mfa_disable(db: Session, user_id: uuid.UUID, password: str, code: str) -> None:
    user = db.get(User, user_id)
    assert user is not None
    if not verify_password(password, user.password_hash):
        raise Unauthorized("Current password is incorrect", code="invalid_credentials")
    if mfa_check(db, user_id, code) is None:
        raise Unauthorized("Invalid code", code="invalid_mfa_code")
    db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user_id).delete()
    db.query(UserMfa).filter(UserMfa.user_id == user_id).delete()


def mfa_regenerate_codes(db: Session, user_id: uuid.UUID, code: str) -> list[str]:
    if mfa_check(db, user_id, code) is None:
        raise Unauthorized("Invalid code", code="invalid_mfa_code")
    return _new_recovery_codes(db, user_id)
