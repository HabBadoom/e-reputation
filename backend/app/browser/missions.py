"""Browser Mission Orchestrator: creation, validation and state changes.

The state machine is enforced by a database trigger (migration 0007); TRANSITIONS mirrors it
for readable code and a test checks both stay identical.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.browser.guard import authorize, domain_policy
from app.collection.robots import RobotsCache
from app.collection.urls import domain_of
from app.db.models import BrowserMission, BrowserSessionEvent

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "CREATED": ("VALIDATING", "CANCELLED"),
    "VALIDATING": ("QUEUED", "FAILED", "CANCELLED"),
    "QUEUED": ("ASSIGNED", "CANCELLED", "EXPIRED"),
    "ASSIGNED": ("RUNNING", "QUEUED", "FAILED", "CANCELLED"),
    "RUNNING": ("COMPLETED", "FAILED", "PAUSED", "QUEUED", "CANCELLED"),
    "PAUSED": ("QUEUED", "WAITING_FOR_HUMAN", "CANCELLED", "EXPIRED"),
    "WAITING_FOR_HUMAN": ("QUEUED", "CANCELLED", "EXPIRED"),
}
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED", "EXPIRED"})
ACTIVE = frozenset({"CREATED", "VALIDATING", "QUEUED", "ASSIGNED", "RUNNING", "PAUSED",
                    "WAITING_FOR_HUMAN"})


class IllegalTransition(Exception):
    pass


def transition(m: BrowserMission, to: str, *, error: str | None = None) -> None:
    if to not in TRANSITIONS.get(m.status, ()):
        raise IllegalTransition(f"{m.status} -> {to}")
    m.status = to
    if error is not None:
        m.error = error[:300]


def event(db: Session, m: BrowserMission, kind: str, detail: dict[str, Any],
          worker_id: uuid.UUID | None = None) -> None:
    db.add(BrowserSessionEvent(tenant_id=m.tenant_id, mission_id=m.id, kind=kind,
                               detail=detail, worker_id=worker_id))


def create_mission(db: Session, tenant_id: uuid.UUID, kind: str, url: str, *,
                   mention_id: uuid.UUID | None = None, source_id: uuid.UUID | None = None,
                   params: dict[str, Any] | None = None, priority: int = 5,
                   created_by: uuid.UUID | None = None, robots: RobotsCache | None = None,
                   deadline_hours: int = 24) -> BrowserMission:
    """CREATED → VALIDATING → QUEUED (authorized) or FAILED (with the reason)."""
    domain = domain_of(url)
    m = BrowserMission(id=uuid.uuid4(), tenant_id=tenant_id, kind=kind, url=url[:2000],
                       domain=domain, status="CREATED", priority=priority,
                       mention_id=mention_id, source_id=source_id, params=params or {},
                       created_by=created_by,
                       deadline_at=datetime.now(UTC) + timedelta(hours=deadline_hours))
    db.add(m)
    db.flush()
    transition(m, "VALIDATING")
    db.flush()
    auth = authorize(db, tenant_id, url, robots=robots)
    m.authorization_record = auth.record
    event(db, m, "validation", auth.record)
    if auth.ok:
        m.timeout_seconds = domain_policy(db, domain).timeout_seconds
        transition(m, "QUEUED")
    else:
        transition(m, "FAILED", error=f"not_authorized:{auth.reason}")
    db.flush()
    return m


def cancel(db: Session, m: BrowserMission, reason: str) -> bool:
    if m.status in TERMINAL or "CANCELLED" not in TRANSITIONS.get(m.status, ()):
        return False
    transition(m, "CANCELLED", error=reason)
    db.flush()
    return True
