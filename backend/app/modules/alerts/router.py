"""Alerts feed, alert rules and webhooks."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.alerts.engine import default_rules
from app.alerts.webhooks import WebhookDeliveryError, new_secret, post
from app.api.deps import AuthContext, require
from app.collection.fetcher import FetchError, check_url
from app.core.crypto import encrypt
from app.core.errors import AppError, NotFound
from app.core.rate_limit import get_limiter
from app.db.models import Alert, AlertRule, Brand, Tenant, Webhook
from app.db.session import get_db
from app.modules.audit.service import record

router = APIRouter(tags=["alerts"])

Severity = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
RuleKind = Literal["negative_mention", "negative_spike", "crisis", "emerging_topic",
                   "competitor_spike", "connector_failure"]
Channel = Literal["in_app", "email", "webhook"]
RoleKey = Literal["owner", "admin", "analyst", "viewer"]


# ------------------------------------------------------------------------------ alerts
class AlertOut(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID | None
    kind: str
    severity: str
    title: str
    body: str
    brand_id: uuid.UUID | None
    data: dict[str, Any]
    status: str
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime


class AlertPage(BaseModel):
    items: list[AlertOut]
    total: int
    limit: int
    offset: int


class AlertSummary(BaseModel):
    open: int
    by_severity: dict[str, int]


def _alert_out(a: Alert) -> AlertOut:
    return AlertOut(id=a.id, rule_id=a.rule_id, kind=a.kind, severity=a.severity, title=a.title,
                    body=a.body, brand_id=a.brand_id, data=a.data, status=a.status,
                    acknowledged_at=a.acknowledged_at, resolved_at=a.resolved_at,
                    created_at=a.created_at)


@router.get("/alerts", response_model=AlertPage)
def list_alerts(ctx: AuthContext = Depends(require("alerts:read")),
                db: Session = Depends(get_db),
                status: Literal["open", "acknowledged", "resolved"] | None = None,
                severity: Severity | None = None, brand_id: uuid.UUID | None = None,
                kind: str | None = Query(default=None, max_length=30),
                limit: int = Query(default=50, ge=1, le=200),
                offset: int = Query(default=0, ge=0)) -> AlertPage:
    stmt = select(Alert).where(Alert.tenant_id == ctx.tenant_id)
    if status:
        stmt = stmt.where(Alert.status == status)
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if brand_id:
        stmt = stmt.where(Alert.brand_id == brand_id)
    if kind:
        stmt = stmt.where(Alert.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Alert.created_at.desc()).limit(limit).offset(offset))
    return AlertPage(items=[_alert_out(a) for a in rows], total=total, limit=limit,
                     offset=offset)


@router.get("/alerts/summary", response_model=AlertSummary)
def alert_summary(ctx: AuthContext = Depends(require("alerts:read")),
                  db: Session = Depends(get_db)) -> AlertSummary:
    rows = db.execute(select(Alert.severity, func.count()).where(
        Alert.tenant_id == ctx.tenant_id, Alert.status == "open").group_by(Alert.severity)).all()
    by = {sev: n for sev, n in rows}
    return AlertSummary(open=sum(by.values()), by_severity=by)


def _get_alert(db: Session, ctx: AuthContext, alert_id: uuid.UUID) -> Alert:
    a = db.scalars(select(Alert).where(Alert.id == alert_id,
                                       Alert.tenant_id == ctx.tenant_id)).one_or_none()
    if a is None:
        raise NotFound("Alert not found")
    return a


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge(alert_id: uuid.UUID, ctx: AuthContext = Depends(require("alerts:manage")),
                db: Session = Depends(get_db)) -> AlertOut:
    a = _get_alert(db, ctx, alert_id)
    if a.status == "open":
        a.status, a.acknowledged_by, a.acknowledged_at = ("acknowledged", ctx.user_id,
                                                          datetime.now(UTC))
    db.commit()
    return _alert_out(a)


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve(alert_id: uuid.UUID, ctx: AuthContext = Depends(require("alerts:manage")),
            db: Session = Depends(get_db)) -> AlertOut:
    a = _get_alert(db, ctx, alert_id)
    now = datetime.now(UTC)
    if a.status != "resolved":
        if a.acknowledged_at is None:
            a.acknowledged_by, a.acknowledged_at = ctx.user_id, now
        a.status, a.resolved_at = "resolved", now
    db.commit()
    return _alert_out(a)


@router.post("/alerts/evaluate", status_code=202)
def evaluate_now(ctx: AuthContext = Depends(require("alerts:manage")),
                 db: Session = Depends(get_db)) -> dict[str, bool]:
    """Queue an evaluation of this organization's rules now (at most one waiting)."""
    get_limiter().hit(f"alerts:evaluate:{ctx.tenant_id}", 30, 3600)
    queued = bool(db.execute(text("SELECT request_alert_evaluation()")).scalar_one())
    db.commit()
    return {"queued": queued}


