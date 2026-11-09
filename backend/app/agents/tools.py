"""Tools: thin, read-mostly wrappers over existing services, each with the permission it needs.
Agents can only reach the database through these (and only those in their allowlist).
All queries run in the task's tenant context, so row-level security applies as well."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text

from app.agents.base import AgentContext, AgentError
from app.analytics import queries as q
from app.analytics.stats import reputation_index, share_of_voice
from app.core.config import get_settings
from app.db.models import (
    Brand,
    BrandAlias,
    CaptchaEvent,
    ConnectorPolicy,
    CrisisEvent,
    Mention,
    MentionAnalysis,
    MonitoringProject,
    MonitoringRun,
    Source,
)


@dataclass(frozen=True)
class Tool:
    name: str
    permission: str
    fn: Callable[..., Any]
    description: str


TOOLS: dict[str, Tool] = {}


def tool(name: str, permission: str, description: str) -> Callable[[Callable[..., Any]],
                                                                    Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOLS[name] = Tool(name, permission, fn, description)
        return fn
    return deco


def _window(days: int) -> tuple[datetime, datetime, datetime]:
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    return start - timedelta(days=days), start, end


def _brand(ctx: AgentContext, brand_id: uuid.UUID | None) -> Brand | None:
    if brand_id is None:
        return None
    b = ctx.db.scalars(select(Brand).where(Brand.id == brand_id,
                                           Brand.tenant_id == ctx.tenant_id)).one_or_none()
    if b is None:
        raise AgentError("brand_not_found")
    return b


# ------------------------------------------------------------------------------- brands
@tool("brands.get", "brands:read", "Brand name, aliases, response tone and guidelines")
def brands_get(ctx: AgentContext, brand_id: uuid.UUID) -> dict[str, Any]:
    b = _brand(ctx, brand_id)
    assert b is not None
    aliases = list(ctx.db.scalars(select(BrandAlias.alias).where(BrandAlias.brand_id == b.id)))
    return {"id": b.id, "name": b.name, "aliases": aliases, "tone": b.response_tone,
            "guidelines": b.response_guidelines, "language": b.primary_language}


@tool("brands.list", "brands:read", "Active brands of the organization")
def brands_list(ctx: AgentContext) -> list[dict[str, Any]]:
    return [{"id": b.id, "name": b.name} for b in ctx.db.scalars(
        select(Brand).where(Brand.tenant_id == ctx.tenant_id, Brand.status == "active")
        .order_by(Brand.name).limit(100))]


# --------------------------------------------------------------------------- monitoring
@tool("monitoring.projects", "monitoring:read", "Monitoring projects with their last run")
def monitoring_projects(ctx: AgentContext, brand_id: uuid.UUID | None) -> list[dict[str, Any]]:
    stmt = select(MonitoringProject).where(MonitoringProject.tenant_id == ctx.tenant_id)
    if brand_id:
        stmt = stmt.where(MonitoringProject.brand_id == brand_id)
    out = []
    for p in ctx.db.scalars(stmt.limit(50)):
        last = ctx.db.scalars(select(MonitoringRun).where(MonitoringRun.project_id == p.id)
                              .order_by(MonitoringRun.queued_at.desc()).limit(1)).one_or_none()
        failed = ctx.db.scalar(select(func.count()).select_from(MonitoringRun).where(
            MonitoringRun.project_id == p.id, MonitoringRun.status == "failed",
            MonitoringRun.queued_at > datetime.now(UTC) - timedelta(days=7))) or 0
        out.append({"id": p.id, "name": p.name, "status": p.status,
                    "frequency_minutes": p.frequency_minutes,
                    "last_run_status": last.status if last else None,
                    "last_run_at": last.queued_at if last else None, "failed_7d": failed})
    return out


# ---------------------------------------------------------------------------- analytics
@tool("analytics.window", "mentions:read", "Totals for the period and the one before")
def analytics_window(ctx: AgentContext, brand_id: uuid.UUID | None, days: int
                     ) -> dict[str, Any]:
    p0, start, end = _window(days)
    cur = q.totals(ctx.db, ctx.tenant_id, brand_id, start, end)
    prev = q.totals(ctx.db, ctx.tenant_id, brand_id, p0, start)
    m = get_settings().reputation_min_sample

    def rep(t: dict[str, int]) -> dict[str, Any]:
        r = reputation_index(t["positive"], t["negative"], t["neutral"], t["mixed"], m)
        return {"value": r.value, "sample": r.sample}
    return {"days": days, "current": cur, "previous": prev, "reputation": rep(cur),
            "previous_reputation": rep(prev), "min_sample": m}


@tool("analytics.entities", "mentions:read",
      "Mentions per brand/product/competitor, share of voice")
def analytics_entities(ctx: AgentContext, brand_id: uuid.UUID | None, days: int
                       ) -> list[dict[str, Any]]:
    _, start, end = _window(days)
    ents = q.entities(ctx.db, ctx.tenant_id, brand_id, start, end)
    sov = share_of_voice({e["entity_name"]: e["total"] for e in ents
                          if e["entity_type"] in ("brand", "competitor")})
    m = get_settings().reputation_min_sample
    out = []
    for e in ents:
        r = reputation_index(e["positive"], e["negative"], e["neutral"], e["mixed"], m)
        out.append({**dict(e), "share": sov.get(e["entity_name"]), "reputation": r.value})
    return out


@tool("analytics.languages", "mentions:read", "Language, script and code-switching mix")
def analytics_languages(ctx: AgentContext, brand_id: uuid.UUID | None, days: int
                        ) -> dict[str, Any]:
    _, start, _end = _window(days)
    rows = ctx.db.execute(text("""
        SELECT coalesce(a.language, 'unknown') AS language, a.script, a.code_switching,
               count(*) AS n
        FROM mentions m JOIN mention_analysis a ON a.mention_id = m.id
        WHERE m.tenant_id = :t AND m.duplicate_of IS NULL AND m.collected_at >= :s
          AND (CAST(:b AS uuid) IS NULL OR m.brand_id = CAST(:b AS uuid))
        GROUP BY 1, 2, 3
    """), {"t": ctx.tenant_id, "b": brand_id, "s": start}).mappings()
    langs: dict[str, int] = {}
    scripts: dict[str, int] = {}
    switching = total = 0
    for r in rows:
        langs[r["language"]] = langs.get(r["language"], 0) + r["n"]
        if r["script"]:
            scripts[r["script"]] = scripts.get(r["script"], 0) + r["n"]
        switching += r["n"] if r["code_switching"] else 0
        total += r["n"]
    return {"total": total, "languages": langs, "scripts": scripts,
            "code_switching": switching}


@tool("analytics.about_target", "mentions:read", "Homonym / off-topic decisions")
def analytics_about_target(ctx: AgentContext, brand_id: uuid.UUID | None, days: int
                           ) -> dict[str, int]:
    _, start, _end = _window(days)
    rows = ctx.db.execute(text("""
        SELECT coalesce(a.corrected_about_target, a.about_target, 'unanalysed') AS v, count(*)
        FROM mentions m LEFT JOIN mention_analysis a ON a.mention_id = m.id
        WHERE m.tenant_id = :t AND m.duplicate_of IS NULL AND m.collected_at >= :s
          AND (CAST(:b AS uuid) IS NULL OR m.brand_id = CAST(:b AS uuid))
        GROUP BY 1
    """), {"t": ctx.tenant_id, "b": brand_id, "s": start}).all()
    return {v: n for v, n in rows}


@tool("nlp.validation", "mentions:read", "Which language tasks are validated on the benchmark")
def nlp_validation(ctx: AgentContext) -> dict[str, Any]:
    from app.nlp.service import validation_status
    st = validation_status(ctx.db)
    return {"tasks": {k: v["validated"] for k, v in st["tasks"].items()},
            "ai_configured": st["ai_configured"]}


# ------------------------------------------------------------------- trends and crises
@tool("trends.recent", "alerts:read", "Emerging topics detected recently")
def trends_recent(ctx: AgentContext, brand_id: uuid.UUID | None, days: int
                  ) -> list[dict[str, Any]]:
    return [dict(r) for r in ctx.db.execute(text("""
        SELECT t.topic, t.day, t.count, t.expected::float AS expected, t.z::float AS z,
               b.name AS brand
        FROM trend_events t JOIN brands b ON b.id = t.brand_id AND b.tenant_id = t.tenant_id
        WHERE t.tenant_id = :t AND (CAST(:b AS uuid) IS NULL OR t.brand_id = CAST(:b AS uuid))
          AND t.day >= current_date - :d
        ORDER BY t.z DESC LIMIT 10
    """), {"t": ctx.tenant_id, "b": brand_id, "d": days}).mappings()]


@tool("crises.list", "alerts:read", "Active and recent potential crises")
def crises_list(ctx: AgentContext, brand_id: uuid.UUID | None, days: int
                ) -> list[dict[str, Any]]:
    stmt = select(CrisisEvent, Brand.name).join(
        Brand, (Brand.id == CrisisEvent.brand_id) & (Brand.tenant_id == CrisisEvent.tenant_id)
    ).where(CrisisEvent.tenant_id == ctx.tenant_id,
            (CrisisEvent.status == "active")
            | (CrisisEvent.last_detected_at > datetime.now(UTC) - timedelta(days=days)))
    if brand_id:
        stmt = stmt.where(CrisisEvent.brand_id == brand_id)
    return [{"id": c.id, "brand": n, "status": c.status, "severity": c.severity,
             "started_at": c.started_at, "peak_negative": c.peak_negative,
             "metrics": c.metrics} for c, n in ctx.db.execute(stmt.limit(20))]


# ------------------------------------------------------------------------------ mentions
@tool("mentions.top", "mentions:read", "Most repeated mentions with a given sentiment")
def mentions_top(ctx: AgentContext, brand_id: uuid.UUID | None, sentiment: str, days: int,
                 limit: int = 5) -> list[dict[str, Any]]:
    _, start, end = _window(days)
    return [dict(r) for r in q.top_mentions(ctx.db, ctx.tenant_id, brand_id, start, end,
                                            sentiment, min(limit, 10))]


@tool("mentions.get", "mentions:read", "One mention with its analysis")
def mentions_get(ctx: AgentContext, mention_id: uuid.UUID) -> dict[str, Any]:
    m = ctx.db.scalars(select(Mention).where(Mention.id == mention_id,
                                             Mention.tenant_id == ctx.tenant_id)).one_or_none()
    if m is None:
        raise AgentError("mention_not_found")
    a = ctx.db.get(MentionAnalysis, m.id)
    return {"id": m.id, "brand_id": m.brand_id, "title": m.title, "text": m.original_text,
            "url": m.url, "domain": m.domain, "published_at": m.published_at,
            "entity_name": m.entity_name,
            "sentiment": (a.corrected_sentiment or a.sentiment) if a else None,
            "language": a.language if a else None,
            "intents": list((a.corrected_intents or a.intents) or []) if a else []}


# ------------------------------------------------------------------------------- sources
@tool("sources.coverage", "sources:read", "Sources by coverage state, failing sources")
def sources_coverage(ctx: AgentContext) -> dict[str, Any]:
    rows = ctx.db.execute(select(Source.coverage_status, func.count()).where(
        Source.tenant_id == ctx.tenant_id, Source.enabled).group_by(Source.coverage_status))
    failing = [{"id": s.id, "domain": s.domain, "kind": s.kind, "coverage": s.coverage_status,
                "errors": s.consecutive_errors, "last_challenge": s.last_challenge_kind}
               for s in ctx.db.scalars(select(Source).where(
                   Source.tenant_id == ctx.tenant_id, Source.enabled,
                   Source.coverage_status.in_(("UNAVAILABLE", "CONNECTOR_ERROR", "RESTRICTED")))
                   .order_by(Source.mentions_count.desc()).limit(10))]
    return {"by_coverage": {k: n for k, n in rows}, "failing": failing}


@tool("sources.discovery", "sources:read", "Disabled or unchecked sources worth enabling")
def sources_discovery(ctx: AgentContext) -> dict[str, Any]:
    feeds = [{"id": s.id, "url": s.url, "domain": s.domain} for s in ctx.db.scalars(
        select(Source).where(Source.tenant_id == ctx.tenant_id, Source.kind == "rss",
                             Source.enabled.is_(False),
                             Source.discovered_via == "feed_discovery")
        .order_by(Source.created_at.desc()).limit(5))]
    unchecked = [{"id": s.id, "domain": s.domain, "mentions": s.mentions_count}
                 for s in ctx.db.scalars(select(Source).where(
                     Source.tenant_id == ctx.tenant_id, Source.kind == "website",
                     Source.enabled, Source.last_checked_at.is_(None),
                     Source.mentions_count >= 2).order_by(Source.mentions_count.desc()).limit(5))]
    return {"disabled_feeds": feeds, "unchecked_websites": unchecked}


@tool("challenges.recent", "browser:read", "Anti-bot challenges met recently")
def challenges_recent(ctx: AgentContext, days: int) -> dict[str, int]:
    rows = ctx.db.execute(select(CaptchaEvent.kind, func.count()).where(
        CaptchaEvent.tenant_id == ctx.tenant_id,
        CaptchaEvent.detected_at > datetime.now(UTC) - timedelta(days=days))
        .group_by(CaptchaEvent.kind))
    return {k: n for k, n in rows}


@tool("compliance.connectors", "sources:read", "Compliance review status of each connector")
def compliance_connectors(ctx: AgentContext) -> dict[str, str]:
    return {p.key: p.review_status for p in ctx.db.scalars(select(ConnectorPolicy))}
