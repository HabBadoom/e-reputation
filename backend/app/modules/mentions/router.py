"""Collected mentions with full provenance. Read-only in Milestone 3 (corrections arrive with
the NLP analysis in Milestone 4)."""

import csv
import io
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.core.errors import NotFound
from app.db.models import Brand, Mention, MonitoringProject, Source
from app.db.session import get_db

router = APIRouter(prefix="/mentions", tags=["mentions"])

Verification = Literal["verified_on_page", "snippet_only", "not_found_on_page",
                       "page_unavailable", "blocked_by_robots", "feed_item"]


class MentionOut(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    brand_name: str
    project_id: uuid.UUID | None
    entity_type: str
    entity_name: str
    url: str
    domain: str
    title: str
    original_text: str
    matched_terms: list[str]
    publisher: str | None
    published_at: datetime | None
    published_at_source: str | None
    collected_at: datetime
    last_seen_at: datetime
    seen_count: int
    collection_method: str
    verification_status: str
    is_repost: bool
    language: str | None
    analysed: bool          # False until Milestone 4: no sentiment is shown or invented


class MentionDetail(MentionOut):
    snippet: str
    canonical_url: str
    connector: str
    connector_version: str
    content_hash: str
    duplicate_of: uuid.UUID | None
    source: dict[str, Any] | None
    project_name: str | None
    provider_fields: dict[str, Any]


class MentionPage(BaseModel):
    items: list[MentionOut]
    total: int
    limit: int
    offset: int


def _base(ctx: AuthContext) -> Select:
    return (select(Mention, Brand.name).join(Brand, Brand.id == Mention.brand_id)
            .where(Mention.tenant_id == ctx.tenant_id))


def _filtered(ctx: AuthContext, brand_id, project_id, entity_type, verification, domain,  # noqa: ANN001
              since, until, q, include_reposts) -> Select:
    stmt = _base(ctx)
    if brand_id:
        stmt = stmt.where(Mention.brand_id == brand_id)
    if project_id:
        stmt = stmt.where(Mention.project_id == project_id)
    if entity_type:
        stmt = stmt.where(Mention.entity_type == entity_type)
    if verification:
        stmt = stmt.where(Mention.verification_status == verification)
    if domain:
        stmt = stmt.where(Mention.domain == domain.lower())
    if since:
        stmt = stmt.where(func.coalesce(Mention.published_at, Mention.collected_at) >= since)
    if until:
        stmt = stmt.where(func.coalesce(Mention.published_at, Mention.collected_at) < until)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(Mention.title.ilike(like) | Mention.original_text.ilike(like))
    if not include_reposts:
        stmt = stmt.where(Mention.duplicate_of.is_(None))
    return stmt


def _out(m: Mention, brand_name: str) -> MentionOut:
    return MentionOut(
        id=m.id, brand_id=m.brand_id, brand_name=brand_name, project_id=m.project_id,
        entity_type=m.entity_type, entity_name=m.entity_name, url=m.url, domain=m.domain,
        title=m.title, original_text=m.original_text, matched_terms=list(m.matched_terms or []),
        publisher=m.publisher, published_at=m.published_at,
        published_at_source=m.published_at_source, collected_at=m.collected_at,
        last_seen_at=m.last_seen_at, seen_count=m.seen_count,
        collection_method=m.collection_method, verification_status=m.verification_status,
        is_repost=m.duplicate_of is not None, language=m.language, analysed=False)


class Filters:
    def __init__(self, brand_id: uuid.UUID | None = None, project_id: uuid.UUID | None = None,
                 entity_type: Literal["brand", "product", "competitor"] | None = None,
                 verification: Verification | None = None,
                 domain: str | None = Query(default=None, max_length=255),
                 since: datetime | None = None, until: datetime | None = None,
                 q: str | None = Query(default=None, max_length=200),
                 include_reposts: bool = False) -> None:
        self.args = (brand_id, project_id, entity_type, verification, domain, since, until, q,
                     include_reposts)


@router.get("", response_model=MentionPage)
def list_mentions(ctx: AuthContext = Depends(require("mentions:read")),
                  db: Session = Depends(get_db), f: Filters = Depends(),
                  limit: int = Query(default=25, ge=1, le=100),
                  offset: int = Query(default=0, ge=0, le=100_000)) -> MentionPage:
    stmt = _filtered(ctx, *f.args)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(
        func.coalesce(Mention.published_at, Mention.collected_at).desc(), Mention.id)
        .limit(limit).offset(offset)).all()
    return MentionPage(items=[_out(m, bn) for m, bn in rows], total=total, limit=limit,
                       offset=offset)


def _cell(v: object) -> str:
    """Neutralise spreadsheet formulas (CSV injection): third-party text is untrusted."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/export.csv")
def export_mentions(ctx: AuthContext = Depends(require("mentions:read")),
                    db: Session = Depends(get_db), f: Filters = Depends()) -> Response:
    rows = db.execute(_filtered(ctx, *f.args).order_by(Mention.collected_at.desc())
                      .limit(5000)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["collected_at", "published_at", "published_at_source", "brand", "entity_type",
                "entity", "domain", "url", "title", "excerpt", "matched_terms", "method",
                "verification", "is_repost"])
    for m, bn in rows:
        w.writerow([_cell(x) for x in (
            m.collected_at.isoformat(), m.published_at.isoformat() if m.published_at else "",
            m.published_at_source or "", bn, m.entity_type, m.entity_name, m.domain, m.url,
            m.title, m.original_text, "; ".join(m.matched_terms or []), m.collection_method,
            m.verification_status, "yes" if m.duplicate_of else "no")])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"content-disposition": 'attachment; filename="mentions.csv"'})


@router.get("/{mention_id}", response_model=MentionDetail)
def get_mention(mention_id: uuid.UUID, ctx: AuthContext = Depends(require("mentions:read")),
                db: Session = Depends(get_db)) -> MentionDetail:
    row = db.execute(_base(ctx).where(Mention.id == mention_id)).one_or_none()
    if row is None:
        raise NotFound("Mention not found")
    m, bn = row
    src = db.get(Source, m.source_id) if m.source_id else None
    proj = db.get(MonitoringProject, m.project_id) if m.project_id else None
    return MentionDetail(
        **_out(m, bn).model_dump(), snippet=m.snippet, canonical_url=m.canonical_url,
        connector=m.connector, connector_version=m.connector_version,
        content_hash=m.content_hash, duplicate_of=m.duplicate_of,
        source={"id": str(src.id), "kind": src.kind, "domain": src.domain, "name": src.name,
                "coverage_status": src.coverage_status} if src else None,
        project_name=proj.name if proj else None, provider_fields=m.raw or {})
