"""Social sources: pages, groups, accounts, hashtags and searches collected through Apify
(third-party provider) or the YouTube Data API (official), per monitoring project."""

import re
import uuid
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.connectors.apify import ApifyConnector
from app.connectors.base import ConnectorError
from app.connectors.youtube import YouTubeConnector, channel_ref
from app.core.errors import AppError, Conflict, NotFound
from app.core.rate_limit import get_limiter
from app.db.models import ConnectorPolicy, MonitoringProject, Source, SourceSync
from app.db.session import get_db
from app.modules.audit.service import record
from app.social.sync import PLAN_KEY, SOCIAL_KINDS, YOUTUBE_KINDS, metric_of, platform_of

router = APIRouter(tags=["social"])

Kind = Literal["facebook_page", "facebook_group", "tiktok_profile", "tiktok_hashtag",
               "tiktok_search", "linkedin_company", "reddit_community", "reddit_search",
               "youtube_channel", "youtube_search"]
QUERY_KINDS = ("tiktok_profile", "tiktok_hashtag", "tiktok_search", "reddit_community",
               "reddit_search", "youtube_search")


class SocialSourceIn(BaseModel):
    project_id: uuid.UUID
    kind: Kind
    url: str = Field(default="", max_length=1000)
    query: str = Field(default="", max_length=300)
    name: str = Field(default="", max_length=200)
    match_mode: Literal["terms", "brand"] = "terms"
    frequency_minutes: int | None = Field(default=None, ge=15, le=10080)


class SocialSourcePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    match_mode: Literal["terms", "brand"] | None = None
    frequency_minutes: int | None = Field(default=None, ge=15, le=10080)


class SyncOut(BaseModel):
    id: uuid.UUID
    provider: str
    stage: str
    trigger: str
    status: str
    max_items: int | None
    items_fetched: int
    items_unreadable: int
    mentions_new: int
    mentions_known: int
    results_billed: int
    api_units: int
    provider_cost_usd: float | None
    error_category: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class SocialSourceOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    kind: str
    platform: str
    provider: str
    url: str
    query: str
    name: str
    match_mode: str
    enabled: bool
    frequency_minutes: int | None
    coverage_status: str
    mentions_count: int
    last_synced_at: datetime | None
    next_sync_at: datetime | None
    last_error: str | None
    pending_comment_posts: int
    last_sync: SyncOut | None


class PlatformUsage(BaseModel):
    metric: str
    used: int
    limit: int | None


class ProviderStatus(BaseModel):
    key: str
    configured: bool
    review_status: str
    allowed: bool
    note: str


class SocialOverview(BaseModel):
    sources: list[SocialSourceOut]
    usage: list[PlatformUsage]
    providers: list[ProviderStatus]


# ------------------------------------------------------------------------- validation
def _fb_url(raw: str, *, group: bool) -> str:
    p = urlsplit(raw.strip())
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not (host == "facebook.com"
                                                  or host.endswith(".facebook.com")):
        raise AppError("Enter a facebook.com address", code="invalid_url", status_code=422)
    path = p.path.rstrip("/")
    if group != path.startswith("/groups/") or path in ("", "/groups"):
        raise AppError("Enter the address of a public group (facebook.com/groups/…)" if group
                       else "Enter the address of a public Page (not a group)",
                       code="invalid_url", status_code=422)
    if "/posts/" in path or "/permalink" in path:
        raise AppError("Enter the Page or group address, not a post", code="invalid_url",
                       status_code=422)
    return f"https://www.facebook.com{path}/"


def _clean_query(q: str, kind: str) -> str:
    q = " ".join(q.split())
    if kind == "tiktok_profile":
        q = q.lstrip("@")
        if not re.fullmatch(r"[\w.]{2,24}", q):
            raise AppError("Enter a TikTok username, e.g. @ooredoo_dz", code="invalid_query",
                           status_code=422)
    elif kind == "tiktok_hashtag":
        q = q.lstrip("#")
        if not re.fullmatch(r"[\w؀-ۿ]{2,100}", q):
            raise AppError("Enter one hashtag, without spaces", code="invalid_query",
                           status_code=422)
    elif kind == "reddit_community":
        q = q.removeprefix("r/").removeprefix("/r/")
        if not re.fullmatch(r"\w{2,21}", q):
            raise AppError("Enter a subreddit name, e.g. algeria", code="invalid_query",
                           status_code=422)
    elif len(q) < 2:
        raise AppError("Enter a search query", code="invalid_query", status_code=422)
    return q