@router.post("/alerts/acknowledge-all", response_model=AlertSummary)
def acknowledge_all(ctx: AuthContext = Depends(require("alerts:manage")),
                    db: Session = Depends(get_db)) -> AlertSummary:
    db.execute(update(Alert).where(Alert.tenant_id == ctx.tenant_id, Alert.status == "open")
               .values(status="acknowledged", acknowledged_by=ctx.user_id,
                       acknowledged_at=datetime.now(UTC)))
    db.commit()
    return AlertSummary(open=0, by_severity={})


# ------------------------------------------------------------------------------- rules
class RuleParams(BaseModel):
    """Optional thresholds; defaults are documented in docs/REPUTATION_METHODOLOGY.md."""
    model_config = {"extra": "forbid"}
    min_count: int | None = Field(default=None, ge=1, le=10000)
    z_threshold: float | None = Field(default=None, ge=1, le=50)
    max_age_days: int | None = Field(default=None, ge=1, le=90)
    error_threshold: int | None = Field(default=None, ge=1, le=100)
    escalation_only: bool | None = None


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: RuleKind
    brand_id: uuid.UUID | None = None
    params: RuleParams = RuleParams()
    min_severity: Severity = "LOW"
    channels: list[Channel] = Field(default_factory=lambda: ["in_app"], min_length=1)
    notify_roles: list[RoleKey] = Field(default_factory=lambda: ["owner", "admin"])
    webhook_id: uuid.UUID | None = None
    enabled: bool = True

    @field_validator("channels", "notify_roles")
    @classmethod
    def _dedup(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def _webhook(self) -> "RuleIn":
        if "webhook" in self.channels and self.webhook_id is None:
            raise ValueError("webhook channel needs webhook_id")
        return self


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    params: RuleParams | None = None
    min_severity: Severity | None = None
    channels: list[Channel] | None = Field(default=None, min_length=1)
    notify_roles: list[RoleKey] | None = None
    webhook_id: uuid.UUID | None = None
    enabled: bool | None = None


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    brand_id: uuid.UUID | None
    params: dict[str, Any]
    min_severity: str
    channels: list[str]
    notify_roles: list[str]
    webhook_id: uuid.UUID | None
    enabled: bool
    last_evaluated_at: datetime | None
    created_at: datetime


def _rule_out(r: AlertRule) -> RuleOut:
    return RuleOut(id=r.id, name=r.name, kind=r.kind, brand_id=r.brand_id, params=r.params,
                   min_severity=r.min_severity, channels=list(r.channels),
                   notify_roles=list(r.notify_roles), webhook_id=r.webhook_id,
                   enabled=r.enabled, last_evaluated_at=r.last_evaluated_at,
                   created_at=r.created_at)


def _get_rule(db: Session, ctx: AuthContext, rule_id: uuid.UUID) -> AlertRule:
    r = db.scalars(select(AlertRule).where(AlertRule.id == rule_id,
                                           AlertRule.tenant_id == ctx.tenant_id)).one_or_none()
    if r is None:
        raise NotFound("Alert rule not found")
    return r


def _check_refs(db: Session, ctx: AuthContext, brand_id: uuid.UUID | None,
                webhook_id: uuid.UUID | None) -> None:
    if brand_id and db.scalar(select(Brand.id).where(
            Brand.id == brand_id, Brand.tenant_id == ctx.tenant_id)) is None:
        raise NotFound("Brand not found")
    if webhook_id and db.scalar(select(Webhook.id).where(
            Webhook.id == webhook_id, Webhook.tenant_id == ctx.tenant_id)) is None:
        raise NotFound("Webhook not found")


def _rule_count_limit(db: Session, ctx: AuthContext) -> None:
    n = db.scalar(select(func.count()).select_from(AlertRule).where(
        AlertRule.tenant_id == ctx.tenant_id)) or 0
    if n >= 100:
        raise AppError("Too many alert rules", code="limit_reached", status_code=409)


@router.get("/alert-rules", response_model=list[RuleOut])
def list_rules(ctx: AuthContext = Depends(require("alerts:read")),
               db: Session = Depends(get_db)) -> list[RuleOut]:
    rows = db.scalars(select(AlertRule).where(AlertRule.tenant_id == ctx.tenant_id)
                      .order_by(AlertRule.created_at))
    return [_rule_out(r) for r in rows]


@router.post("/alert-rules", response_model=RuleOut, status_code=201)
def create_rule(body: RuleIn, request: Request,
                ctx: AuthContext = Depends(require("alerts:manage")),
                db: Session = Depends(get_db)) -> RuleOut:
    _rule_count_limit(db, ctx)
    _check_refs(db, ctx, body.brand_id, body.webhook_id)
    r = AlertRule(id=uuid.uuid4(), tenant_id=ctx.tenant_id, name=body.name.strip(),
                  kind=body.kind, brand_id=body.brand_id,
                  params=body.params.model_dump(exclude_none=True),
                  min_severity=body.min_severity, channels=body.channels,
                  notify_roles=body.notify_roles, webhook_id=body.webhook_id,
                  enabled=body.enabled, created_by=ctx.user_id)
    db.add(r)
    db.flush()
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="alert_rule.created",
           target_type="alert_rule", target_id=r.id, meta={"kind": r.kind}, request=request)
    db.commit()
    return _rule_out(r)


