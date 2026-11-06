"""Source registry: what is registered vs what is actually monitored, and why."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.collection.robots import RobotsCache
from app.collection.urls import domain_of, is_http_url
from app.connectors.base import ConnectorError
from app.connectors.rss import RssConnector
from app.connectors.serper import SerperConnector
from app.connectors.web import BlockedByRobots, WebPageConnector
from app.core.errors import AppError, Conflict, NotFound
from app.core.rate_limit import get_limiter
from app.db.models import ConnectorPolicy, Source
from app.db.session import get_db
from app.modules.audit.service import record

router = APIRouter(tags=["sources"])

Category = Literal["unknown", "news", "blog", "forum", "social", "review", "directory",
                   "ecommerce", "video", "app_store", "company", "other"]
Coverage = Literal["AVAILABLE", "PARTIALLY_AVAILABLE", "PENDING_AUTHORIZATION", "RESTRICTED",
                   "UNAVAILABLE", "CONNECTOR_ERROR"]


class SourceIn(BaseModel):
    kind: Literal["rss", "website"]
    url: str = Field(min_length=8, max_length=1000)
    name: str = Field(default="", max_length=200)
    category: Category = "unknown"
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class SourcePatch(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, max_length=200)
    category: Category | None = None
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class SourceOut(BaseModel):
    id: uuid.UUID
    kind: str
    url: str
    domain: str
    name: str
    category: str
    language: str | None
    connector: str
    enabled: bool
    monitored: bool            # enabled AND usable: what the dashboard counts as covered
    discovered_via: str
    coverage_status: str
    restrictions: str
    mentions_count: int
    last_collected_at: datetime | None
    last_checked_at: datetime | None
    last_error: str | None
    created_at: datetime


class SourcePage(BaseModel):
    items: list[SourceOut]
    total: int
    limit: int
    offset: int


class CheckOut(BaseModel):
    ok: bool
    detail: str
    source: SourceOut
    discovered_feeds: list[SourceOut] = []


def _monitored(s: Source) -> bool:
    # Websites are reached through search verification; feeds are polled directly.
    return s.enabled and s.coverage_status not in ("RESTRICTED", "UNAVAILABLE",
                                                   "PENDING_AUTHORIZATION", "CONNECTOR_ERROR")


def _out(s: Source) -> SourceOut:
    return SourceOut(id=s.id, kind=s.kind, url=s.url, domain=s.domain, name=s.name,
                     category=s.category, language=s.language, connector=s.connector,
                     enabled=s.enabled, monitored=_monitored(s), discovered_via=s.discovered_via,
                     coverage_status=s.coverage_status, restrictions=s.restrictions,
                     mentions_count=s.mentions_count, last_collected_at=s.last_collected_at,
                     last_checked_at=s.last_checked_at, last_error=s.last_error,
                     created_at=s.created_at)


def _get(db: Session, ctx: AuthContext, source_id: uuid.UUID) -> Source:
    s = db.scalars(select(Source).where(Source.id == source_id,
                                        Source.tenant_id == ctx.tenant_id)).one_or_none()
    if s is None:
        raise NotFound("Source not found")
    return s


def _check(db: Session, ctx: AuthContext, src: Source) -> CheckOut:
    now = datetime.now(UTC)
    src.last_checked_at = now
    discovered: list[Source] = []
    if src.kind == "rss":
        try:
            title, items = RssConnector().fetch(src.url)
        except ConnectorError as e:
            src.last_error, src.coverage_status = str(e)[:500], "CONNECTOR_ERROR"
            src.consecutive_errors += 1
            return CheckOut(ok=False, detail=e.reason, source=_out(src))
        src.last_error, src.consecutive_errors, src.coverage_status = None, 0, "AVAILABLE"
        if not src.name and title:
            src.name = title[:200]
        return CheckOut(ok=True, detail=f"{len(items)} items", source=_out(src))

    web = WebPageConnector(RobotsCache())
    try:
        page = web.fetch_page(src.url)
    except BlockedByRobots:
        src.coverage_status, src.restrictions = "RESTRICTED", "robots.txt disallows our crawler"
        return CheckOut(ok=False, detail="blocked_by_robots", source=_out(src))
    except ConnectorError as e:
        src.last_error, src.coverage_status = str(e)[:500], "UNAVAILABLE"
        return CheckOut(ok=False, detail=e.reason, source=_out(src))
    src.last_error, src.coverage_status, src.restrictions = None, "AVAILABLE", ""
    if not src.name and page.title:
        src.name = page.title[:200]
    for feed in page.feed_links[:5]:
        if not is_http_url(feed):
            continue
        exists = db.scalar(select(Source.id).where(
            Source.tenant_id == ctx.tenant_id, Source.kind == "rss",
            func.lower(Source.url) == feed.lower()))
        if exists:
            continue
        f = Source(tenant_id=ctx.tenant_id, kind="rss", url=feed, domain=domain_of(feed),
                   name=f"{src.name or src.domain} (RSS)"[:200], category=src.category,
                   language=src.language, connector="rss", enabled=False,
                   discovered_via="feed_discovery", coverage_status="PARTIALLY_AVAILABLE")
        db.add(f)
        discovered.append(f)
    db.flush()
    return CheckOut(ok=True, detail="reachable", source=_out(src),
                    discovered_feeds=[_out(f) for f in discovered])


@router.get("/sources", response_model=SourcePage)
def list_sources(ctx: AuthContext = Depends(require("sources:read")),
                 db: Session = Depends(get_db),
                 kind: Literal["rss", "website"] | None = None,
                 coverage: Coverage | None = None,
                 q: str | None = Query(default=None, max_length=200),
                 limit: int = Query(default=50, ge=1, le=200),
                 offset: int = Query(default=0, ge=0)) -> SourcePage:
    stmt = select(Source).where(Source.tenant_id == ctx.tenant_id)
    if kind:
        stmt = stmt.where(Source.kind == kind)
    if coverage:
        stmt = stmt.where(Source.coverage_status == coverage)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(Source.domain.ilike(like) | Source.name.ilike(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Source.mentions_count.desc(), Source.domain)
                      .limit(limit).offset(offset))
    return SourcePage(items=[_out(s) for s in rows], total=total, limit=limit, offset=offset)


@router.post("/sources", response_model=CheckOut, status_code=201)
def add_source(body: SourceIn, request: Request,
               ctx: AuthContext = Depends(require("sources:write")),
               db: Session = Depends(get_db)) -> CheckOut:
    url = body.url.strip()
    if not is_http_url(url):
        raise AppError("Only http(s) URLs are accepted", code="invalid_url", status_code=422)
    get_limiter().hit(f"sources:add:{ctx.tenant_id}", 60, 3600)
    src = Source(tenant_id=ctx.tenant_id, kind=body.kind, url=url, domain=domain_of(url),
                 name=body.name.strip(), category=body.category, language=body.language,
                 connector="rss" if body.kind == "rss" else "web", discovered_via="manual",
                 coverage_status="PARTIALLY_AVAILABLE")
    db.add(src)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict("This source is already registered", code="duplicate") from e
    result = _check(db, ctx, src)     # immediate feedback: reachable? a feed? robots?
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="source.added",
           target_type="source", target_id=src.id, meta={"url": url, "kind": body.kind},
           request=request)
    db.commit()
    return result


@router.post("/sources/{source_id}/check", response_model=CheckOut)
def check_source(source_id: uuid.UUID, ctx: AuthContext = Depends(require("sources:write")),
                 db: Session = Depends(get_db)) -> CheckOut:
    src = _get(db, ctx, source_id)
    get_limiter().hit(f"sources:check:{ctx.tenant_id}", 120, 3600)
    result = _check(db, ctx, src)
    db.commit()
    return result


@router.patch("/sources/{source_id}", response_model=SourceOut)
def update_source(source_id: uuid.UUID, body: SourcePatch, request: Request,
                  ctx: AuthContext = Depends(require("sources:write")),
                  db: Session = Depends(get_db)) -> SourceOut:
    src = _get(db, ctx, source_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(src, k, v.strip() if isinstance(v, str) else v)
    src.updated_at = datetime.now(UTC)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="source.updated",
           target_type="source", target_id=src.id, meta={"fields": sorted(changes)},
           request=request)
    db.commit()
    return _out(src)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: uuid.UUID, request: Request,
                  ctx: AuthContext = Depends(require("sources:write")),
                  db: Session = Depends(get_db)) -> Response:
    src = _get(db, ctx, source_id)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="source.deleted",
           target_type="source", target_id=src.id, meta={"url": src.url}, request=request)
    db.delete(src)    # mentions keep their URL/domain; source_id becomes NULL
    db.commit()
    return Response(status_code=204)


class ConnectorOut(BaseModel):
    key: str
    name: str
    version: str
    category: str
    access_method: str
    capabilities: list[str]
    limitations: list[str]
    configured: bool
    review_status: str
    allowed: bool
    note: str
    legal_basis: str
    data_categories: list[str]
    retention_days: int


@router.get("/connectors", response_model=list[ConnectorOut])
def list_connectors(_: AuthContext = Depends(require("sources:read")),
                    db: Session = Depends(get_db)) -> list[ConnectorOut]:
    """What can collect data, whether it is configured, and its compliance status."""
    out = []
    serper = SerperConnector()
    for conn, configured in ((serper, serper.configured), (RssConnector(), True),
                             (WebPageConnector(), True)):
        pol = db.get(ConnectorPolicy, conn.key)
        perm = conn.check_permissions(db)
        d = conn.describe()
        out.append(ConnectorOut(
            **d, name=pol.name if pol else conn.key, configured=configured,
            review_status=perm.review_status, allowed=perm.allowed and configured,
            note=perm.reason if configured else "Not configured on this server",
            legal_basis=pol.legal_basis if pol else "", data_categories=pol.data_categories
            if pol else [], retention_days=pol.retention_days if pol else 0))
    return out