def normalize(kind: str, url: str, query: str) -> tuple[str, str]:
    """(url, query) to store; the URL is the real page of what is followed."""
    if kind in ("facebook_page", "facebook_group"):
        return _fb_url(url, group=kind == "facebook_group"), ""
    if kind == "linkedin_company":
        p = urlsplit(url.strip())
        m = re.match(r"^/(company|school|showcase)/([^/]+)", p.path or "")
        if not ((p.hostname or "").endswith("linkedin.com") and m):
            raise AppError("Enter a company page address (linkedin.com/company/…)",
                           code="invalid_url", status_code=422)
        return f"https://www.linkedin.com/{m.group(1)}/{m.group(2)}/", ""
    if kind == "youtube_channel":
        try:
            ref = channel_ref(url)
        except ConnectorError as e:
            raise AppError(str(e).split(": ", 1)[-1], code="invalid_url", status_code=422) from e
        return ("https://www.youtube.com/" + (f"channel/{ref['id']}" if "id" in ref
                                              else ref["forHandle"])), ""
    q = _clean_query(query or url, kind)
    return {
        "tiktok_profile": f"https://www.tiktok.com/@{q}",
        "tiktok_hashtag": f"https://www.tiktok.com/tag/{quote(q)}",
        "tiktok_search": f"https://www.tiktok.com/search?q={quote(q)}",
        "reddit_community": f"https://www.reddit.com/r/{q}/",
        "reddit_search": f"https://www.reddit.com/search/?q={quote(q)}",
        "youtube_search": f"https://www.youtube.com/results?search_query={quote(q)}",
    }[kind], q


# ------------------------------------------------------------------------------ output
def _sync_out(s: SourceSync) -> SyncOut:
    return SyncOut.model_validate(s, from_attributes=True)


def _out(db: Session, s: Source) -> SocialSourceOut:
    last = db.scalars(select(SourceSync).where(SourceSync.source_id == s.id,
                                               SourceSync.stage == "items")
                      .order_by(SourceSync.created_at.desc()).limit(1)).first()
    return SocialSourceOut(
        id=s.id, project_id=s.project_id, kind=s.kind, platform=platform_of(s.kind),
        provider="youtube" if s.kind in YOUTUBE_KINDS else "apify", url=s.url, query=s.query,
        name=s.name, match_mode=s.match_mode, enabled=s.enabled,
        frequency_minutes=s.frequency_minutes, coverage_status=s.coverage_status,
        mentions_count=s.mentions_count, last_synced_at=s.last_synced_at,
        next_sync_at=s.next_sync_at, last_error=s.last_error,
        pending_comment_posts=len((s.sync_state or {}).get("pending_comments") or []),
        last_sync=_sync_out(last) if last else None)


def _get(db: Session, ctx: AuthContext, source_id: uuid.UUID) -> Source:
    s = db.get(Source, source_id)
    if s is None or s.tenant_id != ctx.tenant_id or s.kind not in SOCIAL_KINDS:
        raise NotFound("Source not found")
    return s


def _providers(db: Session) -> list[ProviderStatus]:
    out = []
    for conn in (ApifyConnector(), YouTubeConnector()):
        perm = conn.check_permissions(db)
        pol = db.get(ConnectorPolicy, conn.key)
        out.append(ProviderStatus(
            key=conn.key, configured=conn.configured,
            review_status=pol.review_status if pol else "missing",
            allowed=perm.allowed and conn.configured,
            note=perm.reason if conn.configured else "not configured on this server"))
    return out


# ------------------------------------------------------------------------------ routes
@router.get("/social-sources", response_model=SocialOverview)
def list_social(ctx: AuthContext = Depends(require("sources:read")),
                db: Session = Depends(get_db),
                project_id: uuid.UUID | None = None) -> SocialOverview:
    from app.billing import usage as usage_mod
    from app.modules.billing.entitlements import plan_info
    stmt = select(Source).where(Source.tenant_id == ctx.tenant_id,
                                Source.kind.in_(SOCIAL_KINDS))
    if project_id:
        stmt = stmt.where(Source.project_id == project_id)
    rows = list(db.scalars(stmt.order_by(Source.created_at)))
    limits = plan_info(db, ctx.tenant_id).limits
    usage = [PlatformUsage(metric=m, used=usage_mod.this_month(db, m), limit=limits.get(k))
             for m, k in PLAN_KEY.items()]
    return SocialOverview(sources=[_out(db, s) for s in rows], usage=usage,
                          providers=_providers(db))