@router.post("/alert-rules/defaults", response_model=list[RuleOut], status_code=201)
def create_default_rules(request: Request,
                         ctx: AuthContext = Depends(require("alerts:manage")),
                         db: Session = Depends(get_db)) -> list[RuleOut]:
    """Adds the starter rules whose kind is not configured yet (idempotent)."""
    existing = set(db.scalars(select(AlertRule.kind).where(AlertRule.tenant_id == ctx.tenant_id)))
    tenant = db.get(Tenant, ctx.tenant_id)
    added = [r for r in default_rules(ctx.tenant_id, ctx.user_id,
                                      tenant.default_locale if tenant else "fr")
             if r.kind not in existing]
    db.add_all(added)
    db.flush()
    if added:
        record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
               action="alert_rule.defaults_added", meta={"count": len(added)}, request=request)
    db.commit()
    return [_rule_out(r) for r in added]


@router.patch("/alert-rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: uuid.UUID, body: RulePatch, request: Request,
                ctx: AuthContext = Depends(require("alerts:manage")),
                db: Session = Depends(get_db)) -> RuleOut:
    r = _get_rule(db, ctx, rule_id)
    fields = body.model_dump(exclude_unset=True)
    if "webhook_id" in fields:
        _check_refs(db, ctx, None, body.webhook_id)
        r.webhook_id = body.webhook_id
    if body.name is not None:
        r.name = body.name.strip()
    if body.params is not None:
        r.params = body.params.model_dump(exclude_none=True)
    if body.min_severity is not None:
        r.min_severity = body.min_severity
    if body.channels is not None:
        r.channels = list(dict.fromkeys(body.channels))
    if body.notify_roles is not None:
        r.notify_roles = list(dict.fromkeys(body.notify_roles))
    if body.enabled is not None:
        r.enabled = body.enabled
    if "webhook" in r.channels and r.webhook_id is None:
        raise AppError("webhook channel needs webhook_id", code="validation_error",
                       status_code=422)
    r.updated_at = datetime.now(UTC)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="alert_rule.updated",
           target_type="alert_rule", target_id=r.id, meta={"fields": sorted(fields)},
           request=request)
    db.commit()
    return _rule_out(r)


@router.delete("/alert-rules/{rule_id}", status_code=204)
def delete_rule(rule_id: uuid.UUID, request: Request,
                ctx: AuthContext = Depends(require("alerts:manage")),
                db: Session = Depends(get_db)) -> Response:
    r = _get_rule(db, ctx, rule_id)
    db.delete(r)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="alert_rule.deleted",
           target_type="alert_rule", target_id=rule_id, request=request)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------- webhooks
class WebhookIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=12, max_length=1000)

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        v = v.strip()
        if not v.lower().startswith("https://"):
            raise ValueError("webhook URL must use https")
        return v


class WebhookPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None


class WebhookOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    enabled: bool
    last_status: int | None
    last_error: str | None
    last_delivery_at: datetime | None
    created_at: datetime


class WebhookCreated(WebhookOut):
    secret: str        # shown once; stored encrypted


class TestResult(BaseModel):
    ok: bool
    status: int | None
    detail: str


def _hook_out(h: Webhook) -> WebhookOut:
    return WebhookOut(id=h.id, name=h.name, url=h.url, enabled=h.enabled,
                      last_status=h.last_status, last_error=h.last_error,
                      last_delivery_at=h.last_delivery_at, created_at=h.created_at)


def _get_hook(db: Session, ctx: AuthContext, hook_id: uuid.UUID) -> Webhook:
    h = db.scalars(select(Webhook).where(Webhook.id == hook_id,
                                         Webhook.tenant_id == ctx.tenant_id)).one_or_none()
    if h is None:
        raise NotFound("Webhook not found")
    return h


