"""Plan entitlements. Limits live in the database (plans / plan_entitlements), never in code.
A missing entitlement or a NULL value means "unlimited"."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
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
)

KEYS = ("max_brands", "max_monitoring_projects", "max_members", "min_frequency_minutes",
        "max_competitors_per_brand", "max_keywords_per_brand")


class PlanLimitReached(AppError):
    status_code = 402
    code = "plan_limit_reached"


@dataclass(frozen=True)
class PlanInfo:
    key: str
    name: str
    status: str
    trial_ends_at: datetime | None
    limits: dict[str, int | None]


def create_subscription(db: Session, tenant_id: uuid.UUID) -> None:
    s = get_settings()
    q = select(Plan).where(Plan.key == s.default_plan) if s.default_plan else \
        select(Plan).where(Plan.is_default.is_(True))
    plan = db.scalars(q).one()
    db.add(Subscription(tenant_id=tenant_id, plan_id=plan.id, status="trialing",
                        trial_ends_at=datetime.now(UTC) + timedelta(days=s.trial_days)))
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
