"""Everything a report says, computed from stored mentions and analyses. No AI-written text:
recommendations are rules on measured values (docs/REPUTATION_METHODOLOGY.md)."""

import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analytics import queries as q
from app.analytics.stats import reputation_index, share_of_voice
from app.core.config import get_settings
from app.db.models import Brand, CrisisEvent, Report, Tenant


def _rep(t: dict[str, int]) -> dict[str, Any]:
    r = reputation_index(t["positive"], t["negative"], t["neutral"], t["mixed"],
                         get_settings().reputation_min_sample)
    return {"value": r.value, "net": r.net_sentiment, "sample": r.sample}


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def build(db: Session, report: Report) -> dict[str, Any]:
    tid, bid = report.tenant_id, report.brand_id
    start, end = report.period_start, report.period_end
    span = end - start
    days = max(1, min(366, math.ceil(span.total_seconds() / 86400)))
    tenant = db.get(Tenant, tid)
    brand = db.get(Brand, bid) if bid else None

    cur = q.totals(db, tid, bid, start, end)
    prev = q.totals(db, tid, bid, start - span, start)
    series = q.daily(db, tid, bid, days, end=end - timedelta(microseconds=1))
    ents = q.entities(db, tid, bid, start, end)
    sov_counts = {e["entity_name"]: e["total"] for e in ents
                  if e["entity_type"] in ("brand", "competitor")}
    sov = share_of_voice(sov_counts)
    competitors = [{**e, "share": sov.get(e["entity_name"], 0.0), "reputation": _rep(e)}
                   for e in ents if e["entity_type"] == "competitor"]
    brand_rows = [{**e, "share": sov.get(e["entity_name"], 0.0), "reputation": _rep(e)}
                  for e in ents if e["entity_type"] == "brand"]
    products = [{**e, "reputation": _rep(e)} for e in ents if e["entity_type"] == "product"]

    top_neg = q.top_mentions(db, tid, bid, start, end, "negative", 5)
    top_pos = q.top_mentions(db, tid, bid, start, end, "positive", 5)

    topics = [dict(r) for r in db.execute(text("""
        SELECT t.topic, t.day, t.count, t.expected, t.z, b.name AS brand
        FROM trend_events t JOIN brands b ON b.id = t.brand_id AND b.tenant_id = t.tenant_id
        WHERE t.tenant_id = :t AND (CAST(:b AS uuid) IS NULL OR t.brand_id = CAST(:b AS uuid))
          AND t.day >= (timezone(:tz, :s))::date AND t.day <= (timezone(:tz, :e))::date
        ORDER BY t.z DESC LIMIT 15
    """), {"t": tid, "b": bid, "s": start, "e": end,
           "tz": get_settings().analytics_timezone}).mappings()]

    cq = select(CrisisEvent, Brand.name).join(
        Brand, (Brand.id == CrisisEvent.brand_id) & (Brand.tenant_id == CrisisEvent.tenant_id)
    ).where(CrisisEvent.tenant_id == tid, CrisisEvent.started_at < end,
            (CrisisEvent.resolved_at.is_(None)) | (CrisisEvent.resolved_at >= start))
    if bid:
        cq = cq.where(CrisisEvent.brand_id == bid)
    crises = [{"brand": name, "severity": c.severity, "status": c.status,
               "started_at": c.started_at, "resolved_at": c.resolved_at,
               "peak_negative": c.peak_negative, "metrics": c.metrics}
              for c, name in db.execute(cq.order_by(CrisisEvent.started_at.desc()))]

    health = db.execute(text("""
        SELECT (SELECT count(*) FROM sources WHERE tenant_id = :t AND enabled) AS sources,
               (SELECT count(*) FROM sources WHERE tenant_id = :t AND enabled
                  AND consecutive_errors >= 3) AS failing_sources,
               (SELECT count(*) FROM monitoring_runs WHERE tenant_id = :t AND status = 'failed'
                  AND finished_at >= :s AND finished_at < :e) AS failed_runs,
               (SELECT count(*) FROM monitoring_runs WHERE tenant_id = :t
                  AND status = 'completed' AND finished_at >= :s AND finished_at < :e) AS runs
    """), {"t": tid, "s": start, "e": end}).mappings().one()

    analysed = cur["positive"] + cur["negative"] + cur["neutral"] + cur["mixed"]
    data: dict[str, Any] = {
        "report_id": str(report.id), "kind": report.kind, "locale": report.locale,
        "organization": tenant.name if tenant else "",
        "brand": brand.name if brand else None,
        "period_start": start, "period_end": end, "days": days,
        "generated_at": datetime.now(start.tzinfo),
        "timezone": get_settings().analytics_timezone,
        "totals": cur, "previous": prev,
        "reputation": _rep(cur), "previous_reputation": _rep(prev),
        "negative_share": _pct(cur["negative"], analysed),
        "previous_negative_share": _pct(prev["negative"],
                                        prev["positive"] + prev["negative"] + prev["neutral"]
                                        + prev["mixed"]),
        "series": series, "brands": brand_rows, "competitors": competitors,
        "products": products, "top_negative": top_neg, "top_positive": top_pos,
        "topics": topics, "crises": crises, "health": dict(health),
    }
    data["recommendations"] = recommendations(data)
    return data


def recommendations(d: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(code, values) pairs, translated by the renderer. Ordered by urgency."""
    out: list[tuple[str, dict[str, Any]]] = []
    t = d["totals"]
    if any(c["status"] == "active" for c in d["crises"]):
        out.append(("crisis_active", {}))
    esc = sum(1 for m in d["top_negative"]
              if set(m.get("intents") or []) & {"boycott_intent", "escalation_threat"})
    if esc:
        out.append(("escalation", {"n": esc}))
    ns, pns = d["negative_share"], d["previous_negative_share"]
    if ns is not None and pns is not None and ns - pns >= 10:
        out.append(("negative_rising", {"now": ns, "before": pns}))
    if d["topics"]:
        out.append(("topics", {"topic": d["topics"][0]["topic"]}))
    if d["brands"] and d["competitors"]:
        mine = max(b["share"] for b in d["brands"])
        lead = max(d["competitors"], key=lambda c: c["share"])
        if lead["share"] > mine:
            out.append(("competitor_ahead", {"competitor": lead["entity_name"]}))
    if t["uncertain"] + t["unanalysed"] >= max(5, 0.2 * max(t["total"], 1)):
        out.append(("review_uncertain", {"n": t["uncertain"] + t["unanalysed"]}))
    if d["health"]["failing_sources"] or d["health"]["failed_runs"]:
        out.append(("fix_collection", {"sources": d["health"]["failing_sources"],
                                       "runs": d["health"]["failed_runs"]}))
    if t["total"] and t["undated"] / t["total"] > 0.3:
        out.append(("undated", {"pct": round(100 * t["undated"] / t["total"])}))
    if d["reputation"]["value"] is None:
        out.append(("small_sample", {"n": d["reputation"]["sample"],
                                     "min": get_settings().reputation_min_sample}))
    if not out:
        out.append(("steady", {}))
    return out


def default_period(kind: str, now: datetime) -> tuple[datetime, datetime]:
    span = {"daily": 1, "weekly": 7, "monthly": 30}.get(kind, 7)
    return now - timedelta(days=span), now
