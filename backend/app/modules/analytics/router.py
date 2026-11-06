"""Reputation analytics: volume and sentiment over time, reputation index, share of voice,
emerging topics and crises. Computed on demand from mentions + analyses."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analytics import queries as q
from app.analytics.stats import reputation_index, share_of_voice
from app.api.deps import AuthContext, require
from app.core.config import get_settings
from app.core.errors import NotFound
from app.db.models import Brand, CrisisEvent
from app.db.session import get_db

router = APIRouter(tags=["analytics"])


class Reputation(BaseModel):
    value: float | None
    net_sentiment: float | None
    sample: int
    min_sample: int


class EntityStat(BaseModel):
    entity_type: str
    entity_name: str
    total: int
    positive: int
    negative: int
    neutral: int
    mixed: int
    uncertain: int
    unanalysed: int
    share_of_voice: float | None
    reputation: Reputation


class TopicOut(BaseModel):
    brand_id: uuid.UUID
    brand: str
    topic: str
    day: date
    count: int
    expected: float
    z: float


class CrisisOut(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    brand: str
    status: str
    severity: str
    started_at: datetime
    last_detected_at: datetime
    resolved_at: datetime | None
    peak_negative: int
    metrics: dict[str, Any]


class Overview(BaseModel):
    brand_id: uuid.UUID | None
    days: int
    timezone: str
    period_start: datetime
    period_end: datetime
    series: list[dict[str, Any]]
    totals: dict[str, int]
    previous_totals: dict[str, int]
    reputation: Reputation
    previous_reputation: Reputation
    entities: list[EntityStat]
    topics: list[TopicOut]
    active_crises: list[CrisisOut]


def _rep(t: dict[str, int]) -> Reputation:
    m = get_settings().reputation_min_sample
    r = reputation_index(t["positive"], t["negative"], t["neutral"], t["mixed"], m)
    return Reputation(value=r.value, net_sentiment=r.net_sentiment, sample=r.sample, min_sample=m)


def _check_brand(db: Session, ctx: AuthContext, brand_id: uuid.UUID | None) -> None:
    if brand_id and db.scalar(select(Brand.id).where(Brand.id == brand_id,
                                                     Brand.tenant_id == ctx.tenant_id)) is None:
        raise NotFound("Brand not found")


def _crises(db: Session, ctx: AuthContext, brand_id: uuid.UUID | None,
            status: str | None, limit: int) -> list[CrisisOut]:
    stmt = select(CrisisEvent, Brand.name).join(
        Brand, (Brand.id == CrisisEvent.brand_id) & (Brand.tenant_id == CrisisEvent.tenant_id)
    ).where(CrisisEvent.tenant_id == ctx.tenant_id)
    if brand_id:
        stmt = stmt.where(CrisisEvent.brand_id == brand_id)
    if status:
        stmt = stmt.where(CrisisEvent.status == status)
    rows = db.execute(stmt.order_by(CrisisEvent.started_at.desc()).limit(limit))
    return [CrisisOut(id=c.id, brand_id=c.brand_id, brand=name, status=c.status,
                      severity=c.severity, started_at=c.started_at,
                      last_detected_at=c.last_detected_at, resolved_at=c.resolved_at,
                      peak_negative=c.peak_negative, metrics=c.metrics) for c, name in rows]


@router.get("/analytics/overview", response_model=Overview)
def overview(ctx: AuthContext = Depends(require("mentions:read")),
             db: Session = Depends(get_db), brand_id: uuid.UUID | None = None,
             days: int = Query(default=30, ge=1, le=365)) -> Overview:
    _check_brand(db, ctx, brand_id)
    s = get_settings()
    series = q.daily(db, ctx.tenant_id, brand_id, days)
    # Period boundaries follow the series' calendar days in the analytics timezone.
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(s.analytics_timezone)
    first = series[0]["day"]
    start = datetime(first.year, first.month, first.day, tzinfo=tz).astimezone(UTC)
    end = start + timedelta(days=days)
    cur = q.totals(db, ctx.tenant_id, brand_id, start, end)
    prev = q.totals(db, ctx.tenant_id, brand_id, start - timedelta(days=days), start)
    ents = q.entities(db, ctx.tenant_id, brand_id, start, end)
    sov = share_of_voice({e["entity_name"]: e["total"] for e in ents
                          if e["entity_type"] in ("brand", "competitor")})
    entities = [EntityStat(**{k: e[k] for k in ("entity_type", "entity_name", "total",
                                                "positive", "negative", "neutral", "mixed",
                                                "uncertain", "unanalysed")},
                           share_of_voice=sov.get(e["entity_name"])
                           if e["entity_type"] != "product" else None,
                           reputation=_rep(e)) for e in ents]
    topics = [TopicOut(**dict(r)) for r in db.execute(text("""
        SELECT t.brand_id, b.name AS brand, t.topic, t.day, t.count,
               t.expected::float AS expected, t.z::float AS z
        FROM trend_events t JOIN brands b ON b.id = t.brand_id AND b.tenant_id = t.tenant_id
        WHERE t.tenant_id = :t AND (CAST(:b AS uuid) IS NULL OR t.brand_id = CAST(:b AS uuid))
          AND t.day >= :first
        ORDER BY t.day DESC, t.z DESC LIMIT 30
    """), {"t": ctx.tenant_id, "b": brand_id, "first": first}).mappings()]
    return Overview(brand_id=brand_id, days=days, timezone=s.analytics_timezone,
                    period_start=start, period_end=end, series=series, totals=cur,
                    previous_totals=prev, reputation=_rep(cur), previous_reputation=_rep(prev),
                    entities=entities, topics=topics,
                    active_crises=_crises(db, ctx, brand_id, "active", 20))


@router.get("/crises", response_model=list[CrisisOut])
def list_crises(ctx: AuthContext = Depends(require("alerts:read")),
                db: Session = Depends(get_db), brand_id: uuid.UUID | None = None,
                status: Literal["active", "resolved"] | None = None,
                limit: int = Query(default=50, ge=1, le=200)) -> list[CrisisOut]:
    _check_brand(db, ctx, brand_id)
    return _crises(db, ctx, brand_id, status, limit)
