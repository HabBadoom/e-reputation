"""Scanner request authentication and context.

Same order of operations as AnyPulse (app/api/deps.py), with Scanner tokens and cookies:
  1. Read the Scanner access token (Bearer for API clients, `sc_access` cookie for browsers).
  2. Verify signature, expiry, type and audience → account id from the token only.
  3. Set the Scanner DB context (RLS now scopes every query to this account).
  4. Check that the refresh family is still alive and the account active (immediate logout
     and disabling).
  5. Double-submit CSRF for cookie-authenticated unsafe methods.
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, Unauthorized
from app.core.security import constant_time_eq, decode_scanner_access_token
from app.db.models import ScannerAccount, ScannerRefreshToken
from app.db.session import get_db, set_scanner_context

ACCESS_COOKIE = "sc_access"
REFRESH_COOKIE = "sc_refresh"
CSRF_COOKIE = "sc_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class ScannerContext:
    account_id: uuid.UUID
    session_id: uuid.UUID
    via_cookie: bool


def _extract_token(request: Request) -> tuple[str, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    cookie = request.cookies.get(ACCESS_COOKIE)
    if cookie:
        return cookie, True
    raise Unauthorized("Authentication required")


def get_scanner_auth(request: Request, db: Session = Depends(get_db)) -> ScannerContext:
    token, via_cookie = _extract_token(request)
    claims = decode_scanner_access_token(token)

    csrf_ok = constant_time_eq(request.headers.get(CSRF_HEADER), request.cookies.get(CSRF_COOKIE))
    if via_cookie and request.method not in SAFE_METHODS and not csrf_ok:
        raise Forbidden("CSRF token missing or invalid", code="csrf_failed")

    check_session(db, claims.account_id, claims.session_id)
    request.state.scanner_account_id = claims.account_id
    return ScannerContext(claims.account_id, claims.session_id, via_cookie)


def check_session(db: Session, account_id: uuid.UUID, family_id: uuid.UUID) -> None:
    """Set the Scanner context, then require a live refresh family and an active account
    (immediate effect of logout, reuse detection and disabling). Also used by the built-in
    browser service for its WebSocket connections."""
    set_scanner_context(db, account_id=account_id)
    alive = db.scalar(
        select(ScannerRefreshToken.id).where(
            ScannerRefreshToken.family_id == family_id,
            ScannerRefreshToken.revoked_at.is_(None),
            ScannerRefreshToken.expires_at > func.now()).limit(1)
    )
    if alive is None:
        raise Unauthorized("Session has ended", code="session_revoked")
    status = db.scalar(select(ScannerAccount.status).where(ScannerAccount.id == account_id))
    if status != "active":
        raise Unauthorized("Account disabled", code="account_disabled")
