"""Monitoring projects: what to watch, how often; runs history; manual runs."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.core.errors import Conflict, NotFound
from app.core.rate_limit import get_limiter
from app.db.models import Brand, MonitoringProject, MonitoringRun
from app.db.session import get_db
from app.jobs.queue import enqueue
from app.modules.audit.service import record
from app.modules.billing.entitlements import ensure_capacity, ensure_frequency
from app.modules.monitoring.query_plan import load_query_plan

router = APIRouter(prefix="/monitoring-projects", tags=["monitoring"])

Lang = Literal["fr", "ar", "ar-DZ", "en", "kab"]


def _clean(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("must not be blank")
    return v


Name = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_clean)]
Frequency = Annotated[int, Field(ge=15, le=10080, description="Minutes between runs")]


class ProjectIn(BaseModel):
    name: Name
    brand_id: uuid.UUID
    frequency_minutes: Frequency = 1440
    include_competitors: bool = True
    languages: list[Lang] = Field(default_factory=lambda: ["fr", "ar", "ar-DZ"], min_length=1)
    status: Literal["active", "paused"] = "active"


class ProjectPatch(BaseModel):
    name: Name | None = None
    frequency_minutes: Frequency | None = None
    include_competitors: bool | None = None
    languages: list[Lang] | None = Field(default=None, min_length=1)
    status: Literal["active", "paused"] | None = None


class RunOut(BaseModel):
    id: uuid.UUID
    trigger: str
    status: str
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: dict[str, Any]
    error: str | None


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    brand_id: uuid.UUID
    brand_name: str
    status: str
    frequency_minutes: int
    include_competitors: bool
    languages: list[str]
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run: RunOut | None
    created_at: datetime


def _run_out(r: MonitoringRun) -> RunOut:
    return RunOut(id=r.id, trigger=r.trigger, status=r.status, queued_at=r.queued_at,
                  started_at=r.started_at, finished_at=r.finished_at, summary=r.summary or {},
                  error=r.error)


def _last_runs(db: Session, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, MonitoringRun]:
    if not project_ids:
        return {}
    sub = (select(MonitoringRun.project_id, func.max(MonitoringRun.queued_at).label("q"))
           .where(MonitoringRun.project_id.in_(project_ids))
           .group_by(MonitoringRun.project_id).subquery())
    rows = db.scalars(select(MonitoringRun).join(
        sub, (sub.c.project_id == MonitoringRun.project_id) & (sub.c.q == MonitoringRun.queued_at)))
    return {r.project_id: r for r in rows}


def _out(p: MonitoringProject, brand_name: str, last: MonitoringRun | None) -> ProjectOut:
    return ProjectOut(id=p.id, name=p.name, brand_id=p.brand_id, brand_name=brand_name,
                      status=p.status, frequency_minutes=p.frequency_minutes,
                      include_competitors=p.include_competitors, languages=list(p.languages),
                      next_run_at=p.next_run_at, last_run_at=p.last_run_at,
                      last_run=_run_out(last) if last else None, created_at=p.created_at)


def _get(db: Session, ctx: AuthContext, project_id: uuid.UUID) -> tuple[MonitoringProject, str]:
    row = db.execute(select(MonitoringProject, Brand.name)
                     .join(Brand, Brand.id == MonitoringProject.brand_id)
                     .where(MonitoringProject.id == project_id,
                            MonitoringProject.tenant_id == ctx.tenant_id)).one_or_none()
    if row is None:
        raise NotFound("Monitoring project not found")
    return row[0], row[1]


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict("A monitoring project with this name already exists",
                       code="duplicate") from e


def _audit(db: Session, ctx: AuthContext, request: Request, action: str,
           p: MonitoringProject, **meta: object) -> None:
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action=action,
           target_type="monitoring_project", target_id=p.id, meta={"name": p.name, **meta},
           request=request)


@router.get("", response_model=list[ProjectOut])
def list_projects(ctx: AuthContext = Depends(require("monitoring:read")),
                  db: Session = Depends(get_db),
                  brand_id: uuid.UUID | None = None) -> list[ProjectOut]:
    q = (select(MonitoringProject, Brand.name).join(Brand, Brand.id == MonitoringProject.brand_id)
         .where(MonitoringProject.tenant_id == ctx.tenant_id))
    if brand_id:
        q = q.where(MonitoringProject.brand_id == brand_id)
    rows = db.execute(q.order_by(MonitoringProject.name)).all()
    last = _last_runs(db, [p.id for p, _ in rows])
    return [_out(p, bn, last.get(p.id)) for p, bn in rows]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, request: Request,
                   ctx: AuthContext = Depends(require("monitoring:write")),
                   db: Session = Depends(get_db)) -> ProjectOut:
    brand = db.scalars(select(Brand).where(Brand.id == body.brand_id,
                                           Brand.tenant_id == ctx.tenant_id)).one_or_none()
    if brand is None:
        raise NotFound("Brand not found")
    ensure_capacity(db, ctx.tenant_id, "max_monitoring_projects")
    ensure_frequency(db, ctx.tenant_id, body.frequency_minutes)
    p = MonitoringProject(
        tenant_id=ctx.tenant_id, brand_id=brand.id, name=body.name, status=body.status,
        frequency_minutes=body.frequency_minutes, include_competitors=body.include_competitors,
        languages=list(dict.fromkeys(body.languages)), created_by=ctx.user_id,
        next_run_at=datetime.now(UTC) if body.status == "active" else None,
    )
    db.add(p)
    _flush(db)
    _audit(db, ctx, request, "monitoring_project.created", p)
    db.commit()
    return _out(p, brand.name, None)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, ctx: AuthContext = Depends(require("monitoring:read")),
                db: Session = Depends(get_db)) -> ProjectOut:
    p, bn = _get(db, ctx, project_id)
    return _out(p, bn, _last_runs(db, [p.id]).get(p.id))


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: uuid.UUID, body: ProjectPatch, request: Request,
                   ctx: AuthContext = Depends(require("monitoring:write")),
                   db: Session = Depends(get_db)) -> ProjectOut:
    p, bn = _get(db, ctx, project_id)
    changes = body.model_dump(exclude_unset=True)
    if "frequency_minutes" in changes:
        ensure_frequency(db, ctx.tenant_id, changes["frequency_minutes"])
    if "languages" in changes:
        changes["languages"] = list(dict.fromkeys(changes["languages"]))
    was_active = p.status == "active"
    for k, v in changes.items():
        setattr(p, k, v)
    if p.status == "active" and not was_active:
        p.next_run_at = datetime.now(UTC)   # resume: run soon
    elif p.status == "paused":
        p.next_run_at = None
    p.updated_at = datetime.now(UTC)
    _flush(db)
    _audit(db, ctx, request, "monitoring_project.updated", p, fields=sorted(changes))
    db.commit()
    return _out(p, bn, _last_runs(db, [p.id]).get(p.id))


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, request: Request,
                   ctx: AuthContext = Depends(require("monitoring:write")),
                   db: Session = Depends(get_db)) -> Response:
    p, _ = _get(db, ctx, project_id)
    _audit(db, ctx, request, "monitoring_project.deleted", p)
    db.delete(p)
    db.commit()
    return Response(status_code=204)


@router.post("/{project_id}/runs", response_model=RunOut, status_code=202)
def run_now(project_id: uuid.UUID, request: Request,
            ctx: AuthContext = Depends(require("monitoring:write")),
            db: Session = Depends(get_db)) -> RunOut:
    p, _ = _get(db, ctx, project_id)
    get_limiter().hit(f"monitoring:manual:{p.id}", 10, 3600)
    busy = db.scalar(select(func.count()).select_from(MonitoringRun)
                     .where(MonitoringRun.project_id == p.id,
                            MonitoringRun.status.in_(["queued", "running"])))
    if busy:
        raise Conflict("A run is already queued or in progress", code="run_in_progress")
    run = MonitoringRun(tenant_id=ctx.tenant_id, project_id=p.id, trigger="manual")
    db.add(run)
    db.flush()
    enqueue(db, "monitoring.run",
            {"project_id": str(p.id), "run_id": str(run.id), "trigger": "manual"},
            tenant_id=ctx.tenant_id, max_attempts=3)
    _audit(db, ctx, request, "monitoring_project.run_requested", p)
    db.commit()
    return _run_out(run)


@router.get("/{project_id}/runs", response_model=list[RunOut])
def list_runs(project_id: uuid.UUID, ctx: AuthContext = Depends(require("monitoring:read")),
              db: Session = Depends(get_db),
              limit: int = Query(default=20, ge=1, le=100)) -> list[RunOut]:
    p, _ = _get(db, ctx, project_id)
    return [_run_out(r) for r in db.scalars(
        select(MonitoringRun).where(MonitoringRun.project_id == p.id)
        .order_by(MonitoringRun.queued_at.desc()).limit(limit))]


@router.get("/{project_id}/query-plan")
def query_plan(project_id: uuid.UUID, ctx: AuthContext = Depends(require("monitoring:read")),
               db: Session = Depends(get_db)) -> dict:
    p, _ = _get(db, ctx, project_id)
    return {"project_id": str(p.id), "groups": [g.to_dict() for g in load_query_plan(db, p)]}
