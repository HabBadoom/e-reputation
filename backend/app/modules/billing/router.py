"""Billing for the organization: plan, usage, billing details, invoices, plan changes.

Invoicing is manual (`REPINTEL_BILLING_PROVIDER=manual`): a plan change is a request to the
platform team, who updates the subscription and issues the invoice."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, require
from app.billing import invoices as inv_svc
from app.billing.usage import month_summary
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.models import (
    BillingAccount,
    Invoice,
    Plan,
    PlanChangeRequest,
    PlanPrice,
    Tenant,
)
from app.db.session import get_db
from app.modules.audit.service import record
from app.modules.billing.entitlements import plan_info, usage

router = APIRouter(prefix="/billing", tags=["billing"])


class AccountIn(BaseModel):
    legal_name: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=500)
    city: str = Field(default="", max_length=100)
    postal_code: str = Field(default="", max_length=20)
    country: str = Field(default="DZ", pattern=r"^[A-Z]{2}$")
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    rc: str = Field(default="", max_length=40)
    nif: str = Field(default="", max_length=40)
    nis: str = Field(default="", max_length=40)
    article_imposition: str = Field(default="", max_length=40)
    vat_number: str = Field(default="", max_length=40)
    currency: str = Field(default="DZD", pattern=r"^[A-Z]{3}$")


class PlanRequestIn(BaseModel):
    plan_key: str = Field(max_length=50)
    billing_interval: Literal["month", "year"] = "month"
    message: str = Field(default="", max_length=1000)


def _account(db: Session, tenant_id: uuid.UUID) -> BillingAccount:
    acct = db.get(BillingAccount, tenant_id)
    if acct is None:
        acct = BillingAccount(tenant_id=tenant_id)
        db.add(acct)
        db.flush()
    return acct


def _public_plans(db: Session) -> list[dict[str, Any]]:
    out = []
    for p in db.scalars(select(Plan).where(Plan.is_active, Plan.is_public)
                        .order_by(Plan.sort_order)):
        prices = [{"currency": x.currency, "billing_interval": x.billing_interval,
                   "amount_minor": x.amount_minor}
                  for x in db.scalars(select(PlanPrice).where(PlanPrice.plan_id == p.id,
                                                              PlanPrice.is_active))]
        out.append({"key": p.key, "name": p.name, "description": p.description,
                    "limits": plan_info_limits(db, p.id), "prices": prices})
    return out


def plan_info_limits(db: Session, plan_id: uuid.UUID) -> dict[str, int | None]:
    from app.db.models import PlanEntitlement
    from app.modules.billing.entitlements import KEYS
    ents = {e.key: e.value for e in db.scalars(select(PlanEntitlement)
                                               .where(PlanEntitlement.plan_id == plan_id))}
    return {k: ents.get(k) for k in KEYS}


@router.get("")
def overview(ctx: AuthContext = Depends(require("billing:read")),
             db: Session = Depends(get_db)) -> dict[str, Any]:
    info = plan_info(db, ctx.tenant_id)
    pending = db.scalars(select(PlanChangeRequest).where(
        PlanChangeRequest.tenant_id == ctx.tenant_id,
        PlanChangeRequest.status == "pending")).one_or_none()
    acct = _account(db, ctx.tenant_id)
    out = {
        "plan": {"key": info.key, "name": info.name, "status": info.status,
                 "active": info.active, "trial_ends_at": info.trial_ends_at,
                 "limits": info.limits},
        "usage": usage(db, ctx.tenant_id),
        "usage_month": month_summary(db),
        "provider": get_settings().billing_provider,
        "account": {k: getattr(acct, k) for k in AccountIn.model_fields},
        "missing_fields": inv_svc.missing_buyer_fields(acct),
        "plans": _public_plans(db),
        "pending_request": {"plan_key": db.get(Plan, pending.plan_id).key,
                            "billing_interval": pending.billing_interval,
                            "created_at": pending.created_at} if pending else None,
        "invoices": [{k: getattr(i, k) for k in (
            "id", "number", "status", "currency", "issue_date", "due_date", "total_minor",
            "paid_at")} for i in db.scalars(select(Invoice).where(
                Invoice.tenant_id == ctx.tenant_id, Invoice.status != "draft")
                .order_by(Invoice.issue_date.desc()))],
    }
    db.commit()
    return out


@router.put("/account")
def update_account(body: AccountIn, request: Request,
                   ctx: AuthContext = Depends(require("billing:manage")),
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    acct = _account(db, ctx.tenant_id)
    for k, v in body.model_dump().items():
        setattr(acct, k, v.strip() if isinstance(v, str) else v)
    acct.updated_at = datetime.now(UTC)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="billing.account_updated", request=request)
    db.commit()
    return {"account": body.model_dump(), "missing_fields": inv_svc.missing_buyer_fields(acct)}


@router.post("/plan-request", status_code=201)
def request_plan(body: PlanRequestIn, request: Request,
                 ctx: AuthContext = Depends(require("billing:manage")),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    plan = db.scalars(select(Plan).where(Plan.key == body.plan_key, Plan.is_active,
                                         Plan.is_public)).one_or_none()
    if plan is None:
        raise NotFound("Plan not available")
    if db.scalar(select(PlanChangeRequest.id).where(
            PlanChangeRequest.tenant_id == ctx.tenant_id,
            PlanChangeRequest.status == "pending")):
        raise AppError("A request is already pending", code="request_pending", status_code=409)
    r = PlanChangeRequest(id=uuid.uuid4(), tenant_id=ctx.tenant_id, plan_id=plan.id,
                          billing_interval=body.billing_interval, message=body.message.strip(),
                          requested_by=ctx.user_id)
    db.add(r)
    db.flush()
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="billing.plan_requested", target_type="plan_change_request", target_id=r.id,
           meta={"plan": plan.key, "interval": body.billing_interval}, request=request)
    _notify_platform(db, ctx.tenant_id, plan.name, body.billing_interval, body.message)
    db.commit()
    return {"id": r.id, "status": r.status}


@router.delete("/plan-request", status_code=204)
def cancel_request(request: Request, ctx: AuthContext = Depends(require("billing:manage")),
                   db: Session = Depends(get_db)) -> Response:
    r = db.scalars(select(PlanChangeRequest).where(
        PlanChangeRequest.tenant_id == ctx.tenant_id,
        PlanChangeRequest.status == "pending")).one_or_none()
    if r is None:
        raise NotFound("No pending request")
    r.status = "canceled"
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="billing.plan_request_canceled", target_id=r.id, request=request)
    db.commit()
    return Response(status_code=204)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: uuid.UUID, ctx: AuthContext = Depends(require("billing:read")),
                db: Session = Depends(get_db)) -> Response:
    i = db.get(Invoice, invoice_id)
    if i is None or i.tenant_id != ctx.tenant_id or i.status == "draft":
        raise NotFound("Invoice not found")
    return Response(inv_svc.render_pdf(db, i), media_type="application/pdf",
                    headers={"content-disposition": f'attachment; filename="{i.number}.pdf"',
                             "cache-control": "private, no-store"})


def _notify_platform(db: Session, tenant_id: uuid.UUID, plan: str, interval: str,
                     message: str) -> None:
    from app.modules.notifications.email import EmailMessage, queue_email
    s = get_settings()
    t = db.get(Tenant, tenant_id)
    for to in s.platform_notify_emails:
        body = (f"Organisation : {t.name if t else tenant_id}\nOffre demandée : {plan} "
                f"({interval})\nMessage : {message or '—'}\n\nConsole : "
                f"{s.app_base_url.rstrip('/')}/admin")
        queue_email(db, EmailMessage(to_email=to, to_name="", subject=f"[{s.email_from_name}] "
                                     f"Demande de changement d'offre", html="<pre>"
                                     + body.replace("&", "&amp;").replace("<", "&lt;")
                                     + "</pre>", text=body, tags=("plan_request",)))