def _validate_target(url: str) -> None:
    try:
        check_url(url)
    except FetchError as e:
        raise AppError("This URL cannot receive webhooks (it must be a public https address)",
                       code="invalid_url", status_code=422) from e


@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(ctx: AuthContext = Depends(require("alerts:manage")),
                  db: Session = Depends(get_db)) -> list[WebhookOut]:
    rows = db.scalars(select(Webhook).where(Webhook.tenant_id == ctx.tenant_id)
                      .order_by(Webhook.created_at))
    return [_hook_out(h) for h in rows]


@router.post("/webhooks", response_model=WebhookCreated, status_code=201)
def create_webhook(body: WebhookIn, request: Request,
                   ctx: AuthContext = Depends(require("alerts:manage")),
                   db: Session = Depends(get_db)) -> WebhookCreated:
    n = db.scalar(select(func.count()).select_from(Webhook).where(
        Webhook.tenant_id == ctx.tenant_id)) or 0
    if n >= 10:
        raise AppError("Too many webhooks", code="limit_reached", status_code=409)
    _validate_target(body.url)
    secret = new_secret()
    h = Webhook(id=uuid.uuid4(), tenant_id=ctx.tenant_id, name=body.name.strip(), url=body.url,
                secret_enc=encrypt(secret), enabled=True)
    db.add(h)
    db.flush()
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="webhook.created",
           target_type="webhook", target_id=h.id, meta={"url": body.url}, request=request)
    db.commit()
    return WebhookCreated(**_hook_out(h).model_dump(), secret=secret)


@router.patch("/webhooks/{hook_id}", response_model=WebhookOut)
def update_webhook(hook_id: uuid.UUID, body: WebhookPatch, request: Request,
                   ctx: AuthContext = Depends(require("alerts:manage")),
                   db: Session = Depends(get_db)) -> WebhookOut:
    h = _get_hook(db, ctx, hook_id)
    if body.name is not None:
        h.name = body.name.strip()
    if body.enabled is not None:
        h.enabled = body.enabled
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="webhook.updated",
           target_type="webhook", target_id=h.id,
           meta={"fields": sorted(body.model_dump(exclude_unset=True))}, request=request)
    db.commit()
    return _hook_out(h)


@router.post("/webhooks/{hook_id}/rotate-secret", response_model=WebhookCreated)
def rotate_secret(hook_id: uuid.UUID, request: Request,
                  ctx: AuthContext = Depends(require("alerts:manage")),
                  db: Session = Depends(get_db)) -> WebhookCreated:
    h = _get_hook(db, ctx, hook_id)
    secret = new_secret()
    h.secret_enc = encrypt(secret)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="webhook.secret_rotated", target_type="webhook", target_id=h.id,
           request=request)
    db.commit()
    return WebhookCreated(**_hook_out(h).model_dump(), secret=secret)


@router.post("/webhooks/{hook_id}/test", response_model=TestResult)
def test_webhook(hook_id: uuid.UUID, ctx: AuthContext = Depends(require("alerts:manage")),
                 db: Session = Depends(get_db)) -> TestResult:
    h = _get_hook(db, ctx, hook_id)
    get_limiter().hit(f"webhooks:test:{ctx.tenant_id}", 20, 3600)
    delivery = uuid.uuid4()
    payload = {"event": "ping", "id": str(delivery), "tenant_id": str(ctx.tenant_id),
               "created_at": datetime.now(UTC).isoformat()}
    try:
        status = post(h, payload, delivery, event="ping")
        ok, detail = True, "delivered"
    except WebhookDeliveryError as e:
        status, ok, detail = h.last_status, False, str(e)
    db.commit()
    return TestResult(ok=ok, status=status, detail=detail)


@router.delete("/webhooks/{hook_id}", status_code=204)
def delete_webhook(hook_id: uuid.UUID, request: Request,
                   ctx: AuthContext = Depends(require("alerts:manage")),
                   db: Session = Depends(get_db)) -> Response:
    h = _get_hook(db, ctx, hook_id)
    db.delete(h)
    # Rules that used it fall back to their other channels.
    db.flush()
    rules = db.scalars(select(AlertRule).where(AlertRule.tenant_id == ctx.tenant_id,
                                               AlertRule.webhook_id.is_(None)))
    for r in rules:
        if "webhook" in r.channels:
            r.channels = [c for c in r.channels if c != "webhook"] or ["in_app"]
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="webhook.deleted",
           target_type="webhook", target_id=hook_id, request=request)
    db.commit()
    return Response(status_code=204)
