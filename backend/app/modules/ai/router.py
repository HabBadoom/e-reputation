"""AI tasks, agent runs, proposed actions and human approvals."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.agents import AGENTS
from app.agents.base import AGENT_PERMISSION_CEILING
from app.agents.orchestrator import PLAYBOOKS, finalize
from app.agents.policy import ACTIONS, ResponseParams, execute
from app.api.deps import AuthContext, require
from app.billing import usage
from app.core.errors import AppError, NotFound
from app.core.rate_limit import get_limiter
from app.db.models import AgentRun, AiAction, AiTask, ApprovalRequest, Brand, Mention
from app.db.session import get_db
from app.jobs.queue import enqueue
from app.modules.audit.service import record
from app.modules.billing.entitlements import ensure_active, plan_info

router = APIRouter(prefix="/ai", tags=["ai"])

TaskKind = Literal["brand_health", "crisis_response", "competitor_review", "source_discovery",
                   "respond_to_mention", "report", "ask"]


class TaskIn(BaseModel):
    kind: TaskKind
    brand_id: uuid.UUID | None = None
    goal: str = Field(default="", max_length=1000)
    mention_id: uuid.UUID | None = None
    report_format: Literal["pdf", "xlsx", "csv", "html"] | None = None
    report_kind: Literal["daily", "weekly", "monthly", "crisis", "competitor"] = "weekly"
    locale: Literal["fr", "ar", "en"] = "fr"

    @model_validator(mode="after")
    def _check(self) -> "TaskIn":
        if self.kind == "ask" and len(self.goal.strip()) < 3:
            raise ValueError("ask needs a goal")
        if self.kind == "respond_to_mention" and self.mention_id is None:
            raise ValueError("respond_to_mention needs mention_id")
        if self.kind == "report" and self.report_format is None:
            self.report_format = "pdf"
        return self


class TaskOut(BaseModel):
    id: uuid.UUID
    kind: str
    resolved_kind: str | None
    goal: str
    brand_id: uuid.UUID | None
    status: str
    error: str | None
    plan: list[Any]
    result: dict[str, Any]
    locale: str
    created_at: datetime
    finished_at: datetime | None


class RunOut(BaseModel):
    id: uuid.UUID
    position: int
    agent: str
    agent_version: str
    status: str
    output: dict[str, Any]
    tools_used: list[str]
    llm_calls: int
    llm_model: str | None
    prompt_version: str | None
    error: str | None
    duration_ms: int | None


class ApprovalOut(BaseModel):
    id: uuid.UUID
    status: str
    expires_at: datetime
    decided_at: datetime | None
    comment: str


class ActionOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    kind: str
    params: dict[str, Any]
    rationale: dict[str, Any]
    risk: str
    requires_approval: bool
    status: str
    compliance: dict[str, Any]
    result: dict[str, Any]
    error: str | None
    created_at: datetime
    executed_at: datetime | None
    approval: ApprovalOut | None = None


class TaskDetail(TaskOut):
    runs: list[RunOut]
    actions: list[ActionOut]


class TaskPage(BaseModel):
    items: list[TaskOut]
    total: int
    limit: int
    offset: int


class Decision(BaseModel):
    comment: str = Field(default="", max_length=500)
    reply: str | None = Field(default=None, min_length=1, max_length=800)   # edited draft


def _task_out(t: AiTask) -> TaskOut:
    return TaskOut(**{k: getattr(t, k) for k in TaskOut.model_fields})


def _action_out(db: Session, a: AiAction) -> ActionOut:
    ap = db.scalars(select(ApprovalRequest).where(ApprovalRequest.action_id == a.id)).one_or_none()
    return ActionOut(**{k: getattr(a, k) for k in ActionOut.model_fields if k != "approval"},
                     approval=ApprovalOut(**{k: getattr(ap, k) for k in ApprovalOut.model_fields})
                     if ap else None)


def _expire(db: Session, tenant_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    for ap in db.scalars(select(ApprovalRequest).where(
            ApprovalRequest.tenant_id == tenant_id, ApprovalRequest.status == "pending",
            ApprovalRequest.expires_at < now)):
        ap.status = "expired"
        a = db.get(AiAction, ap.action_id)
        if a is not None and a.status == "pending_approval":
            a.status = "expired"
    db.flush()


@router.get("/agents")
def agent_catalog(_: AuthContext = Depends(require("ai:read"))) -> dict[str, Any]:
    """What each agent does, its tools, timeout and version; playbooks; action policy."""
    return {
        "agents": [{"name": a.name, "version": a.version, "responsibility": a.responsibility,
                    "tools": sorted(a.tools), "timeout_seconds": a.timeout_seconds}
                   for a in AGENTS.values()],
        "playbooks": PLAYBOOKS,
        "actions": [{"kind": s.kind, "permission": s.permission, "risk": s.risk,
                     "requires_approval": s.requires_approval} for s in ACTIONS.values()],
        "permission_ceiling": sorted(AGENT_PERMISSION_CEILING),
    }


@router.post("/tasks", response_model=TaskOut, status_code=202)
def create_task(body: TaskIn, request: Request, ctx: AuthContext = Depends(require("ai:run")),
                db: Session = Depends(get_db)) -> TaskOut:
    get_limiter().hit(f"ai:tasks:{ctx.tenant_id}", 60, 3600)
    ensure_active(db, ctx.tenant_id)
    if body.brand_id and db.scalar(select(Brand.id).where(
            Brand.id == body.brand_id, Brand.tenant_id == ctx.tenant_id)) is None:
        raise NotFound("Brand not found")
    if body.mention_id:
        m = db.scalars(select(Mention).where(Mention.id == body.mention_id,
                                             Mention.tenant_id == ctx.tenant_id)).one_or_none()
        if m is None:
            raise NotFound("Mention not found")
        body.brand_id = body.brand_id or m.brand_id
    limit = plan_info(db, ctx.tenant_id).limits.get("max_ai_tasks_per_day")
    if limit is not None:
        from sqlalchemy import text
        used = db.execute(text("SELECT ai_tasks_today()")).scalar_one()
        if used >= limit:
            raise AppError("Daily AI task quota reached", code="plan_limit_reached",
                           status_code=402)
    params: dict[str, Any] = {}
    if body.mention_id:
        params["mention_id"] = str(body.mention_id)
    if body.report_format:
        params.update(report_format=body.report_format, report_kind=body.report_kind)
    t = AiTask(id=uuid.uuid4(), tenant_id=ctx.tenant_id, kind=body.kind, goal=body.goal.strip(),
               brand_id=body.brand_id, params=params, locale=body.locale, status="queued",
               created_by=ctx.user_id)
    db.add(t)
    db.flush()
    enqueue(db, "ai.task", {"task_id": str(t.id)}, tenant_id=ctx.tenant_id, max_attempts=1)
    usage.record(db, ctx.tenant_id, "agent_tasks", 1, task=t.id, kind=body.kind)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="ai.task_requested",
           target_type="ai_task", target_id=t.id, meta={"kind": body.kind}, request=request)
    db.commit()
    return _task_out(t)


@router.get("/tasks", response_model=TaskPage)
def list_tasks(ctx: AuthContext = Depends(require("ai:read")), db: Session = Depends(get_db),
               limit: int = Query(default=30, ge=1, le=100),
               offset: int = Query(default=0, ge=0)) -> TaskPage:
    stmt = select(AiTask).where(AiTask.tenant_id == ctx.tenant_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AiTask.created_at.desc()).limit(limit).offset(offset))
    return TaskPage(items=[_task_out(t) for t in rows], total=total, limit=limit, offset=offset)


def _get_task(db: Session, ctx: AuthContext, task_id: uuid.UUID) -> AiTask:
    t = db.scalars(select(AiTask).where(AiTask.id == task_id,
                                        AiTask.tenant_id == ctx.tenant_id)).one_or_none()
    if t is None:
        raise NotFound("Task not found")
    return t


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: uuid.UUID, ctx: AuthContext = Depends(require("ai:read")),
             db: Session = Depends(get_db)) -> TaskDetail:
    _expire(db, ctx.tenant_id)
    t = _get_task(db, ctx, task_id)
    runs = db.scalars(select(AgentRun).where(AgentRun.task_id == t.id)
                      .order_by(AgentRun.position))
    actions = db.scalars(select(AiAction).where(AiAction.task_id == t.id)
                         .order_by(AiAction.created_at))
    out = TaskDetail(**_task_out(t).model_dump(),
                     runs=[RunOut(**{k: getattr(r, k) for k in RunOut.model_fields})
                           for r in runs],
                     actions=[_action_out(db, a) for a in actions])
    db.commit()
    return out


@router.get("/approvals", response_model=list[ActionOut])
def pending_approvals(ctx: AuthContext = Depends(require("ai:read")),
                      db: Session = Depends(get_db)) -> list[ActionOut]:
    _expire(db, ctx.tenant_id)
    rows = db.scalars(select(AiAction).where(AiAction.tenant_id == ctx.tenant_id,
                                             AiAction.status == "pending_approval")
                      .order_by(AiAction.created_at).limit(100))
    out = [_action_out(db, a) for a in rows]
    db.commit()
    return out


def _decide(db: Session, ctx: AuthContext, action_id: uuid.UUID) -> tuple[AiAction,
                                                                          ApprovalRequest]:
    _expire(db, ctx.tenant_id)
    a = db.scalars(select(AiAction).where(AiAction.id == action_id,
                                          AiAction.tenant_id == ctx.tenant_id)).one_or_none()
    if a is None:
        raise NotFound("Action not found")
    ap = db.scalars(select(ApprovalRequest).where(ApprovalRequest.action_id == a.id)).one_or_none()
    if ap is None or ap.status != "pending" or a.status != "pending_approval":
        raise AppError("This action is not waiting for approval", code="not_pending",
                       status_code=409)
    return a, ap


def _close_task_if_done(db: Session, task_id: uuid.UUID) -> None:
    t = db.get(AiTask, task_id)
    if t is not None and t.status == "waiting_approval":
        finalize(db, t)


@router.post("/actions/{action_id}/approve", response_model=ActionOut)
def approve(action_id: uuid.UUID, body: Decision, request: Request,
            ctx: AuthContext = Depends(require("ai:approve")),
            db: Session = Depends(get_db)) -> ActionOut:
    """A human decision. The action is re-checked and executed with the approver's rights."""
    a, ap = _decide(db, ctx, action_id)
    if body.reply is not None:
        if a.kind != "use_response":
            raise AppError("Only reply drafts can be edited", code="not_editable",
                           status_code=422)
        a.params = ResponseParams.model_validate(
            {**a.params, "reply": body.reply.strip()}).model_dump(mode="json")
        a.rationale = {**a.rationale, "edited_by_human": True}
    ap.status, ap.decided_by, ap.decided_at = "approved", ctx.user_id, datetime.now(UTC)
    ap.comment = body.comment
    db.flush()
    a.status = "approved"
    db.flush()
    execute(db, a, ctx.permissions & AGENT_PERMISSION_CEILING, ctx.user_id)
    db.flush()
    _close_task_if_done(db, a.task_id)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="ai.action_approved",
           target_type="ai_action", target_id=a.id,
           meta={"kind": a.kind, "result": a.status, "edited": body.reply is not None},
           request=request)
    db.commit()
    return _action_out(db, a)


@router.post("/actions/{action_id}/reject", response_model=ActionOut)
def reject(action_id: uuid.UUID, body: Decision, request: Request,
           ctx: AuthContext = Depends(require("ai:approve")),
           db: Session = Depends(get_db)) -> ActionOut:
    a, ap = _decide(db, ctx, action_id)
    ap.status, ap.decided_by, ap.decided_at = "rejected", ctx.user_id, datetime.now(UTC)
    ap.comment = body.comment
    a.status = "rejected"
    db.flush()
    _close_task_if_done(db, a.task_id)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="ai.action_rejected",
           target_type="ai_action", target_id=a.id, meta={"kind": a.kind}, request=request)
    db.commit()
    return _action_out(db, a)
