"""Aggregations over mentions + analysis. Raw SQL with bound parameters (no string-built
values); RLS applies on top of the explicit tenant filter.

Common rules (docs/REPUTATION_METHODOLOGY.md):
- reposts (duplicate_of) are excluded: one story counts once;
- mentions judged not about the brand (homonyms; human corrections first) are excluded;
- the effective sentiment is the human correction if any, else the AI label;
- a mention's day is its publication date, else its collection date, in the analytics timezone;
- spike and crisis detection only use mentions with a known publication date, so a first
  collection of older articles cannot look like a sudden surge.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings

SENT = "coalesce(a.corrected_sentiment, a.sentiment)"
DAY = "(timezone(:tz, coalesce(m.published_at, m.collected_at)))::date"
BASE = """
FROM mentions m
LEFT JOIN mention_analysis a ON a.mention_id = m.id
WHERE m.tenant_id = :tenant AND m.duplicate_of IS NULL
  AND coalesce(a.corrected_about_target, a.about_target, 'yes') <> 'no'
  AND (CAST(:brand AS uuid) IS NULL OR m.brand_id = CAST(:brand AS uuid))
  AND m.entity_type = ANY(CAST(:etypes AS text[]))
  AND (CAST(:entity AS text) IS NULL OR m.entity_name = CAST(:entity AS text))
  AND (NOT CAST(:dated AS boolean) OR m.published_at IS NOT NULL)
"""
COUNTS = f"""
  count(m.id) AS total,
  count(*) FILTER (WHERE {SENT} = 'positive') AS positive,
  count(*) FILTER (WHERE {SENT} = 'negative') AS negative,
  count(*) FILTER (WHERE {SENT} = 'neutral') AS neutral,
  count(*) FILTER (WHERE {SENT} = 'mixed') AS mixed,
  count(*) FILTER (WHERE {SENT} = 'UNCERTAIN') AS uncertain,
  count(m.id) FILTER (WHERE a.status IS DISTINCT FROM 'done') AS unanalysed
