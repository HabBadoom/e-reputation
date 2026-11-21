"""Scanner side of the AnyPulse link.

Every Scanner account is linked automatically to its own AnyPulse organization: provisioning
runs right after sign-up and, if AnyPulse was unreachable then, again the next time the account
looks at its AnyPulse status. Only platform administrators revoke the access. Scanner users
are collection agents: they never read AnyPulse data. Everything Scanner asks of AnyPulse goes
through the signed integration client.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import ScannerLink
from app.integrations.anypulse.client import AnyPulseClient, AnyPulseRejected, AnyPulseUnavailable

log = logging.getLogger("repintel.scanner.anypulse")


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
