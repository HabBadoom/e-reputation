"""Browser missions, detected challenges and (platform admins) fleet status."""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.browser.missions import cancel
from app.core.errors import AppError, NotFound
from app.db.models import BrowserMission, BrowserSession, BrowserSessionEvent, CaptchaEvent, User
from app.db.session import get_db
from app.modules.audit.service import record

router = APIRouter(prefix="/browser", tags=["browser"])

Status = Literal["CREATED", "VALIDATING", "QUEUED", "ASSIGNED", "RUNNING", "PAUSED",
                 "WAITING_FOR_HUMAN", "COMPLETED", "FAILED", "CANCELLED", "EXPIRED"]


class MissionOut(BaseModel):
    id: uuid.UUID
    kind: str
    url: str
    domain: str
    status: str
    priority: int
    mention_id: uuid.UUID | None
    source_id: uuid.UUID | None
    attempts: int
    max_attempts: int
    error: str | None
    result: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    deadline_at: datetime


class EventOut(BaseModel):
    kind: str
    from_status: str | None
    to_status: str | None
    detail: dict[str, Any]
    created_at: datetime


class SessionOut(BaseModel):
    id: uuid.UUID
    isolation: dict[str, Any]
    opened_at: datetime
    closed_at: datetime | None
    cleanup_verified: bool
    requests_blocked: int


class ChallengeOut(BaseModel):
    id: uuid.UUID
    mission_id: uuid.UUID | None
    source_id: uuid.UUID | None
    mention_id: uuid.UUID | None
    domain: str
    url: str
    via: str
    kind: str
    confidence: float
    signals: list[Any]
    state: str
    history: list[Any]
    response: dict[str, Any]
    detected_at: datetime


class MissionDetail(MissionOut):
    authorization_record: dict[str, Any]
    events: list[EventOut]
    sessions: list[SessionOut]
    challenges: list[ChallengeOut]


class MissionPage(BaseModel):
    items: list[MissionOut]
    total: int
    limit: int
    offset: int
    by_status: dict[str, int]


class ChallengePage(BaseModel):
    items: list[ChallengeOut]
    total: int
    limit: int
    offset: int
    by_kind: dict[str, int]


def _m(m: BrowserMission) -> dict[str, Any]:
    return {k: getattr(m, k) for k in MissionOut.model_fields}


def _c(c: CaptchaEvent) -> ChallengeOut:
    return ChallengeOut(**{k: getattr(c, k) for k in ChallengeOut.model_fields})


@router.get("/missions", response_model=MissionPage)
def list_missions(ctx: AuthContext = Depends(require("browser:read")),
                  db: Session = Depends(get_db), status: Status | None = None,
                  kind: Literal["render_page", "check_source"] | None = None,
                  limit: int = Query(default=50, ge=1, le=200),
                  offset: int = Query(default=0, ge=0)) -> MissionPage:
    stmt = select(BrowserMission).where(BrowserMission.tenant_id == ctx.tenant_id)
    if status:
        stmt = stmt.where(BrowserMission.status == status)
    if kind:
        stmt = stmt.where(BrowserMission.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(BrowserMission.created_at.desc()).limit(limit).offset(offset))
    by = dict(db.execute(select(BrowserMission.status, func.count()).where(
        BrowserMission.tenant_id == ctx.tenant_id).group_by(BrowserMission.status)).all())
    return MissionPage(items=[MissionOut(**_m(m)) for m in rows], total=total, limit=limit,
                       offset=offset, by_status=by)


def _get(db: Session, ctx: AuthContext, mission_id: uuid.UUID) -> BrowserMission:
    m = db.scalars(select(BrowserMission).where(
        BrowserMission.id == mission_id, BrowserMission.tenant_id == ctx.tenant_id)).one_or_none()
    if m is None:
        raise NotFound("Mission not found")
    return m


@router.get("/missions/{mission_id}", response_model=MissionDetail)
def get_mission(mission_id: uuid.UUID, ctx: AuthContext = Depends(require("browser:read")),
                db: Session = Depends(get_db)) -> MissionDetail:
    m = _get(db, ctx, mission_id)
    events = db.scalars(select(BrowserSessionEvent).where(
        BrowserSessionEvent.mission_id == m.id).order_by(BrowserSessionEvent.id))
    sessions = db.scalars(select(BrowserSession).where(BrowserSession.mission_id == m.id)
                          .order_by(BrowserSession.opened_at))
    challenges = db.scalars(select(CaptchaEvent).where(CaptchaEvent.mission_id == m.id))
    return MissionDetail(
        **_m(m), authorization_record=m.authorization_record,
        events=[EventOut(**{k: getattr(e, k) for k in EventOut.model_fields}) for e in events],
        sessions=[SessionOut(**{k: getattr(x, k) for k in SessionOut.model_fields})
                  for x in sessions],
        challenges=[_c(c) for c in challenges])


@router.post("/missions/{mission_id}/cancel", response_model=MissionOut)
def cancel_mission(mission_id: uuid.UUID, request: Request,
                   ctx: AuthContext = Depends(require("browser:run")),
                   db: Session = Depends(get_db)) -> MissionOut:
    m = _get(db, ctx, mission_id)
    if not cancel(db, m, "cancelled_by_user"):
        raise AppError("This mission can no longer be cancelled", code="not_cancellable",
                       status_code=409)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="browser.mission_cancelled", target_type="browser_mission", target_id=m.id,
           request=request)
    db.commit()
    return MissionOut(**_m(m))


@router.get("/challenges", response_model=ChallengePage)
def list_challenges(ctx: AuthContext = Depends(require("browser:read")),
                    db: Session = Depends(get_db), domain: str | None = Query(None, max_length=255),
                    limit: int = Query(default=50, ge=1, le=200),
                    offset: int = Query(default=0, ge=0)) -> ChallengePage:
    stmt = select(CaptchaEvent).where(CaptchaEvent.tenant_id == ctx.tenant_id)
    if domain:
        stmt = stmt.where(CaptchaEvent.domain == domain.lower())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(CaptchaEvent.detected_at.desc()).limit(limit).offset(offset))
    by = dict(db.execute(select(CaptchaEvent.kind, func.count()).where(
        CaptchaEvent.tenant_id == ctx.tenant_id).group_by(CaptchaEvent.kind)).all())
    return ChallengePage(items=[_c(c) for c in rows], total=total, limit=limit, offset=offset,
                         by_kind=by)


@router.get("/fleet")
def fleet(ctx: AuthContext = Depends(require("browser:read")),
          db: Session = Depends(get_db)) -> dict[str, Any]:
    """Platform admins: workers, queue and domains cooling down (all tenants, no tenant data)."""
    if not db.scalar(select(User.is_platform_admin).where(User.id == ctx.user_id)):
        raise AppError("Platform administrators only", code="forbidden", status_code=403)
    workers = [dict(r._mapping) for r in db.execute(text("""
        SELECT id, name, hostname, version, status, max_contexts, active_contexts, metrics,
               started_at, last_heartbeat_at FROM browser_workers
        ORDER BY (status = 'online') DESC, last_heartbeat_at DESC LIMIT 100"""))]
    cooling = [dict(r._mapping) for r in db.execute(text("""
        SELECT domain, cooldown_until, consecutive_challenges, last_challenge_kind
        FROM browser_domain_state WHERE cooldown_until > now()
        ORDER BY cooldown_until DESC LIMIT 200"""))]
    return {"workers": workers, "cooling_domains": cooling,
            "capacity": sum(w["max_contexts"] for w in workers if w["status"] == "online"),
            "busy": sum(w["active_contexts"] for w in workers if w["status"] == "online")}
