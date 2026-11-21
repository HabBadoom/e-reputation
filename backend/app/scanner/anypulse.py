"""Scanner side of the AnyPulse link.

Every Scanner account is linked automatically to its own AnyPulse organization: provisioning
runs right after sign-up and, if AnyPulse was unreachable then, again the next time the account
looks at its AnyPulse status. Only platform administrators revoke the access. Everything
Scanner asks of AnyPulse goes through the signed integration client.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import ScannerLink
from app.integrations.anypulse.client import AnyPulseClient, AnyPulseRejected, AnyPulseUnavailable

log = logging.getLogger("repintel.scanner.anypulse")


class AnyPulseUnavailableError(AppError):
    status_code = 503
    code = "anypulse_unavailable"


class AccessRevoked(AppError):
    status_code = 403
    code = "access_revoked"


class NotLinkedYet(AppError):
    status_code = 409
    code = "anypulse_pending"


def own_link(db: Session) -> ScannerLink | None:
    return db.scalars(select(ScannerLink)).one_or_none()   # RLS: this account's link only


def try_provision(account_id: uuid.UUID) -> bool:
    """Ask AnyPulse to create the account's organization (idempotent). Never raises."""
    try:
        AnyPulseClient(account_id=account_id).provision()
        return True
    except (AnyPulseUnavailable, AnyPulseRejected) as e:
        log.warning("scanner_provision_failed", extra={"error": type(e).__name__})
        return False


def ensure_link(db: Session, account_id: uuid.UUID) -> ScannerLink | None:
    """The account's link, provisioning it first if it does not exist yet."""
    link = own_link(db)
    if link is None and try_provision(account_id):
        link = own_link(db)   # committed by AnyPulse in its own transaction
    return link


def active_link(db: Session, account_id: uuid.UUID) -> ScannerLink:
    link = ensure_link(db, account_id)
    if link is None:
        raise NotLinkedYet("Your AnyPulse space is being prepared; try again in a moment")
    if link.status != "active":
        raise AccessRevoked("Access to AnyPulse has been revoked by an administrator")
    return link


def _call(fn):  # noqa: ANN001, ANN202
    try:
        return fn()
    except AnyPulseUnavailable as e:
        raise AnyPulseUnavailableError("AnyPulse is not reachable right now; try again") from e
    except AnyPulseRejected as e:
        # Pass AnyPulse's own answer through (plan limit, duplicate, revoked access…).
        raise AppError(str(e), code=e.code, status_code=e.status) from e


def brands(link: ScannerLink) -> list[dict]:
    return _call(AnyPulseClient(account_id=link.account_id, link_id=link.id).list_brands)


def create_brand(link: ScannerLink, name: str) -> dict:
    return _call(lambda: AnyPulseClient(account_id=link.account_id,
                                        link_id=link.id).create_brand(name))


def resend_invitation(link: ScannerLink) -> dict:
    return _call(AnyPulseClient(account_id=link.account_id, link_id=link.id).resend_invitation)
