"""Dashboard summary. Returns only what the platform actually has; unbuilt capabilities are
reported as unavailable instead of being filled with placeholder numbers."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.db.models import (
    AuditLog,
    Brand,
    BrandAlias,
    Competitor,
    Membership,
    Mention,
    MonitoringProject,
    MonitoringRun,
    Product,
    Source,
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


class Count(BaseModel):
    key: str
    count: int


class MentionStats(BaseModel):
    total: int
    last_7_days: int
    verified_on_page: int
    reposts: int
    top_domains: list[Count]
    by_entity: list[Count]
    by_method: list[Count]


class SourceStats(BaseModel):
    registered: int
    monitored: int
    by_coverage: list[Count]


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
    mention_stats: MentionStats
    source_stats: SourceStats
    recent_activity: list[Activity]
    mentions: Capability
    sentiment: Capability
    sources: Capability


NO_SENTIMENT = ("Sentiment analysis arrives with the Algerian NLP module (Milestone 4); "
                "no sentiment is shown until it has been measured on the benchmark.")


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
    def grouped(col, *where, limit: int = 10) -> list[Count]:  # noqa: ANN001
        rows = db.execute(select(col, func.count()).where(Mention.tenant_id == t, *where)
                          .group_by(col).order_by(func.count().desc()).limit(limit)).all()
        return [Count(key=str(k), count=c) for k, c in rows]

    week_ago = func.now() - text("interval '7 days'")
    mention_stats = MentionStats(
        total=n(Mention, Mention.tenant_id == t),
        last_7_days=n(Mention, Mention.tenant_id == t, Mention.collected_at >= week_ago),
        verified_on_page=n(Mention, Mention.tenant_id == t,
                           Mention.verification_status == "verified_on_page"),
        reposts=n(Mention, Mention.tenant_id == t, Mention.duplicate_of.is_not(None)),
        top_domains=grouped(Mention.domain, Mention.duplicate_of.is_(None), limit=5),
        by_entity=grouped(Mention.entity_type),
        by_method=grouped(Mention.collection_method),
    )
    cov = db.execute(select(Source.coverage_status, func.count()).where(Source.tenant_id == t)
                     .group_by(Source.coverage_status)).all()
    source_stats = SourceStats(
        registered=sum(c for _, c in cov),
        monitored=n(Source, Source.tenant_id == t, Source.enabled.is_(True),
                    Source.coverage_status.in_(["AVAILABLE", "PARTIALLY_AVAILABLE"])),
        by_coverage=[Count(key=k, count=c) for k, c in cov],
    )
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
        last_run=last_run, mention_stats=mention_stats, source_stats=source_stats,
        mentions=Capability(available=True),
        sentiment=Capability(available=False, reason=NO_SENTIMENT),
        sources=Capability(available=True),
    )
