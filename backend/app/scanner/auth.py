"""Scanner account flows: register, sign in, refresh rotation, sign out.

Scanner accounts are separate from AnyPulse users (decision of 2026-11-21). The flows mirror
AnyPulse's own (app/modules/auth/service.py): Argon2id, short-lived access JWT, opaque refresh
token stored hashed and rotated on every use, reuse of a rotated token revokes the family.
Pre-context lookups go through the narrow SECURITY DEFINER functions of migration 0010.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, Unauthorized
from app.core.security import (
    create_scanner_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    verify_password,
)
from app.db.models import ScannerAccount, ScannerRefreshToken
from app.db.session import set_scanner_context

log = logging.getLogger("repintel.scanner.auth")


@dataclass
class IssuedTokens:
    access_token: str
    access_ttl: int
    refresh_token: str
    refresh_ttl: int
    account_id: uuid.UUID


def issue_tokens(db: Session, *, account_id: uuid.UUID, family_id: uuid.UUID | None = None,
                 user_agent: str | None = None, ip: str | None = None) -> IssuedTokens:
    s = get_settings()
    family_id = family_id or uuid.uuid4()
    access, ttl = create_scanner_access_token(account_id, family_id)
    raw, hashed = new_refresh_token()
    db.add(ScannerRefreshToken(
        account_id=account_id, token_hash=hashed, family_id=family_id,
        expires_at=datetime.now(UTC) + timedelta(seconds=s.refresh_token_ttl_seconds),
        user_agent=(user_agent or "")[:400] or None, ip=ip,
    ))
    return IssuedTokens(access, ttl, raw, s.refresh_token_ttl_seconds, account_id)


def register(db: Session, *, email: str, password: str, full_name: str, locale: str
             ) -> uuid.UUID:
    account_id = uuid.uuid4()
    set_scanner_context(db, account_id=account_id)
    try:
        db.add(ScannerAccount(
            id=account_id, email=email.lower(), password_hash=hash_password(password),
            full_name=full_name, locale=locale,
        ))
        db.flush()
    except IntegrityError as e:
        db.rollback()
        # Same trade-off as AnyPulse registration: reveals that the email is taken; the
        # endpoint is rate-limited.
        raise Conflict("An account with this email already exists", code="email_taken") from e
    return account_id


def authenticate(db: Session, *, email: str, password: str) -> uuid.UUID:
    row = db.execute(
        text("SELECT id, password_hash, status FROM scanner_auth_find_account_by_email(:e)"),
        {"e": email.lower()},
    ).one_or_none()
    ok = verify_password(password, row.password_hash if row else None)
    if not row or not ok or row.status != "active":
        log.info("scanner_login_failed", extra={"reason": "bad_credentials_or_disabled"})
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    set_scanner_context(db, account_id=row.id)
    db.execute(update(ScannerAccount).where(ScannerAccount.id == row.id)
               .values(last_login_at=datetime.now(UTC)))
    return row.id


def rotate_refresh(db: Session, raw: str, *, user_agent: str | None, ip: str | None
                   ) -> IssuedTokens:
    row = db.execute(
        text("SELECT id, account_id, family_id, expires_at, revoked_at "
             "FROM scanner_auth_find_refresh_token(:h)"), {"h": hash_token(raw)},
    ).one_or_none()
    if row is None:
        raise Unauthorized("Invalid refresh token", code="invalid_refresh")
    set_scanner_context(db, account_id=row.account_id)
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        db.execute(update(ScannerRefreshToken)
                   .where(ScannerRefreshToken.family_id == row.family_id,
                          ScannerRefreshToken.revoked_at.is_(None))
                   .values(revoked_at=now))
        db.commit()
        log.warning("scanner_refresh_reuse_detected", extra={"family_id": str(row.family_id)})
        raise Unauthorized("Refresh token reuse detected; please sign in again",
                           code="refresh_reuse")
    if row.expires_at <= now:
        raise Unauthorized("Refresh token expired", code="refresh_expired")
    account = db.get(ScannerAccount, row.account_id)
    if account is None or account.status != "active":
        raise Unauthorized("Account disabled", code="account_disabled")
    db.execute(update(ScannerRefreshToken).where(ScannerRefreshToken.id == row.id)
               .values(revoked_at=now))
    return issue_tokens(db, account_id=row.account_id, family_id=row.family_id,
                        user_agent=user_agent, ip=ip)


def revoke_refresh(db: Session, raw: str) -> None:
    row = db.execute(
        text("SELECT id, account_id, family_id FROM scanner_auth_find_refresh_token(:h)"),
        {"h": hash_token(raw)},
    ).one_or_none()
    if row is None:
        return
    set_scanner_context(db, account_id=row.account_id)
    db.execute(update(ScannerRefreshToken)
               .where(ScannerRefreshToken.family_id == row.family_id,
                      ScannerRefreshToken.revoked_at.is_(None))
               .values(revoked_at=datetime.now(UTC)))