"""


def _params(tenant_id: uuid.UUID, brand_id: uuid.UUID | None, *, etypes=("brand",),  # noqa: ANN001
            entity: str | None = None, dated: bool = False, **extra: Any) -> dict[str, Any]:
    return {"tenant": tenant_id, "brand": brand_id, "etypes": list(etypes), "entity": entity,
            "dated": dated, "tz": get_settings().analytics_timezone, **extra}


def daily(db: Session, tenant_id: uuid.UUID, brand_id: uuid.UUID | None, days: int, *,
          etypes=("brand",), entity: str | None = None, dated: bool = False,  # noqa: ANN001
          end: datetime | None = None) -> list[dict[str, Any]]:
    """One row per day (oldest → newest, zero-filled), ending today or at `end`."""
    sql = f"""
    WITH bounds AS (
      SELECT (timezone(:tz, coalesce(CAST(:end AS timestamptz), now())))::date AS last),
    days AS (SELECT generate_series(last - (:n - 1), last, interval '1 day')::date AS day
             FROM bounds),
    x AS (SELECT {DAY} AS day, m.id, a.status, a.sentiment, a.corrected_sentiment
          {BASE})
    SELECT d.day,
      count(x.id) AS total,
      count(*) FILTER (WHERE coalesce(x.corrected_sentiment, x.sentiment) = 'positive') AS positive,
      count(*) FILTER (WHERE coalesce(x.corrected_sentiment, x.sentiment) = 'negative') AS negative,
      count(*) FILTER (WHERE coalesce(x.corrected_sentiment, x.sentiment) = 'neutral') AS neutral,
      count(*) FILTER (WHERE coalesce(x.corrected_sentiment, x.sentiment) = 'mixed') AS mixed,
      count(*) FILTER (WHERE coalesce(x.corrected_sentiment, x.sentiment) = 'UNCERTAIN')
        AS uncertain,
      count(x.id) FILTER (WHERE x.status IS DISTINCT FROM 'done') AS unanalysed
    FROM days d LEFT JOIN x ON x.day = d.day
    GROUP BY d.day ORDER BY d.day
    """
    rows = db.execute(text(sql), _params(tenant_id, brand_id, etypes=etypes, entity=entity,
                                         dated=dated, n=days, end=end)).mappings().all()
    return [dict(r) for r in rows]


def totals(db: Session, tenant_id: uuid.UUID, brand_id: uuid.UUID | None, start: datetime,
           end: datetime, *, etypes=("brand",)) -> dict[str, int]:  # noqa: ANN001
    sql = f"""SELECT {COUNTS}, count(DISTINCT m.domain) AS domains,
                     count(*) FILTER (WHERE m.published_at IS NULL) AS undated
              {BASE} AND coalesce(m.published_at, m.collected_at) >= :start
                     AND coalesce(m.published_at, m.collected_at) < :end"""
    return dict(db.execute(text(sql), _params(tenant_id, brand_id, etypes=etypes, start=start,
                                               end=end)).mappings().one())


def entities(db: Session, tenant_id: uuid.UUID, brand_id: uuid.UUID | None, start: datetime,
             end: datetime) -> list[dict[str, Any]]:
    """Per entity (the brand, its products, its competitors): volume and sentiment."""
    sql = f"""SELECT m.entity_type, m.entity_name, {COUNTS}
              {BASE} AND coalesce(m.published_at, m.collected_at) >= :start
                     AND coalesce(m.published_at, m.collected_at) < :end
              GROUP BY m.entity_type, m.entity_name ORDER BY count(m.id) DESC"""
    p = _params(tenant_id, brand_id, etypes=("brand", "product", "competitor"), start=start,
                end=end)
    return [dict(r) for r in db.execute(text(sql), p).mappings()]


def topic_series(db: Session, tenant_id: uuid.UUID, brand_id: uuid.UUID | None,
                 days: int) -> dict[str, list[int]]:
    sql = f"""
    WITH bounds AS (SELECT (timezone(:tz, now()))::date AS last),
    u AS (SELECT {DAY} AS day, lower(tp) AS topic
          FROM mentions m JOIN mention_analysis a ON a.mention_id = m.id
          CROSS JOIN LATERAL unnest(a.topics) AS tp
          WHERE m.tenant_id = :tenant AND m.duplicate_of IS NULL AND a.status = 'done'
            AND coalesce(a.corrected_about_target, a.about_target, 'yes') <> 'no'
            AND (CAST(:brand AS uuid) IS NULL OR m.brand_id = CAST(:brand AS uuid))
            AND m.entity_type = 'brand'
            AND (NOT CAST(:dated AS boolean) OR m.published_at IS NOT NULL))
    SELECT u.topic, (bounds.last - u.day) AS age, count(*) AS n
    FROM u, bounds WHERE u.day > bounds.last - :n AND u.day <= bounds.last
    GROUP BY u.topic, age
    """
    out: dict[str, list[int]] = {}
    for r in db.execute(text(sql), _params(tenant_id, brand_id, dated=True, n=days)).mappings():
        out.setdefault(r["topic"], [0] * days)[days - 1 - r["age"]] = r["n"]
    return out


def negative_today_details(db: Session, tenant_id: uuid.UUID, brand_id: uuid.UUID,
                           entity: str | None = None,
                           etypes=("brand",)) -> dict[str, Any]:  # noqa: ANN001
    """Distinct sources and escalation-intent share among today's dated negative mentions."""
    sql = f"""SELECT count(DISTINCT m.domain) AS domains, count(*) AS n,
                     count(*) FILTER (WHERE coalesce(a.corrected_intents, a.intents)
                                      && ARRAY['boycott_intent','escalation_threat']) AS escalation
              {BASE} AND {SENT} = 'negative' AND {DAY} = (timezone(:tz, now()))::date"""
    r = db.execute(text(sql), _params(tenant_id, brand_id, etypes=etypes, entity=entity,
                                      dated=True)).mappings().one()
    return {"domains": r["domains"], "escalation_share":
            round(r["escalation"] / r["n"], 3) if r["n"] else 0.0}


def top_mentions(db: Session, tenant_id: uuid.UUID, brand_id: uuid.UUID | None,
                 start: datetime, end: datetime, sentiment: str, limit: int = 5
                 ) -> list[dict[str, Any]]:
    sql = f"""SELECT m.id, m.title, m.original_text, m.url, m.domain, m.published_at,
                     m.collected_at, m.seen_count, a.agreement,
                     coalesce(a.corrected_intents, a.intents) AS intents
              {BASE} AND {SENT} = :sentiment
                     AND coalesce(m.published_at, m.collected_at) >= :start
                     AND coalesce(m.published_at, m.collected_at) < :end
              ORDER BY m.seen_count DESC, a.agreement DESC NULLS LAST, m.collected_at DESC
              LIMIT :limit"""
    return [dict(r) for r in db.execute(text(sql), _params(
        tenant_id, brand_id, start=start, end=end, sentiment=sentiment, limit=limit)).mappings()]
