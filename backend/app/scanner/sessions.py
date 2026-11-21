"""Collection sessions: the user's successive additions, grouped until sent to AnyPulse.

Each account has at most one *open* session (draft / collecting / ready), enforced by a partial
unique index. `current()` returns it, or creates one, so the interface can always come back
to the active collection without losing it. Counters are collection metrics only (how many
screenshots, URLs, items to review) — never analytical figures.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.db.models import ScannerCollectionItem, ScannerCollectionSession

OPEN_STATUSES = ("draft", "collecting", "ready")


@dataclass(frozen=True)
class Counters:
    items: int = 0
    screenshots: int = 0
    urls: int = 0
    imports: int = 0
    needs_review: int = 0
    ready: int = 0
    failed: int = 0


def _open_session(db: Session, account_id: uuid.UUID) -> ScannerCollectionSession | None:
    return db.scalars(select(ScannerCollectionSession).where(
        ScannerCollectionSession.account_id == account_id,
        ScannerCollectionSession.status.in_(OPEN_STATUSES))).one_or_none()


def current(db: Session, account_id: uuid.UUID) -> tuple[ScannerCollectionSession, bool]:
    """The open session, created if there is none. Returns (session, created)."""
    found = _open_session(db, account_id)
    if found is not None:
        return found, False
    sp = db.begin_nested()
    try:
        s = ScannerCollectionSession(account_id=account_id, status="draft")
        db.add(s)
        db.flush()
        sp.commit()
        return s, True
    except IntegrityError:
        # Another request (second tab) created it concurrently: use that one.
        sp.rollback()
        found = _open_session(db, account_id)
        assert found is not None
        return found, False


def get(db: Session, session_id: uuid.UUID) -> ScannerCollectionSession:
    s = db.get(ScannerCollectionSession, session_id)  # RLS: other accounts' rows are invisible
    if s is None:
        raise NotFound("Collection session not found")
    return s


def list_sessions(db: Session, *, limit: int, offset: int
                  ) -> tuple[list[ScannerCollectionSession], int]:
    total = db.scalar(select(func.count()).select_from(ScannerCollectionSession)) or 0
    rows = db.scalars(select(ScannerCollectionSession)
                      .order_by(ScannerCollectionSession.created_at.desc())
                      .limit(limit).offset(offset)).all()
    return list(rows), total


def rename(db: Session, s: ScannerCollectionSession, title: str) -> ScannerCollectionSession:
    s.title = title
    s.updated_at = datetime.now(UTC)
    return s


def cancel(db: Session, s: ScannerCollectionSession) -> ScannerCollectionSession:
    if s.status not in OPEN_STATUSES:
        raise Conflict("Only an open collection can be cancelled", code="session_not_open")
    s.status = "cancelled"
    s.updated_at = datetime.now(UTC)
    return s


def counters(db: Session, session_ids: list[uuid.UUID]) -> dict[uuid.UUID, Counters]:
    if not session_ids:
        return {}
    i = ScannerCollectionItem

    def n(cond):  # noqa: ANN001, ANN202
        return func.count(case((cond, 1)))

    rows = db.execute(
        select(i.session_id, func.count(), n(i.source_type == "screenshot"),
               n(i.source_type == "url"), n(i.source_type == "import"),
               n(i.status == "needs_review"), n(i.status == "ready"), n(i.status == "failed"))
        .where(i.session_id.in_(session_ids)).group_by(i.session_id)
    ).all()
    out = {sid: Counters() for sid in session_ids}
    for r in rows:
        out[r[0]] = Counters(*r[1:])
    return out


def set_destination(db: Session, s: ScannerCollectionSession, *, link_id: uuid.UUID | None,
                    brand_id: uuid.UUID | None) -> ScannerCollectionSession:
    """Point an open collection at an AnyPulse organization (link) and brand.

    The brand is checked against the organization's current brands through the signed
    integration client, and again by a database trigger (scanner_sessions_destination_check).
    """
    from app.scanner import links  # local import: links imports the integration client

    if s.status not in OPEN_STATUSES:
        raise Conflict("Only an open collection can change destination", code="session_not_open")
    if link_id is None:
        if brand_id is not None:
            raise Conflict("Choose an AnyPulse organization before a brand",
                           code="invalid_destination")
        s.link_id = s.anypulse_brand_id = s.anypulse_brand_name = None
    else:
        link = links.require_active(links.get(db, link_id))
        name = None
        if brand_id is not None:
            match = [b for b in links.brands(link) if b["id"] == str(brand_id)]
            if not match:
                raise Conflict("This brand is not an active brand of the linked organization",
                               code="invalid_destination")
            name = match[0]["name"]
        s.link_id, s.anypulse_brand_id, s.anypulse_brand_name = link.id, brand_id, name
    s.updated_at = datetime.now(UTC)
    db.flush()
    return s