@router.post("/social-sources", response_model=SocialSourceOut, status_code=201)
def add_social(body: SocialSourceIn, request: Request,
               ctx: AuthContext = Depends(require("sources:write")),
               db: Session = Depends(get_db)) -> SocialSourceOut:
    from app.modules.billing.entitlements import ensure_active, ensure_frequency
    ensure_active(db, ctx.tenant_id)
    project = db.get(MonitoringProject, body.project_id)
    if project is None or project.tenant_id != ctx.tenant_id:
        raise NotFound("Monitoring project not found")
    if body.frequency_minutes is not None:
        ensure_frequency(db, ctx.tenant_id, body.frequency_minutes)
    if body.kind in QUERY_KINDS and not (body.query or body.url).strip():
        raise AppError("A search query or name is required", code="invalid_query",
                       status_code=422)
    get_limiter().hit(f"social:add:{ctx.tenant_id}", 60, 3600)
    url, query = normalize(body.kind, body.url, body.query)
    youtube = body.kind in YOUTUBE_KINDS
    src = Source(id=uuid.uuid4(), tenant_id=ctx.tenant_id, kind=body.kind, url=url,
                 domain=urlsplit(url).hostname.removeprefix("www.") if urlsplit(url).hostname
                 else "", name=body.name.strip(),
                 category="video" if youtube or body.kind.startswith("tiktok") else "social",
                 connector="youtube" if youtube else "apify", discovered_via="manual",
                 coverage_status="PARTIALLY_AVAILABLE", project_id=project.id,
                 match_mode=body.match_mode, query=query,
                 frequency_minutes=body.frequency_minutes, next_sync_at=datetime.now(UTC),
                 sync_state={})
    db.add(src)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict("This source is already followed by this project",
                       code="duplicate") from e
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="social_source.added",
           target_type="source", target_id=src.id,
           meta={"kind": src.kind, "url": url, "project_id": str(project.id),
                 "match_mode": src.match_mode}, request=request)
    db.commit()
    return _out(db, src)


@router.patch("/social-sources/{source_id}", response_model=SocialSourceOut)
def update_social(source_id: uuid.UUID, body: SocialSourcePatch, request: Request,
                  ctx: AuthContext = Depends(require("sources:write")),
                  db: Session = Depends(get_db)) -> SocialSourceOut:
    from app.modules.billing.entitlements import ensure_frequency
    src = _get(db, ctx, source_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("frequency_minutes") is not None:
        ensure_frequency(db, ctx.tenant_id, changes["frequency_minutes"])
    for k, v in changes.items():
        setattr(src, k, v.strip() if isinstance(v, str) else v)
    if changes.get("enabled") is True and src.next_sync_at is None:
        src.next_sync_at = datetime.now(UTC)
    src.updated_at = datetime.now(UTC)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="social_source.updated", target_type="source", target_id=src.id,
           meta={"fields": sorted(changes)}, request=request)
    db.commit()
    return _out(db, src)


@router.delete("/social-sources/{source_id}", status_code=204)
def delete_social(source_id: uuid.UUID, request: Request,
                  ctx: AuthContext = Depends(require("sources:write")),
                  db: Session = Depends(get_db)) -> Response:
    src = _get(db, ctx, source_id)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="social_source.deleted", target_type="source", target_id=src.id,
           meta={"kind": src.kind, "url": src.url}, request=request)
    db.delete(src)          # mentions stay (source_id becomes NULL); sync history goes
    db.commit()
    return Response(status_code=204)


@router.post("/social-sources/{source_id}/sync", status_code=202)
def sync_now(source_id: uuid.UUID, request: Request,
             ctx: AuthContext = Depends(require("sources:write")),
             db: Session = Depends(get_db)) -> dict[str, str]:
    from app.modules.billing.entitlements import ensure_active
    from app.social.sync import queue_manual
    src = _get(db, ctx, source_id)
    ensure_active(db, ctx.tenant_id)
    if not src.enabled:
        raise AppError("This source is disabled", code="source_disabled", status_code=409)
    running = db.scalar(select(SourceSync.id).where(
        SourceSync.source_id == src.id, SourceSync.status.in_(("queued", "running"))).limit(1))
    if running:
        raise Conflict("A synchronisation is already running", code="sync_running")
    get_limiter().hit(f"social:sync:{src.id}", 6, 3600)
    queue_manual(db, src)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="social_source.sync_requested", target_type="source", target_id=src.id,
           meta={"kind": src.kind, "metric": metric_of(src.kind)}, request=request)
    db.commit()
    return {"status": "queued"}


@router.get("/social-sources/{source_id}/syncs", response_model=list[SyncOut])
def list_syncs(source_id: uuid.UUID, ctx: AuthContext = Depends(require("sources:read")),
               db: Session = Depends(get_db),
               limit: int = Query(default=20, ge=1, le=100)) -> list[SyncOut]:
    src = _get(db, ctx, source_id)
    rows = db.scalars(select(SourceSync).where(SourceSync.source_id == src.id)
                      .order_by(SourceSync.created_at.desc()).limit(limit))
    return [_sync_out(s) for s in rows]
