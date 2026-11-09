"""Plan entitlements. Limits live in the database (plans / plan_entitlements), never in code.
A missing entitlement or a NULL value means "unlimited"."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import (
    Brand,
    BrandKeyword,
    Competitor,
    Invitation,
    Membership,
    MonitoringProject,
    Plan,
    PlanEntitlement,
    Subscription,
    Webhook,
)

KEYS = ("max_brands", "max_monitoring_projects", "max_members", "min_frequency_minutes",
        "max_competitors_per_brand", "max_keywords_per_brand", "max_browser_missions_per_day",
        "max_ai_tasks_per_day", "max_mentions_per_month", "max_ai_calls_per_month",
        "max_reports_per_month", "max_webhooks", "max_concurrent_browser_missions",
        "max_retention_days")
# Monthly entitlements and the usage metric they cap.
MONTHLY = {"max_mentions_per_month": "mentions_collected", "max_ai_calls_per_month": "ai_calls",
           "max_reports_per_month": "reports_generated"}
ACTIVE_STATUSES = ("trialing", "active", "past_due")


class PlanLimitReached(AppError):
    status_code = 402
    code = "plan_limit_reached"


class SubscriptionInactive(AppError):
    status_code = 402
    code = "subscription_inactive"


@dataclass(frozen=True)
class PlanInfo:
    key: str
    name: str
    status: str
    trial_ends_at: datetime | None
    limits: dict[str, int | None]

    @property
    def active(self) -> bool:
        """Collection and new work are allowed. An expired trial is inactive even before the
        daily job marks it expired."""
        if self.status not in ACTIVE_STATUSES:
            return False
        return not (self.status == "trialing" and self.trial_ends_at is not None
                    and self.trial_ends_at < datetime.now(UTC))


def create_subscription(db: Session, tenant_id: uuid.UUID) -> None:
    s = get_settings()
    q = select(Plan).where(Plan.key == s.default_plan) if s.default_plan else \
        select(Plan).where(Plan.is_default.is_(True))
    plan = db.scalars(q).one()
    db.add(Subscription(tenant_id=tenant_id, plan_id=plan.id, status="trialing",
                        trial_ends_at=datetime.now(UTC) + timedelta(days=s.trial_days)))
    from app.db.models import BillingAccount, Tenant
    t = db.get(Tenant, tenant_id)
    db.add(BillingAccount(tenant_id=tenant_id, legal_name=t.name if t else ""))
    db.flush()


def plan_info(db: Session, tenant_id: uuid.UUID) -> PlanInfo:
    row = db.execute(select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id)
                     .where(Subscription.tenant_id == tenant_id)).one_or_none()
    if row is None:
        # Should not happen (created at registration / backfilled by migration). Fail safe:
        # treat as the most restrictive possible plan rather than unlimited.
        return PlanInfo("none", "No plan", "canceled", None, dict.fromkeys(KEYS, 0))
    sub, plan = row
    ents = {e.key: e.value for e in db.scalars(
        select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id))}
    return PlanInfo(plan.key, plan.name, sub.status, sub.trial_ends_at,
                    {k: ents.get(k) for k in KEYS})


def usage(db: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    def count(model, *where) -> int:  # noqa: ANN001
        return db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    return {
        "max_brands": count(Brand, Brand.tenant_id == tenant_id),
        "max_monitoring_projects": count(MonitoringProject,
                                         MonitoringProject.tenant_id == tenant_id),
        "max_members": count(Membership, Membership.tenant_id == tenant_id)
        + count(Invitation, Invitation.tenant_id == tenant_id,
                Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None),
                Invitation.expires_at > func.now()),
        "max_browser_missions_per_day": db.scalar(text("SELECT browser_missions_today()")) or 0,
        "max_ai_tasks_per_day": db.scalar(text("SELECT ai_tasks_today()")) or 0,
        "max_webhooks": count(Webhook, Webhook.tenant_id == tenant_id),
        **{k: int(db.scalar(text("SELECT usage_this_month(:m)"), {"m": m}) or 0)
           for k, m in MONTHLY.items()},
    }


def _limit(db: Session, tenant_id: uuid.UUID, key: str) -> int | None:
    return plan_info(db, tenant_id).limits.get(key)


def ensure_capacity(db: Session, tenant_id: uuid.UUID, key: str, *,
                    brand_id: uuid.UUID | None = None) -> None:
    """Raise PlanLimitReached if adding one more item would exceed the plan."""
    limit = _limit(db, tenant_id, key)
    if limit is None:
        return
    if key == "max_competitors_per_brand":
        current = db.scalar(select(func.count()).select_from(Competitor)
                            .where(Competitor.brand_id == brand_id)) or 0
    elif key == "max_keywords_per_brand":
        current = db.scalar(select(func.count()).select_from(BrandKeyword)
                            .where(BrandKeyword.brand_id == brand_id)) or 0
    else:
        current = usage(db, tenant_id)[key]
    if current >= limit:
        raise PlanLimitReached(f"Your plan allows {limit} ({key}). Contact us to upgrade.")


def ensure_frequency(db: Session, tenant_id: uuid.UUID, minutes: int) -> None:
    floor = _limit(db, tenant_id, "min_frequency_minutes")
    if floor is not None and minutes < floor:
        raise PlanLimitReached(f"Your plan allows monitoring every {floor} minutes at most.")


def ensure_active(db: Session, tenant_id: uuid.UUID) -> None:
    """New work (collection, reports, AI tasks, new brands…) needs an active subscription.
    Existing data stays readable and exportable."""
    if not plan_info(db, tenant_id).active:
        raise SubscriptionInactive("Your subscription is not active. Contact us to renew it.")


def remaining_this_month(db: Session, tenant_id: uuid.UUID, key: str) -> int | None:
    """How many more units the plan allows this month (None = unlimited)."""
    limit = _limit(db, tenant_id, key)
    if limit is None:
        return None
    used = int(db.scalar(text("SELECT usage_this_month(:m)"), {"m": MONTHLY[key]}) or 0)
    return max(0, limit - used)


def ensure_monthly(db: Session, tenant_id: uuid.UUID, key: str, amount: int = 1) -> None:
    left = remaining_this_month(db, tenant_id, key)
    if left is not None and left < amount:
        raise PlanLimitReached(f"Monthly limit reached ({key}). Contact us to upgrade.")
