"""Alert evaluation for one tenant (job `alerts.evaluate`).

1. Maintain derived state for every active brand: crisis lifecycle (`crisis_events`) and
   emerging topics (`trend_events`). This runs whether or not rules exist, so the Crisis and
   Trends screens are filled in.
2. Evaluate each enabled rule into candidate alerts, keep those at or above the rule's minimum
   severity, cap them, and insert them with a de-duplication key (the same fact never alerts
   twice).
3. Deliver new alerts on the rule's channels: in-app (the row itself), one email digest per
   recipient, one signed webhook call per alert (separate jobs, retried).

Every number in an alert is a count or a documented formula (app.analytics.stats); nothing is
estimated by an AI model.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.alerts.texts import describe
from app.analytics import queries as q
from app.analytics.stats import (
    SEVERITY_ORDER,
    assess_crisis,
    detect_spike,
    emerging_topics,
    severity_at_least,
)
from app.core.config import get_settings
from app.db.models import (
    AlertRule,
    Brand,
    Competitor,
    CrisisEvent,
    Tenant,
    Webhook,
)

log = logging.getLogger("repintel.alerts")

ESCALATION_INTENTS = ("boycott_intent", "escalation_threat")


@dataclass
class Candidate:
    event: str                 # texts key (may differ from the rule kind)
    severity: str
    dedup_key: str
    brand_id: uuid.UUID | None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CrisisTransition:
    crisis: CrisisEvent
    change: str                # opened | escalated | resolved
    data: dict[str, Any]


def _today(now: datetime) -> date:
    return now.astimezone(ZoneInfo(get_settings().analytics_timezone)).date()


def _param(rule: AlertRule, key: str, default: Any) -> Any:
    v = (rule.params or {}).get(key)
    return default if v is None else v


# ------------------------------------------------------------------------- derived state
def update_crises(db: Session, tenant_id: uuid.UUID, brands: list[Brand],
                  now: datetime) -> list[CrisisTransition]:
    s = get_settings()
    out: list[CrisisTransition] = []
    for b in brands:
        rows = q.daily(db, tenant_id, b.id, s.trend_baseline_days + 1, dated=True)
        details = q.negative_today_details(db, tenant_id, b.id)
        a = assess_crisis([r["negative"] for r in rows], rows[-1]["total"],
                          domains=details["domains"],
                          escalation_share=details["escalation_share"])
        active = db.scalar(select(CrisisEvent).where(
            CrisisEvent.tenant_id == tenant_id, CrisisEvent.brand_id == b.id,
            CrisisEvent.status == "active"))
        if a is not None:
            metrics = {"negative_today": a.negative_today, "expected": a.expected, "z": a.z,
                       "negative_share": a.negative_share, "domains": a.domains,
                       "escalation_share": a.escalation_share, "day": str(_today(now))}
            data = {"brand": b.name, "severity": a.severity, "count": a.negative_today,
                    "expected": a.expected, "share": round(a.negative_share * 100),
                    "domains": a.domains, "escalation": round(a.escalation_share * 100),
                    "z": a.z}
            if active is None:
                active = CrisisEvent(id=uuid.uuid4(), tenant_id=tenant_id, brand_id=b.id,
                                     status="active", severity=a.severity, started_at=now,
                                     last_detected_at=now, peak_negative=a.negative_today,
                                     metrics=metrics)
                db.add(active)
                db.flush()
                out.append(CrisisTransition(active, "opened", data))
            else:
                escalated = SEVERITY_ORDER.index(a.severity) > SEVERITY_ORDER.index(
                    active.severity)
                active.last_detected_at = now
                active.peak_negative = max(active.peak_negative, a.negative_today)
                active.metrics = metrics
                if escalated:
                    active.severity = a.severity
                    out.append(CrisisTransition(active, "escalated", data))
        elif active is not None and active.last_detected_at < now - timedelta(
                hours=s.crisis_quiet_hours):
            active.status, active.resolved_at = "resolved", now
            out.append(CrisisTransition(active, "resolved", {"brand": b.name}))
    return out


def update_trends(db: Session, tenant_id: uuid.UUID, brands: list[Brand],
                  now: datetime) -> dict[uuid.UUID, list[Any]]:
    s = get_settings()
    day = _today(now)
    found: dict[uuid.UUID, list[Any]] = {}
    for b in brands:
        topics = emerging_topics(q.topic_series(db, tenant_id, b.id, s.trend_baseline_days + 1))
        found[b.id] = topics
        for t in topics:
            db.execute(text("""
                INSERT INTO trend_events (id, tenant_id, brand_id, topic, day, count, expected, z)
                VALUES (:id, :t, :b, :topic, :day, :count, :exp, :z)
                ON CONFLICT (tenant_id, brand_id, lower(topic), day)
                DO UPDATE SET count = EXCLUDED.count, expected = EXCLUDED.expected, z = EXCLUDED.z
            """), {"id": uuid.uuid4(), "t": tenant_id, "b": b.id, "topic": t.topic[:120],
                   "day": day, "count": t.count, "exp": t.expected, "z": t.z})
    return found


# ------------------------------------------------------------------------------ rules
def _brands_for(rule: AlertRule, brands: list[Brand]) -> list[Brand]:
    return [b for b in brands if rule.brand_id is None or b.id == rule.brand_id]


def eval_negative_mention(db: Session, rule: AlertRule, brands: list[Brand], since: datetime,
                          cap: int) -> list[Candidate]:
    names = {b.id: b.name for b in _brands_for(rule, brands)}
    if not names:
        return []
    rows = db.execute(text(f"""
        SELECT m.id, m.brand_id, m.domain, m.title, m.url, m.published_at,
               coalesce(a.corrected_intents, a.intents) AS intents
        FROM mentions m JOIN mention_analysis a ON a.mention_id = m.id
        WHERE m.tenant_id = :t AND m.duplicate_of IS NULL AND a.status = 'done'
          AND m.entity_type = 'brand' AND m.brand_id = ANY(:brands)
          AND {q.SENT} = 'negative'
          AND coalesce(a.corrected_about_target, a.about_target, 'yes') <> 'no'
          AND coalesce(a.corrected_at, a.analysed_at) > :since
          AND (m.published_at IS NULL OR m.published_at > now() - make_interval(days => :age))
        ORDER BY m.seen_count DESC, m.collected_at DESC LIMIT :cap
    """), {"t": rule.tenant_id, "brands": list(names), "since": since, "cap": cap,  # noqa: S608 - constant fragment
           "age": int(_param(rule, "max_age_days", 7))}).mappings()
    out = []
    for r in rows:
        escalation = bool(set(r["intents"] or []) & set(ESCALATION_INTENTS))
        out.append(Candidate(
            "negative_mention", "MEDIUM" if escalation else "LOW", f"neg:{r['id']}",
            r["brand_id"],
            {"brand": names[r["brand_id"]], "domain": r["domain"], "mention_id": str(r["id"]),
             "url": r["url"], "mention_title": (r["title"] or "")[:200],
             "intents": list(r["intents"] or []),
             "published_at": r["published_at"].isoformat() if r["published_at"] else None}))
    return out


def eval_negative_spike(db: Session, rule: AlertRule, brands: list[Brand], now: datetime
                        ) -> list[Candidate]:
    s = get_settings()
    out = []
    for b in _brands_for(rule, brands):
        rows = q.daily(db, rule.tenant_id, b.id, s.trend_baseline_days + 1, dated=True)
        sp = detect_spike([r["negative"] for r in rows],
                          min_count=int(_param(rule, "min_count", 3)),
                          z_threshold=float(_param(rule, "z_threshold", 3.0)))
        if sp:
            day = str(_today(now))
            out.append(Candidate("negative_spike", sp.severity,
                                 f"spike:{b.id}:{day}:{sp.severity}", b.id,
                                 {"brand": b.name, "count": sp.count, "expected": sp.expected,
                                  "z": sp.z, "days": s.trend_baseline_days, "day": day}))
    return out


def eval_crisis(rule: AlertRule, brands: list[Brand], transitions: list[CrisisTransition]
                ) -> list[Candidate]:
    ids = {b.id for b in _brands_for(rule, brands)}
    out = []
    for t in transitions:
        if t.change == "resolved" or t.crisis.brand_id not in ids:
            continue
        key = f"crisis:{t.crisis.id}:open" if t.change == "opened" else \
            f"crisis:{t.crisis.id}:{t.crisis.severity}"
        out.append(Candidate("crisis", t.crisis.severity, key, t.crisis.brand_id,
                             {**t.data, "crisis_id": str(t.crisis.id), "change": t.change}))
    return out


def eval_emerging(rule: AlertRule, brands: list[Brand], trends: dict[uuid.UUID, list[Any]],
                  now: datetime) -> list[Candidate]:
    day = str(_today(now))
    out = []
    for b in _brands_for(rule, brands):
        for t in trends.get(b.id, []):
            out.append(Candidate("emerging_topic", "MEDIUM" if t.z >= 6 else "LOW",
                                 f"topic:{b.id}:{t.topic}:{day}"[:300], b.id,
                                 {"brand": b.name, "topic": t.topic, "count": t.count,
                                  "expected": t.expected, "z": t.z, "day": day}))
    return out


def eval_competitor(db: Session, rule: AlertRule, brands: list[Brand], now: datetime
                    ) -> list[Candidate]:
    s = get_settings()
    day = str(_today(now))
    out = []
    for b in _brands_for(rule, brands):
        comps = db.scalars(select(Competitor).where(Competitor.tenant_id == rule.tenant_id,
                                                    Competitor.brand_id == b.id))
        for c in comps:
            rows = q.daily(db, rule.tenant_id, b.id, s.trend_baseline_days + 1,
                           etypes=("competitor",), entity=c.name, dated=True)
            sp = detect_spike([r["total"] for r in rows],
                              min_count=int(_param(rule, "min_count", 3)),
                              z_threshold=float(_param(rule, "z_threshold", 3.0)))
            if sp:
                # Information about a competitor is never more urgent than about the client.
                sev = "MEDIUM" if sp.severity == "HIGH" else "LOW"
                out.append(Candidate("competitor_spike", sev, f"comp:{b.id}:{c.id}:{day}", b.id,
                                     {"brand": b.name, "competitor": c.name, "count": sp.count,
                                      "expected": sp.expected, "z": sp.z,
                                      "negative": rows[-1]["negative"], "day": day}))
    return out


def eval_connector_failure(db: Session, rule: AlertRule, brands: list[Brand], since: datetime,
                           now: datetime, cap: int) -> list[Candidate]:
    ids = [b.id for b in _brands_for(rule, brands)]
    out = []
    runs = db.execute(text("""
        SELECT r.id, r.error, p.name, p.brand_id FROM monitoring_runs r
        JOIN monitoring_projects p ON p.id = r.project_id AND p.tenant_id = r.tenant_id
        WHERE r.tenant_id = :t AND r.status = 'failed' AND r.finished_at > :since
          AND p.brand_id = ANY(:brands)
        ORDER BY r.finished_at DESC LIMIT :cap
    """), {"t": rule.tenant_id, "since": since, "brands": ids, "cap": cap}).mappings()
    for r in runs:
        out.append(Candidate("run_failed", "MEDIUM", f"run:{r['id']}", r["brand_id"],
                             {"project": r["name"], "run_id": str(r["id"])}))
    if rule.brand_id is None:   # sources are tenant-wide
        threshold = int(_param(rule, "error_threshold", 3))
        day = str(_today(now))
        src = db.execute(text("""
            SELECT id, domain, consecutive_errors, last_error FROM sources
            WHERE tenant_id = :t AND enabled AND consecutive_errors >= :n
            ORDER BY consecutive_errors DESC LIMIT :cap
        """), {"t": rule.tenant_id, "n": threshold, "cap": cap}).mappings()
        for r in src:
            out.append(Candidate("source_failing", "LOW", f"source:{r['id']}:{day}", None,
                                 {"domain": r["domain"], "errors": r["consecutive_errors"],
                                  "error": (r["last_error"] or "")[:120],
                                  "source_id": str(r["id"])}))
    return out


# ---------------------------------------------------------------------------- delivery
def _insert(db: Session, tenant: Tenant, rule: AlertRule, c: Candidate) -> uuid.UUID | None:
    title, body = describe(c.event, c.data, tenant.default_locale)
    return db.execute(text("""
        INSERT INTO alerts (id, tenant_id, rule_id, kind, severity, title, body, brand_id,
                            dedup_key, data)
        VALUES (:id, :t, :rule, :kind, :sev, :title, :body, :brand, :key, CAST(:data AS jsonb))
        ON CONFLICT (tenant_id, dedup_key) DO NOTHING RETURNING id
    """), {"id": uuid.uuid4(), "t": tenant.id, "rule": rule.id, "kind": c.event,
           "sev": c.severity, "title": title, "body": body, "brand": c.brand_id,
           "key": c.dedup_key[:300], "data": _json({**c.data, "rule_kind": rule.kind})}
    ).scalar_one_or_none()


def _json(d: dict[str, Any]) -> str:
    import json
    return json.dumps(d, ensure_ascii=False, default=str)


def _recipients(db: Session, tenant_id: uuid.UUID, roles: list[str]) -> list[Any]:
    return list(db.execute(text("""
        SELECT u.email, u.full_name, u.locale FROM memberships m
        JOIN users u ON u.id = m.user_id JOIN roles r ON r.id = m.role_id
        WHERE m.tenant_id = :t AND m.status = 'active' AND u.status = 'active'
          AND u.email_verified_at IS NOT NULL AND r.key = ANY(:roles)
    """), {"t": tenant_id, "roles": roles}).mappings())


def deliver(db: Session, tenant: Tenant, rule: AlertRule,
            new: list[tuple[uuid.UUID, Candidate]]) -> dict[str, int]:
    from app.jobs.queue import enqueue
    from app.modules.notifications.email import queue_email
    from app.modules.notifications.templates import alert_digest_email

    sent = {"email": 0, "webhook": 0}
    if not new:
        return sent
    if "email" in rule.channels:
        for r in _recipients(db, tenant.id, list(rule.notify_roles)):
            items = [(c.severity, *describe(c.event, c.data, r["locale"])) for _, c in new]
            queue_email(db, alert_digest_email(r["email"], r["full_name"], r["locale"], items),
                        tenant_id=tenant.id)
            sent["email"] += 1
    if "webhook" in rule.channels and rule.webhook_id:
        hook = db.get(Webhook, rule.webhook_id)
        if hook is not None and hook.enabled:
            for alert_id, _ in new:
                enqueue(db, "webhook.deliver", {"webhook_id": str(hook.id),
                                                "alert_id": str(alert_id)},
                        tenant_id=tenant.id, max_attempts=5)
                sent["webhook"] += 1
    return sent


# -------------------------------------------------------------------------------- main
def evaluate_tenant(db: Session, tenant_id: uuid.UUID, now: datetime | None = None
                    ) -> dict[str, int]:
    s = get_settings()
    now = now or datetime.now(UTC)
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        return {}
    brands = list(db.scalars(select(Brand).where(Brand.tenant_id == tenant_id,
                                                 Brand.status == "active")))
    transitions = update_crises(db, tenant_id, brands, now)
    trends = update_trends(db, tenant_id, brands, now)

    # A resolved crisis closes its open alerts.
    for t in transitions:
        if t.change == "resolved":
            db.execute(text("""UPDATE alerts SET status = 'resolved', resolved_at = :now
                               WHERE tenant_id = :t AND status <> 'resolved'
                                 AND data->>'crisis_id' = :cid"""),
                       {"now": now, "t": tenant_id, "cid": str(t.crisis.id)})

    stats = {"rules": 0, "alerts": 0, "emails": 0, "webhooks": 0,
             "crises_opened": sum(t.change == "opened" for t in transitions),
             "crises_resolved": sum(t.change == "resolved" for t in transitions)}
    cap = s.alerts_per_rule_per_run
    rules = db.scalars(select(AlertRule).where(AlertRule.tenant_id == tenant_id,
                                               AlertRule.enabled).order_by(AlertRule.created_at))
    for rule in rules:
        since = rule.last_evaluated_at or now - timedelta(hours=24)
        if rule.kind == "negative_mention":
            cands = eval_negative_mention(db, rule, brands, since, cap)
            if _param(rule, "escalation_only", False):
                cands = [c for c in cands if c.severity != "LOW"]
        elif rule.kind == "negative_spike":
            cands = eval_negative_spike(db, rule, brands, now)
        elif rule.kind == "crisis":
            cands = eval_crisis(rule, brands, transitions)
        elif rule.kind == "emerging_topic":
            cands = eval_emerging(rule, brands, trends, now)
        elif rule.kind == "competitor_spike":
            cands = eval_competitor(db, rule, brands, now)
        else:
            cands = eval_connector_failure(db, rule, brands, since, now, cap)
        cands = [c for c in cands if severity_at_least(c.severity, rule.min_severity)][:cap]
        new = [(aid, c) for c in cands if (aid := _insert(db, tenant, rule, c)) is not None]
        sent = deliver(db, tenant, rule, new)
        rule.last_evaluated_at = now
        stats["rules"] += 1
        stats["alerts"] += len(new)
        stats["emails"] += sent["email"]
        stats["webhooks"] += sent["webhook"]
    db.flush()
    log.info("alerts_evaluated", extra=stats)
    return stats


DEFAULT_RULE_NAMES = {
    "fr": ["Crise potentielle", "Hausse des mentions négatives",
           "Mention négative avec appel au boycott ou menace d'escalade", "Sujet émergent",
           "Activité inhabituelle d'un concurrent", "Échec de collecte ou de source"],
    "ar": ["أزمة محتملة", "ارتفاع الإشارات السلبية",
           "إشارة سلبية مع دعوة للمقاطعة أو تهديد بالتصعيد",
           "موضوع ناشئ", "نشاط غير معتاد لمنافس", "فشل الجمع أو مصدر"],
    "en": ["Potential crisis", "Negative mentions rising",
           "Negative mention with boycott call or escalation threat", "Emerging topic",
           "Unusual competitor activity", "Collection or source failure"],
}


def default_rules(tenant_id: uuid.UUID, user_id: uuid.UUID | None, locale: str = "fr"
                  ) -> list[AlertRule]:
    """A sensible starting set; every value is visible and editable in the UI."""
    names = DEFAULT_RULE_NAMES.get(locale, DEFAULT_RULE_NAMES["fr"])

    def r(i: int, kind: str, sev: str, channels: list[str], **params: Any) -> AlertRule:
        return AlertRule(id=uuid.uuid4(), tenant_id=tenant_id, name=names[i], kind=kind,
                         min_severity=sev, channels=channels, params=params,
                         notify_roles=["owner", "admin"], created_by=user_id, enabled=True)
    return [
        r(0, "crisis", "MEDIUM", ["in_app", "email"]),
        r(1, "negative_spike", "MEDIUM", ["in_app", "email"]),
        r(2, "negative_mention", "MEDIUM", ["in_app"], escalation_only=True),
        r(3, "emerging_topic", "LOW", ["in_app"]),
        r(4, "competitor_spike", "LOW", ["in_app"]),
        r(5, "connector_failure", "LOW", ["in_app"]),
    ]
