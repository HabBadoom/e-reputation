"""Reports: request generation (queued job), list, download."""

import uuid
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer

from app.api.deps import AuthContext, require
from app.core.errors import AppError, NotFound
from app.core.rate_limit import get_limiter
from app.db.models import Brand, Report
from app.db.session import get_db
from app.jobs.queue import enqueue
from app.modules.audit.service import record
from app.reports.data import default_period

router = APIRouter(prefix="/reports", tags=["reports"])

Kind = Literal["daily", "weekly", "monthly", "crisis", "competitor", "custom"]
Format = Literal["pdf", "xlsx", "csv", "html"]
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"


class ReportIn(BaseModel):
    kind: Kind = "weekly"
    format: Format = "pdf"
    locale: Literal["fr", "ar", "en"] = "fr"
    brand_id: uuid.UUID | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None

    @model_validator(mode="after")
    def _period(self) -> "ReportIn":
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("give both period_start and period_end, or neither")
        if self.period_start and self.period_end:
            for v in (self.period_start, self.period_end):
                if v.tzinfo is None:
                    raise ValueError("dates must include a timezone")
            if self.period_end <= self.period_start:
                raise ValueError("period_end must be after period_start")
            if (self.period_end - self.period_start).days > 366:
                raise ValueError("period cannot exceed 366 days")
        elif self.kind == "custom":
            raise ValueError("custom reports need a period")
        return self


class ReportOut(BaseModel):
    id: uuid.UUID
    kind: str
    format: str
    locale: str
    brand_id: uuid.UUID | None
    period_start: datetime
    period_end: datetime
    status: str
    filename: str | None
    size_bytes: int | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class ReportPage(BaseModel):
    items: list[ReportOut]
    total: int
    limit: int
    offset: int


def _out(r: Report) -> ReportOut:
    return ReportOut(id=r.id, kind=r.kind, format=r.format, locale=r.locale, brand_id=r.brand_id,
                     period_start=r.period_start, period_end=r.period_end, status=r.status,
                     filename=r.filename, size_bytes=r.size_bytes, error=r.error,
                     created_at=r.created_at, finished_at=r.finished_at)


def _get(db: Session, ctx: AuthContext, report_id: uuid.UUID, *, content: bool = False
         ) -> Report:
    stmt = select(Report).where(Report.id == report_id, Report.tenant_id == ctx.tenant_id)
    if content:
        stmt = stmt.options(undefer(Report.content))
    r = db.scalars(stmt).one_or_none()
    if r is None:
        raise NotFound("Report not found")
    return r


@router.post("", response_model=ReportOut, status_code=202)
def create_report(body: ReportIn, request: Request,
                  ctx: AuthContext = Depends(require("reports:create")),
                  db: Session = Depends(get_db)) -> ReportOut:
    get_limiter().hit(f"reports:create:{ctx.tenant_id}", 30, 3600)
    if body.brand_id and db.scalar(select(Brand.id).where(
            Brand.id == body.brand_id, Brand.tenant_id == ctx.tenant_id)) is None:
        raise NotFound("Brand not found")
    pending = db.scalar(select(func.count()).select_from(Report).where(
        Report.tenant_id == ctx.tenant_id, Report.status.in_(("queued", "running")))) or 0
    if pending >= 5:
        raise AppError("Too many reports in progress", code="limit_reached", status_code=409)
    if body.period_start and body.period_end:
        start, end = body.period_start, body.period_end
    else:
        start, end = default_period(body.kind, datetime.now(UTC))
    r = Report(id=uuid.uuid4(), tenant_id=ctx.tenant_id, kind=body.kind, brand_id=body.brand_id,
               period_start=start, period_end=end, format=body.format, locale=body.locale,
               status="queued", created_by=ctx.user_id)
    db.add(r)
    db.flush()
    enqueue(db, "report.generate", {"report_id": str(r.id)}, tenant_id=ctx.tenant_id,
            max_attempts=1)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="report.requested",
           target_type="report", target_id=r.id,
           meta={"kind": body.kind, "format": body.format}, request=request)
    db.commit()
    return _out(r)


@router.get("", response_model=ReportPage)
def list_reports(ctx: AuthContext = Depends(require("reports:read")),
                 db: Session = Depends(get_db),
                 limit: int = Query(default=50, ge=1, le=200),
                 offset: int = Query(default=0, ge=0)) -> ReportPage:
    stmt = select(Report).where(Report.tenant_id == ctx.tenant_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Report.created_at.desc()).limit(limit).offset(offset))
    return ReportPage(items=[_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: uuid.UUID, ctx: AuthContext = Depends(require("reports:read")),
               db: Session = Depends(get_db)) -> ReportOut:
    return _out(_get(db, ctx, report_id))


@router.get("/{report_id}/download")
def download_report(report_id: uuid.UUID, ctx: AuthContext = Depends(require("reports:read")),
                    db: Session = Depends(get_db)) -> Response:
    r = _get(db, ctx, report_id, content=True)
    if r.status != "ready" or r.content is None:
        raise AppError("Report is not ready", code="not_ready", status_code=409)
    name = r.filename or f"report.{r.format}"
    disposition = f"attachment; filename=\"{name.encode('ascii', 'ignore').decode()}\"; " \
                  f"filename*=UTF-8''{quote(name)}"
    # HTML reports are downloaded, never rendered on our origin.
    return Response(r.content, media_type=r.content_type or "application/octet-stream",
                    headers={"content-disposition": disposition,
                             "x-content-type-options": "nosniff",
                             "content-security-policy": CSP,
                             "cache-control": "private, no-store"})


@router.delete("/{report_id}", status_code=204)
def delete_report(report_id: uuid.UUID, request: Request,
                  ctx: AuthContext = Depends(require("reports:create")),
                  db: Session = Depends(get_db)) -> Response:
    r = _get(db, ctx, report_id)
    db.delete(r)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="report.deleted",
           target_type="report", target_id=report_id, request=request)
    db.commit()
    return Response(status_code=204)
