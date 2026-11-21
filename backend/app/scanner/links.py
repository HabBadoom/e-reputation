"""Scanner side of the links to AnyPulse organizations.

A Scanner account asks for a link (start); a member of an AnyPulse organization approves it in
AnyPulse; Scanner then sees the link (organization name only) and can list that
organization's brands through the signed integration client to choose a collection's
destination. Either side can revoke a link.
"""

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.core.security import new_refresh_token
from app.db.models import ScannerLink, ScannerLinkRequest
from app.integrations.anypulse.client import AnyPulseClient, AnyPulseRejected, AnyPulseUnavailable


class AnyPulseUnavailableError(AppError):
    status_code = 503
    code = "anypulse_unavailable"


def start(db: Session, account_id: uuid.UUID) -> tuple[ScannerLinkRequest, str]:
    """Create a link request; returns it and the AnyPulse URL where a member approves it."""
    s = get_settings()
    raw, hashed = new_refresh_token()   # 48 random bytes, stored as SHA-256
    req = ScannerLinkRequest(
        account_id=account_id, token_hash=hashed,
        expires_at=datetime.now(UTC) + timedelta(seconds=s.scanner_link_request_ttl_seconds))
    db.add(req)
    db.flush()
    url = f"{s.app_base_url.rstrip('/')}/connect/scanner?request={quote(raw)}"
    return req, url


def list_links(db: Session) -> list[ScannerLink]:
    return list(db.scalars(select(ScannerLink).order_by(ScannerLink.created_at.desc())))


def get(db: Session, link_id: uuid.UUID) -> ScannerLink:
    link = db.get(ScannerLink, link_id)   # RLS: own links only (and never another account's)
    if link is None:
        raise NotFound("Link not found", code="link_not_found")
    return link


def require_active(link: ScannerLink) -> ScannerLink:
    if link.status != "active":
        raise Conflict("This link to AnyPulse has been revoked", code="link_revoked")
    return link


def revoke(db: Session, link: ScannerLink) -> ScannerLink:
    require_active(link)
    link.status, link.revoked_by = "revoked", "scanner"
    return link


def brands(link: ScannerLink) -> list[dict]:
    """The linked organization's active brands, asked to AnyPulse through the signed client."""
    require_active(link)
    try:
        return AnyPulseClient(account_id=link.account_id, link_id=link.id).list_brands()
    except AnyPulseUnavailable as e:
        raise AnyPulseUnavailableError("AnyPulse is not reachable right now; try again") from e
    except AnyPulseRejected as e:
        if e.code == "link_revoked":
            raise Conflict("This link to AnyPulse has been revoked", code="link_revoked") from e
        raise AppError(f"AnyPulse refused the request: {e}", code="anypulse_rejected",
                       status_code=502) from e
