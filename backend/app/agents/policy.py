"""Compliance policy for agent actions, and their executors.

Every action an agent proposes is checked here (Compliance Agent core, deterministic — no
model decides what is allowed): known action type, parameters valid and belonging to the
tenant, member's effective permission, connector compliance gates, per-task cap. Low-risk
actions within the member's rights run immediately; sensitive ones become approval requests.
Executors re-check everything at execution time (state may have changed since the proposal).
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    AiAction,
    AlertRule,
    ApprovalRequest,
    Brand,
    Mention,
    MonitoringProject,
    Report,
    Source,
)

POLICY_VERSION = "1.0"
MAX_ACTIONS_PER_TASK = 12


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectParams(_P):
    project_id: uuid.UUID


class SourceParams(_P):
    source_id: uuid.UUID


class ReportParams(_P):
    kind: str = Field(pattern="^(daily|weekly|monthly|crisis|competitor)$")
    format: str = Field(pattern="^(pdf|xlsx|csv|html)$")
    locale: str = Field(pattern="^(fr|ar|en)$")
    brand_id: uuid.UUID | None = None


class NoParams(_P):
    pass


class ResponseParams(_P):
    mention_id: uuid.UUID
    reply: str = Field(min_length=1, max_length=800)
    language: str = Field(max_length=20)
    needs_facts: bool = False


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    permission: str
    risk: str                # low | medium | high
    requires_approval: bool
    params: type[_P]
    connector: str | None = None      # compliance record that must allow it
    description: str = ""


ACTIONS: dict[str, ActionSpec] = {a.kind: a for a in (
    ActionSpec("run_monitoring", "monitoring:write", "low", False, ProjectParams,
               description="Run a monitoring project now"),
    ActionSpec("check_source", "sources:write", "low", False, SourceParams,
               description="Check a source (reachability, feeds)"),
    ActionSpec("browser_check_source", "browser:run", "low", False, SourceParams,
               connector="browser", description="Check a source with the browser fleet"),
    ActionSpec("create_report", "reports:create", "low", False, ReportParams,
               description="Generate a report"),
    ActionSpec("enable_source", "sources:write", "medium", True, SourceParams,
               description="Enable a discovered source for monitoring"),
    ActionSpec("create_default_alert_rules", "alerts:manage", "medium", True, NoParams,
               description="Add the recommended alert rules"),
    ActionSpec("use_response", "mentions:read", "high", True, ResponseParams,
               description="Approve a drafted public reply (publishing stays manual)"),
)}


@dataclass
class Decision:
    allowed: bool
    requires_approval: bool
    risk: str
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "requires_approval": self.requires_approval,
                "risk": self.risk, "reasons": self.reasons, "checks": self.checks,
                "policy_version": POLICY_VERSION, "at": datetime.now(UTC).isoformat()}


def _owned(db: Session, tenant_id: uuid.UUID, model: Any, oid: uuid.UUID | None) -> bool:
    if oid is None:
        return True
    return db.scalar(select(model.id).where(model.id == oid, model.tenant_id == tenant_id)) \
        is not None


def decide(db: Session, tenant_id: uuid.UUID, permissions: frozenset[str], kind: str,
           params: dict[str, Any], *, actions_in_task: int = 0) -> tuple[Decision, _P | None]:
    spec = ACTIONS.get(kind)
    if spec is None:
        return Decision(False, False, "high", ["unknown_action"]), None
    d = Decision(True, spec.requires_approval, spec.risk)
    try:
        p = spec.params.model_validate(params)
    except ValidationError:
        d.allowed = False
        d.reasons.append("invalid_parameters")
        return d, None
    d.checks["parameters"] = "valid"
    if spec.permission not in permissions:
        d.allowed = False
        d.reasons.append(f"permission_missing:{spec.permission}")
    d.checks["permission"] = spec.permission
    refs = {"project_id": MonitoringProject, "source_id": Source, "brand_id": Brand,
            "mention_id": Mention}
    for attr, model in refs.items():
        if hasattr(p, attr) and not _owned(db, tenant_id, model, getattr(p, attr)):
            d.allowed = False
            d.reasons.append(f"not_found:{attr}")
    if spec.connector:
        from app.connectors.browser import BrowserConnector
        perm = BrowserConnector().check_permissions(db)
        d.checks["connector"] = {"key": spec.connector, "review_status": perm.review_status}
        if not perm.allowed:
            d.allowed = False
            d.reasons.append("connector_not_authorized")
    if actions_in_task >= MAX_ACTIONS_PER_TASK:
        d.allowed = False
        d.reasons.append("too_many_actions")
    return d, p


# --------------------------------------------------------------------------- executors
def _run_monitoring(db: Session, tenant_id: uuid.UUID, p: ProjectParams, user: Any
                    ) -> dict[str, Any]:
    from app.core.rate_limit import get_limiter
    from app.modules.monitoring.runner import queue_manual_run
    proj = db.get(MonitoringProject, p.project_id)
    get_limiter().hit(f"monitoring:manual:{proj.id}", 10, 3600)
    run = queue_manual_run(db, tenant_id, proj)
    return {"run_id": str(run.id)}


def _check_source(db: Session, tenant_id: uuid.UUID, p: SourceParams, user: Any
                  ) -> dict[str, Any]:
    from app.modules.sources.router import _check
    src = db.get(Source, p.source_id)
    out = _check(db, SimpleNamespace(tenant_id=tenant_id), src)
    return {"ok": out.ok, "detail": out.detail, "coverage": out.source.coverage_status}


def _browser_check(db: Session, tenant_id: uuid.UUID, p: SourceParams, user: Any
                   ) -> dict[str, Any]:
    from app.browser.missions import create_mission
    src = db.get(Source, p.source_id)
    if src.kind != "website":
        raise ValueError("not_a_website")
    m = create_mission(db, tenant_id, "check_source", src.url, source_id=src.id, priority=5,
                       created_by=user)
    return {"mission_id": str(m.id), "status": m.status, "error": m.error}


def _create_report(db: Session, tenant_id: uuid.UUID, p: ReportParams, user: Any
                   ) -> dict[str, Any]:
    from app.jobs.queue import enqueue
    from app.modules.billing.entitlements import ensure_active, ensure_monthly
    from app.reports.data import default_period
    ensure_active(db, tenant_id)
    ensure_monthly(db, tenant_id, "max_reports_per_month")
    start, end = default_period(p.kind, datetime.now(UTC))
    r = Report(id=uuid.uuid4(), tenant_id=tenant_id, kind=p.kind, brand_id=p.brand_id,
               period_start=start, period_end=end, format=p.format, locale=p.locale,
               status="queued", created_by=user)
    db.add(r)
    db.flush()
    enqueue(db, "report.generate", {"report_id": str(r.id)}, tenant_id=tenant_id,
            max_attempts=1)
    return {"report_id": str(r.id)}


def _enable_source(db: Session, tenant_id: uuid.UUID, p: SourceParams, user: Any
                   ) -> dict[str, Any]:
    src = db.get(Source, p.source_id)
    src.enabled = True
    return {"enabled": True, "domain": src.domain}


def _default_rules(db: Session, tenant_id: uuid.UUID, p: NoParams, user: Any
                   ) -> dict[str, Any]:
    from app.alerts.engine import default_rules
    from app.db.models import Tenant
    existing = set(db.scalars(select(AlertRule.kind).where(AlertRule.tenant_id == tenant_id)))
    t = db.get(Tenant, tenant_id)
    added = [r for r in default_rules(tenant_id, user, t.default_locale if t else "fr")
             if r.kind not in existing]
    db.add_all(added)
    return {"rules_added": len(added)}


def _use_response(db: Session, tenant_id: uuid.UUID, p: ResponseParams, user: Any
                  ) -> dict[str, Any]:
    # Nothing is published: the platform has no publishing channel. The approved text is kept
    # for the team to copy, with who approved it (approval request).
    return {"approved_reply": p.reply, "language": p.language, "published": False}


EXECUTORS: dict[str, Callable[..., dict[str, Any]]] = {
    "run_monitoring": _run_monitoring, "check_source": _check_source,
    "browser_check_source": _browser_check, "create_report": _create_report,
    "enable_source": _enable_source, "create_default_alert_rules": _default_rules,
    "use_response": _use_response,
}


def execute(db: Session, action: AiAction, permissions: frozenset[str],
            acting_user: uuid.UUID | None) -> None:
    """Re-check and run. Sets status executed/failed (or rejected_by_policy)."""
    d, p = decide(db, action.tenant_id, permissions, action.kind, action.params)
    action.compliance = {**action.compliance, "at_execution": d.record()}
    if not d.allowed or p is None:
        action.status = "rejected_by_policy" if not action.requires_approval else "failed"
        action.error = ",".join(d.reasons)[:300]
        return
    try:
        with db.begin_nested():
            action.result = EXECUTORS[action.kind](db, action.tenant_id, p, acting_user)
        action.status = "executed"
        action.executed_at = datetime.now(UTC)
    except Exception as e:  # noqa: BLE001 - the error is the action's result
        action.status = "failed"
        action.error = f"{type(e).__name__}: {str(e)[:200]}"


def record_proposal(db: Session, *, tenant_id: uuid.UUID, task_id: uuid.UUID,
                    run_id: uuid.UUID | None, kind: str, params: dict[str, Any],
                    rationale: dict[str, Any], permissions: frozenset[str],
                    requested_by: uuid.UUID | None, actions_in_task: int) -> AiAction:
    spec = ACTIONS.get(kind)
    if spec is None:     # action kinds come from agent code, never from model output
        raise ValueError(f"unknown action kind {kind!r}")
    d, p = decide(db, tenant_id, permissions, kind, params, actions_in_task=actions_in_task)
    action = AiAction(id=uuid.uuid4(), tenant_id=tenant_id, task_id=task_id, agent_run_id=run_id,
                      kind=kind, params=p.model_dump(mode="json") if p else {},
                      rationale=rationale, risk=d.risk, requires_approval=spec.requires_approval,
                      status="proposed", compliance={"at_proposal": d.record()})
    if not d.allowed:
        action.status = "rejected_by_policy"
        action.error = ",".join(d.reasons)[:300]
        db.add(action)
        db.flush()
        return action
    db.add(action)
    db.flush()
    if spec.requires_approval:
        action.status = "pending_approval"
        db.add(ApprovalRequest(
            id=uuid.uuid4(), tenant_id=tenant_id, action_id=action.id, status="pending",
            requested_by=requested_by,
            expires_at=datetime.now(UTC)
            + timedelta(hours=get_settings().ai_approval_expiry_hours)))
    else:
        execute(db, action, permissions, requested_by)
    db.flush()
    return action
