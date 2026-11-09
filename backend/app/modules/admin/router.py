"""Platform administration console (platform administrators only).

Cross-tenant listings go through SECURITY DEFINER functions that check the flag themselves.
Acting on one organization switches the database context to it; every such change is written
to that organization's own audit log, so its owners can see what the platform did.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_auth
from app.billing import invoices as inv_svc
from app.billing.usage import month_summary
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.models import (
    BillingAccount,
    ConnectorPolicy,
    Invoice,
    InvoiceLine,
    Plan,
    PlanChangeRequest,
    PlanEntitlement,
    PlanPrice,
    Subscription,
    Tenant,
    User,
    UserMfa,
)
from app.db.session import get_db, set_context
from app.modules.audit.service import record
from app.modules.billing.entitlements import KEYS

router = APIRouter(prefix="/admin", tags=["admin"])


def require_platform_admin(ctx: AuthContext = Depends(get_auth),
                           db: Session = Depends(get_db)) -> AuthContext:
    if not db.scalar(select(User.is_platform_admin).where(User.id == ctx.user_id)):
        raise AppError("Platform administrators only", code="forbidden", status_code=403)
    if get_settings().admin_require_mfa:
        mfa = db.get(UserMfa, ctx.user_id)
        if mfa is None or mfa.enabled_at is None:
            raise AppError("Enable two-step verification to use the admin console",
                           code="mfa_required", status_code=403)
    return ctx


def _enter(db: Session, ctx: AuthContext, tenant_id: uuid.UUID) -> Tenant:
    set_context(db, tenant_id=tenant_id, user_id=ctx.user_id)
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise NotFound("Organization not found")
    return t


def _leave(db: Session, ctx: AuthContext) -> None:
    set_context(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)


# ------------------------------------------------------------------------ overview
@router.get("/stats")
def stats(ctx: AuthContext = Depends(require_platform_admin),
          db: Session = Depends(get_db)) -> dict[str, Any]:
    return db.execute(text("SELECT admin_platform_stats()")).scalar_one()


@router.get("/tenants")
def tenants(ctx: AuthContext = Depends(require_platform_admin),
            db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in db.execute(text("SELECT * FROM admin_tenants()"))]


# -------------------------------------------------------------------- one tenant
class SubscriptionOut(BaseModel):
    plan_key: str
    plan_name: str
    status: str
    trial_ends_at: datetime | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    billing_interval: str | None
    currency: str | None
    notes: str


def _sub_out(db: Session, sub: Subscription) -> SubscriptionOut:
    p = db.get(Plan, sub.plan_id)
    return SubscriptionOut(plan_key=p.key, plan_name=p.name, status=sub.status,
                           trial_ends_at=sub.trial_ends_at,
                           current_period_start=sub.current_period_start,
                           current_period_end=sub.current_period_end,
                           billing_interval=sub.billing_interval, currency=sub.currency,
                           notes=sub.notes)


def _invoice_out(db: Session, i: Invoice) -> dict[str, Any]:
    lines = db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == i.id)
                       .order_by(InvoiceLine.position))
    return {
        **{k: getattr(i, k) for k in (
            "id", "number", "status", "currency", "locale", "period_start", "period_end",
            "issue_date", "due_date", "subtotal_minor", "tax_rate", "tax_minor",
            "stamp_duty_minor", "total_minor", "amount_in_words", "notes", "payment_method",
            "payment_reference", "paid_at", "void_reason", "voided_at", "created_at")},
        "lines": [{"description": ln.description, "quantity": ln.quantity,
                   "unit_minor": ln.unit_minor, "amount_minor": ln.amount_minor}
                  for ln in lines]}


def _account_out(acct: BillingAccount | None) -> dict[str, Any]:
    if acct is None:
        return {}
    return {k: getattr(acct, k) for k in (
        "legal_name", "address", "city", "postal_code", "country", "email", "phone", "rc",
        "nif", "nis", "article_imposition", "vat_number", "currency")}


@router.get("/tenants/{tenant_id}")
def tenant_detail(tenant_id: uuid.UUID, ctx: AuthContext = Depends(require_platform_admin),
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    t = _enter(db, ctx, tenant_id)
    sub = db.scalars(select(Subscription).where(Subscription.tenant_id == tenant_id)).one()
    acct = db.get(BillingAccount, tenant_id)
    out = {
        "id": t.id, "name": t.name, "slug": t.slug, "created_at": t.created_at,
        "subscription": _sub_out(db, sub).model_dump(),
        "billing_account": _account_out(acct),
        "missing_billing_fields": inv_svc.missing_buyer_fields(acct),
        "usage_month": month_summary(db),
        "invoices": [_invoice_out(db, i) for i in db.scalars(
            select(Invoice).where(Invoice.tenant_id == tenant_id)
            .order_by(Invoice.created_at.desc()))],
        "requests": [{"id": r.id, "plan_key": db.get(Plan, r.plan_id).key,
                      "billing_interval": r.billing_interval, "message": r.message,
                      "status": r.status, "created_at": r.created_at}
                     for r in db.scalars(select(PlanChangeRequest).where(
                         PlanChangeRequest.tenant_id == tenant_id)
                         .order_by(PlanChangeRequest.created_at.desc()).limit(20))],
    }
    _leave(db, ctx)
    return out


class SubscriptionPatch(BaseModel):
    plan_key: str | None = Field(default=None, max_length=50)
    status: Literal["trialing", "active", "past_due", "canceled", "expired"] | None = None
    trial_ends_at: datetime | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    billing_interval: Literal["month", "year"] | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    notes: str | None = Field(default=None, max_length=2000)


@router.patch("/tenants/{tenant_id}/subscription", response_model=SubscriptionOut)
def change_subscription(tenant_id: uuid.UUID, body: SubscriptionPatch, request: Request,
                        ctx: AuthContext = Depends(require_platform_admin),
                        db: Session = Depends(get_db)) -> SubscriptionOut:
    _enter(db, ctx, tenant_id)
    sub = db.scalars(select(Subscription).where(Subscription.tenant_id == tenant_id)).one()
    before = _sub_out(db, sub).model_dump(mode="json")
    changes = body.model_dump(exclude_unset=True)
    if "plan_key" in changes:
        plan = db.scalars(select(Plan).where(Plan.key == body.plan_key)).one_or_none()
        if plan is None:
            raise NotFound("Plan not found")
        sub.plan_id = plan.id
    for k in ("status", "trial_ends_at", "current_period_start", "current_period_end",
              "billing_interval", "currency", "notes"):
        if k in changes:
            setattr(sub, k, changes[k])
    if changes.get("status") == "canceled":
        sub.canceled_at = datetime.now(UTC)
    db.flush()
    after = _sub_out(db, sub)
    record(db, tenant_id=tenant_id, actor_user_id=ctx.user_id,
           action="platform.subscription_changed", target_type="subscription",
           target_id=sub.id, meta={"before": before, "after": after.model_dump(mode="json")},
           request=request)
    # Requests for the plan now in place are done.
    for r in db.scalars(select(PlanChangeRequest).where(
            PlanChangeRequest.tenant_id == tenant_id, PlanChangeRequest.status == "pending",
            PlanChangeRequest.plan_id == sub.plan_id)):
        r.status, r.handled_by, r.handled_at = "done", ctx.user_id, datetime.now(UTC)
    db.commit()
    return after


class RequestDecision(BaseModel):
    status: Literal["done", "declined"]


@router.post("/tenants/{tenant_id}/requests/{request_id}")
def handle_request(tenant_id: uuid.UUID, request_id: uuid.UUID, body: RequestDecision,
                   request: Request, ctx: AuthContext = Depends(require_platform_admin),
                   db: Session = Depends(get_db)) -> dict[str, str]:
    _enter(db, ctx, tenant_id)
    r = db.get(PlanChangeRequest, request_id)
    if r is None or r.tenant_id != tenant_id or r.status != "pending":
        raise NotFound("Pending request not found")
    r.status, r.handled_by, r.handled_at = body.status, ctx.user_id, datetime.now(UTC)
    record(db, tenant_id=tenant_id, actor_user_id=ctx.user_id,
           action=f"platform.plan_request_{body.status}", target_type="plan_change_request",
           target_id=r.id, request=request)
    db.commit()
    return {"status": r.status}


# ----------------------------------------------------------------------- invoices
class LineBody(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, le=Decimal("100000"))
    unit_minor: int = Field(ge=0, le=10**13)


class InvoiceBody(BaseModel):
    from_subscription: bool = False
    currency: str = Field(default="DZD", pattern=r"^[A-Z]{3}$")
    lines: list[LineBody] = Field(default_factory=list, max_length=50)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=100)
    stamp_duty_minor: int = Field(default=0, ge=0)
    locale: Literal["fr", "ar", "en"] = "fr"
    period_start: date | None = None
    period_end: date | None = None
    notes: str = Field(default="", max_length=2000)


def _get_invoice(db: Session, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    i = db.get(Invoice, invoice_id)
    if i is None or i.tenant_id != tenant_id:
        raise NotFound("Invoice not found")
    return i


@router.post("/tenants/{tenant_id}/invoices", status_code=201)
def create_invoice(tenant_id: uuid.UUID, body: InvoiceBody, request: Request,
                   ctx: AuthContext = Depends(require_platform_admin),
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    _enter(db, ctx, tenant_id)
    if body.from_subscription:
        i = inv_svc.draft_from_subscription(db, tenant_id, ctx.user_id)
    else:
        i = inv_svc.create_draft(
            db, tenant_id, currency=body.currency, tax_rate=body.tax_rate, locale=body.locale,
            notes=body.notes, created_by=ctx.user_id,
            period=(body.period_start, body.period_end)
            if body.period_start and body.period_end else None,
            lines=[inv_svc.LineIn(x.description, x.quantity, x.unit_minor) for x in body.lines])
        if body.stamp_duty_minor:
            inv_svc.set_lines(db, i, [inv_svc.LineIn(x.description, x.quantity, x.unit_minor)
                                      for x in body.lines],
                              tax_rate=Decimal(str(i.tax_rate)),
                              stamp_duty_minor=body.stamp_duty_minor)
    record(db, tenant_id=tenant_id, actor_user_id=ctx.user_id, action="platform.invoice_drafted",
           target_type="invoice", target_id=i.id, request=request)
    out = _invoice_out(db, i)
    db.commit()
    return out


@router.put("/tenants/{tenant_id}/invoices/{invoice_id}")
def update_invoice(tenant_id: uuid.UUID, invoice_id: uuid.UUID, body: InvoiceBody,
                   ctx: AuthContext = Depends(require_platform_admin),
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    _enter(db, ctx, tenant_id)
    i = _get_invoice(db, tenant_id, invoice_id)
    if i.status != "draft":
        raise inv_svc.InvoiceError("Only drafts can be edited")
    i.currency, i.locale, i.notes = body.currency, body.locale, body.notes
    i.period_start, i.period_end = body.period_start, body.period_end
    inv_svc.set_lines(db, i, [inv_svc.LineIn(x.description, x.quantity, x.unit_minor)
                              for x in body.lines],
                      tax_rate=body.tax_rate if body.tax_rate is not None
                      else Decimal(str(i.tax_rate)), stamp_duty_minor=body.stamp_duty_minor)
    out = _invoice_out(db, i)
    db.commit()
    return out


class PaymentBody(BaseModel):
    method: Literal["bank_transfer", "ccp", "cheque", "cash", "card", "other"]
    reference: str = Field(default="", max_length=200)
    paid_at: datetime | None = None


class VoidBody(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/tenants/{tenant_id}/invoices/{invoice_id}/issue")
def issue_invoice(tenant_id: uuid.UUID, invoice_id: uuid.UUID, request: Request,
                  ctx: AuthContext = Depends(require_platform_admin),
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    _enter(db, ctx, tenant_id)
    i = inv_svc.issue(db, _get_invoice(db, tenant_id, invoice_id))
    record(db, tenant_id=tenant_id, actor_user_id=ctx.user_id, action="platform.invoice_issued",
           target_type="invoice", target_id=i.id, meta={"number": i.number,
                                                        "total_minor": i.total_minor},
           request=request)
    out = _invoice_out(db, i)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/invoices/{invoice_id}/paid")
def invoice_paid(tenant_id: uuid.UUID, invoice_id: uuid.UUID, body: PaymentBody,
                 request: Request, ctx: AuthContext = Depends(require_platform_admin),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    _enter(db, ctx, tenant_id)
    i = _get_invoice(db, tenant_id, invoice_id)
    inv_svc.mark_paid(db, i, method=body.method, reference=body.reference, paid_at=body.paid_at)
    record(db, tenant_id=tenant_id, actor_user_id=ctx.user_id, action="platform.invoice_paid",
           target_type="invoice", target_id=i.id, meta={"method": body.method}, request=request)
    out = _invoice_out(db, i)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/invoices/{invoice_id}/void")
def invoice_void(tenant_id: uuid.UUID, invoice_id: uuid.UUID, body: VoidBody, request: Request,
                 ctx: AuthContext = Depends(require_platform_admin),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    _enter(db, ctx, tenant_id)
    i = _get_invoice(db, tenant_id, invoice_id)
    was_draft = i.status == "draft"
    inv_svc.void(db, i, body.reason)
    record(db, tenant_id=tenant_id, actor_user_id=ctx.user_id,
           action="platform.invoice_deleted" if was_draft else "platform.invoice_voided",
           target_type="invoice", target_id=invoice_id, meta={"reason": body.reason},
           request=request)
    out = {"id": invoice_id, "status": "deleted"} if was_draft else _invoice_out(db, i)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/invoices/{invoice_id}/pdf")
def invoice_pdf(tenant_id: uuid.UUID, invoice_id: uuid.UUID,
                ctx: AuthContext = Depends(require_platform_admin),
                db: Session = Depends(get_db)) -> Response:
    _enter(db, ctx, tenant_id)
    i = _get_invoice(db, tenant_id, invoice_id)
    pdf = inv_svc.render_pdf(db, i)
    name = f"{i.number or 'brouillon'}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"content-disposition": f'attachment; filename="{name}"',
                             "cache-control": "private, no-store"})


# -------------------------------------------------------------------------- plans
def _plan_out(db: Session, p: Plan) -> dict[str, Any]:
    ents = {e.key: e.value for e in db.scalars(
        select(PlanEntitlement).where(PlanEntitlement.plan_id == p.id))}
    prices = [{"id": x.id, "currency": x.currency, "billing_interval": x.billing_interval,
               "amount_minor": x.amount_minor}
              for x in db.scalars(select(PlanPrice).where(PlanPrice.plan_id == p.id,
                                                          PlanPrice.is_active))]
    return {"key": p.key, "name": p.name, "description": p.description,
            "is_active": p.is_active, "is_public": p.is_public, "is_default": p.is_default,
            "sort_order": p.sort_order,
            "entitlements": {k: ents.get(k) for k in KEYS},
            "unset": [k for k in KEYS if k not in ents], "prices": prices}


@router.get("/plans")
def list_plans(ctx: AuthContext = Depends(require_platform_admin),
               db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [_plan_out(db, p) for p in db.scalars(select(Plan).order_by(Plan.sort_order,
                                                                       Plan.key))]


def _plan(db: Session, key: str) -> Plan:
    p = db.scalars(select(Plan).where(Plan.key == key)).one_or_none()
    if p is None:
        raise NotFound("Plan not found")
    return p


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    is_public: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)


@router.patch("/plans/{key}")
def update_plan(key: str, body: PlanPatch, request: Request,
                ctx: AuthContext = Depends(require_platform_admin),
                db: Session = Depends(get_db)) -> dict[str, Any]:
    p = _plan(db, key)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    p.updated_at = datetime.now(UTC)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="platform.plan_updated",
           target_type="plan", meta={"plan": key, **body.model_dump(exclude_unset=True)},
           request=request)
    db.commit()
    return _plan_out(db, p)


@router.put("/plans/{key}/entitlements")
def set_entitlements(key: str, body: dict[str, int | None], request: Request,
                     ctx: AuthContext = Depends(require_platform_admin),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """Set limits; null = unlimited. Unknown keys are refused."""
    p = _plan(db, key)
    unknown = set(body) - set(KEYS)
    if unknown or any(v is not None and v < 0 for v in body.values()):
        raise AppError(f"Invalid entitlements: {sorted(unknown) or 'negative value'}",
                       code="validation_error", status_code=422)
    for k, v in body.items():
        e = db.get(PlanEntitlement, (p.id, k))
        if e is None:
            db.add(PlanEntitlement(plan_id=p.id, key=k, value=v))
        else:
            e.value = v
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="platform.entitlements_updated", target_type="plan",
           meta={"plan": key, "values": body}, request=request)
    db.commit()
    return _plan_out(db, p)


class PriceBody(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    billing_interval: Literal["month", "year"]
    amount_minor: int = Field(ge=0, le=10**13)


@router.post("/plans/{key}/prices")
def set_price(key: str, body: PriceBody, request: Request,
              ctx: AuthContext = Depends(require_platform_admin),
              db: Session = Depends(get_db)) -> dict[str, Any]:
    """A new price replaces the active one for that currency and interval (history kept)."""
    p = _plan(db, key)
    for old in db.scalars(select(PlanPrice).where(
            PlanPrice.plan_id == p.id, PlanPrice.currency == body.currency,
            PlanPrice.billing_interval == body.billing_interval, PlanPrice.is_active)):
        old.is_active = False
    db.flush()
    db.add(PlanPrice(id=uuid.uuid4(), plan_id=p.id, **body.model_dump()))
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="platform.price_set",
           target_type="plan", meta={"plan": key, **body.model_dump()}, request=request)
    db.commit()
    return _plan_out(db, p)


@router.delete("/plans/{key}/prices/{price_id}")
def remove_price(key: str, price_id: uuid.UUID,
                 ctx: AuthContext = Depends(require_platform_admin),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    p = _plan(db, key)
    price = db.get(PlanPrice, price_id)
    if price is None or price.plan_id != p.id:
        raise NotFound("Price not found")
    price.is_active = False
    db.commit()
    return _plan_out(db, p)


# ---------------------------------------------------------------- connector reviews
class ReviewBody(BaseModel):
    review_status: Literal["pending_legal_review", "approved", "rejected"]
    review_notes: str = Field(default="", max_length=4000)


def _connector_out(c: ConnectorPolicy) -> dict[str, Any]:
    return {k: getattr(c, k) for k in (
        "key", "name", "access_method", "legal_basis", "data_categories", "restrictions",
        "retention_days", "review_status", "reviewed_by", "reviewed_at", "review_notes")}


@router.get("/connectors")
def connectors(ctx: AuthContext = Depends(require_platform_admin),
               db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [_connector_out(c) for c in db.scalars(select(ConnectorPolicy)
                                                  .order_by(ConnectorPolicy.key))]


@router.patch("/connectors/{key}")
def review_connector(key: str, body: ReviewBody, request: Request,
                     ctx: AuthContext = Depends(require_platform_admin),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    """Record the outcome of the legal review. Approval should follow a qualified review
    (Algerian law 18-07, platform terms); the reviewer and notes are kept."""
    c = db.get(ConnectorPolicy, key)
    if c is None:
        raise NotFound("Connector not found")
    me = db.get(User, ctx.user_id)
    c.review_status, c.review_notes = body.review_status, body.review_notes
    c.reviewed_by, c.reviewed_at = (me.email if me else str(ctx.user_id)), datetime.now(UTC)
    c.updated_at = datetime.now(UTC)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="platform.connector_reviewed", target_type="connector",
           meta={"connector": key, "status": body.review_status}, request=request)
    db.commit()
    return _connector_out(c)
