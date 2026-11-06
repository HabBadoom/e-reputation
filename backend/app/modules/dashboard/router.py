"""Dashboard summary. Returns only what the platform actually has; unbuilt capabilities are
reported as unavailable instead of being filled with placeholder numbers."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.db.models import (
    AuditLog,
    Brand,
    BrandAlias,
    Competitor,
    Membership,
    MonitoringProject,
    MonitoringRun,
    Product,
    User,
)
from app.db.session import get_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class Capability(BaseModel):
    available: bool
    reason: str | None = None


class Activity(BaseModel):
    action: str
    actor_email: str | None
    created_at: datetime


class LastRun(BaseModel):
    project_name: str
    status: str
    finished_at: datetime | None
    reason: str | None


class DashboardOut(BaseModel):
    brands_total: int
    brands_active: int
    aliases_total: int
    members_total: int
    products_total: int
    competitors_total: int
    projects_total: int
    projects_active: int
    last_run: LastRun | None
    recent_activity: list[Activity]
    mentions: Capability
    sentiment: Capability
    sources: Capability


NOT_YET = "Content collection is not enabled yet (Milestone 3). No mention data exists."


@router.get("/summary", response_model=DashboardOut)
def summary(ctx: AuthContext = Depends(require("brands:read")),
            db: Session = Depends(get_db)) -> DashboardOut:
    t = ctx.tenant_id
    brands_total = db.scalar(select(func.count()).select_from(Brand).where(Brand.tenant_id == t))
    brands_active = db.scalar(select(func.count()).select_from(Brand)
                              .where(Brand.tenant_id == t, Brand.status == "active"))
    aliases = db.scalar(select(func.count()).select_from(BrandAlias)
                        .where(BrandAlias.tenant_id == t))
    members = db.scalar(select(func.count()).select_from(Membership)
                        .where(Membership.tenant_id == t, Membership.status == "active"))

    def n(model, *where) -> int:  # noqa: ANN001
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    last = db.execute(
        select(MonitoringRun, MonitoringProject.name)
        .join(MonitoringProject, MonitoringProject.id == MonitoringRun.project_id)
        .where(MonitoringRun.tenant_id == t, MonitoringRun.finished_at.is_not(None))
        .order_by(MonitoringRun.finished_at.desc()).limit(1)).one_or_none()
    last_run = LastRun(project_name=last[1], status=last[0].status,
                       finished_at=last[0].finished_at,
                       reason=(last[0].summary or {}).get("reason")) if last else None
    activity: list[Activity] = []
    if ctx.has("audit:read"):
        rows = db.execute(
            select(AuditLog.action, User.email, AuditLog.created_at)
            .outerjoin(User, User.id == AuditLog.actor_user_id)
            .where(AuditLog.tenant_id == t).order_by(AuditLog.created_at.desc()).limit(8)
        ).all()
        activity = [Activity(action=a, actor_email=e, created_at=c) for a, e, c in rows]
    return DashboardOut(
        brands_total=brands_total or 0, brands_active=brands_active or 0,
        aliases_total=aliases or 0, members_total=members or 0, recent_activity=activity,
        products_total=n(Product, Product.tenant_id == t),
        competitors_total=n(Competitor, Competitor.tenant_id == t),
        projects_total=n(MonitoringProject, MonitoringProject.tenant_id == t),
        projects_active=n(MonitoringProject, MonitoringProject.tenant_id == t,
                          MonitoringProject.status == "active"),
        last_run=last_run,
        mentions=Capability(available=False, reason=NOT_YET),
        sentiment=Capability(available=False, reason=NOT_YET),
        sources=Capability(available=False, reason="Source registry arrives in Milestone 3."),
    )
