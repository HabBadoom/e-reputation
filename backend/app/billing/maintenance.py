"""Daily platform maintenance (job `platform.daily`, scheduled once a day):
- trials past their end date expire (collection stops, data stays readable), owners e-mailed;
- data retention: each organization's mentions, reports and browser traces older than its
  plan's `max_retention_days` (capped by REPINTEL_RETENTION_MAX_DAYS) are deleted;
- old usage events are pruned (kept 400 days for billing history)."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    Alert,
    BrowserMission,
    Membership,
    Mention,
    Report,
    Role,
    Tenant,
    UsageEvent,
    User,
)
from app.db.session import session_scope, set_context

log = logging.getLogger("repintel.maintenance")
USAGE_KEEP_DAYS = 400
BROWSER_TRACE_DAYS = 90


def _notify_expired(db: Session, tenant_id: uuid.UUID) -> int:
    from app.modules.notifications.email import queue_email
    from app.modules.notifications.templates import trial_expired_email
    t = db.get(Tenant, tenant_id)
    n = 0
    for u in db.scalars(select(User).join(Membership, Membership.user_id == User.id)
                        .join(Role, Role.id == Membership.role_id)
                        .where(Membership.tenant_id == tenant_id, Membership.status == "active",
                               Role.key == "owner", User.email_verified_at.is_not(None))):
        queue_email(db, trial_expired_email(u.email, u.full_name, u.locale,
                                            t.name if t else ""), tenant_id=tenant_id)
        n += 1
    return n


def retention_days(db: Session, tenant_id: uuid.UUID) -> int:
    from app.modules.billing.entitlements import plan_info
    cap = get_settings().retention_max_days
    plan = plan_info(db, tenant_id).limits.get("max_retention_days")
    return min(cap, plan) if plan is not None else cap


def purge_tenant(db: Session, tenant_id: uuid.UUID, now: datetime) -> dict[str, int]:
    days = retention_days(db, tenant_id)
    cut = now - timedelta(days=days)
    out = {"retention_days": days}
    out["mentions"] = db.execute(delete(Mention).where(
        Mention.tenant_id == tenant_id, Mention.collected_at < cut)).rowcount or 0
    out["reports"] = db.execute(delete(Report).where(
        Report.tenant_id == tenant_id, Report.created_at < cut)).rowcount or 0
    out["alerts"] = db.execute(delete(Alert).where(
        Alert.tenant_id == tenant_id, Alert.status == "resolved",
        Alert.created_at < cut)).rowcount or 0
    trace_cut = now - timedelta(days=min(days, BROWSER_TRACE_DAYS))
    out["browser_missions"] = db.execute(delete(BrowserMission).where(
        BrowserMission.tenant_id == tenant_id,
        BrowserMission.status.in_(("COMPLETED", "FAILED", "CANCELLED", "EXPIRED")),
        BrowserMission.created_at < trace_cut)).rowcount or 0
    out["usage_events"] = db.execute(delete(UsageEvent).where(
        UsageEvent.tenant_id == tenant_id,
        UsageEvent.occurred_at < now - timedelta(days=USAGE_KEEP_DAYS))).rowcount or 0
    return out


def run_daily(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    stats: dict[str, Any] = {"trials_expired": 0, "emails": 0, "purged": {}}
    with session_scope() as db:
        expired = [r[0] for r in db.execute(text("SELECT * FROM expire_trials()"))]
        tenants = [r[0] for r in db.execute(text("SELECT * FROM maintenance_tenants()"))]
    stats["trials_expired"] = len(expired)
    for tid in tenants:
        # One transaction per organization: a failure stays local and is retried tomorrow.
        try:
            with session_scope() as db:
                set_context(db, tenant_id=tid, user_id=None)
                if tid in expired:
                    stats["emails"] += _notify_expired(db, tid)
                res = purge_tenant(db, tid, now)
                if any(v for k, v in res.items() if k != "retention_days"):
                    stats["purged"][str(tid)] = res
        except Exception:
            log.exception("maintenance_tenant_failed", extra={"tenant_id": str(tid)})
    log.info("platform_daily", extra={"trials_expired": stats["trials_expired"],
                                      "tenants_purged": len(stats["purged"])})
    return stats
