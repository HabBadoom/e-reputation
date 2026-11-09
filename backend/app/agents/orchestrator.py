"""Orchestrator: turns a task into a fixed playbook of agent steps, runs them with the
requesting member's (capped) permissions, records every step and routes proposed actions
through the compliance policy. Job `ai.task`."""

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.agents import AGENTS
from app.agents.base import (
    AGENT_PERMISSION_CEILING,
    AgentContext,
    AgentError,
    DeadlineExceeded,
    NotConfigured,
)
from app.agents.policy import record_proposal
from app.agents.prompts import (
    PLANNER_PROMPT_VERSION,
    PLANNER_SYSTEM,
    llm_json,
    planner_schema,
    planner_user,
)
from app.api.deps import permissions_for_role
from app.core.config import get_settings
from app.db.models import AgentRun, AiAction, AiTask, Brand, Membership

log = logging.getLogger("repintel.agents")
ORCHESTRATOR_VERSION = "1.0"

PLAYBOOKS: dict[str, list[str]] = {
    "brand_health": ["compliance", "monitoring", "collection", "language", "entity",
                     "sentiment", "reputation", "trend", "crisis", "competitor", "reporting"],
    "crisis_response": ["crisis", "sentiment", "trend", "response", "reporting"],
    "competitor_review": ["competitor", "reputation", "trend", "reporting"],
    "source_discovery": ["compliance", "collection", "discovery"],
    "respond_to_mention": ["language", "response"],
    "report": ["reporting"],
}


def effective_permissions(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID | None
                          ) -> frozenset[str]:
    """The member's current permissions, capped by what agents may ever do."""
    if user_id is None:
        return frozenset()
    m = db.scalars(select(Membership).where(Membership.tenant_id == tenant_id,
                                            Membership.user_id == user_id,
                                            Membership.status == "active")).one_or_none()
    if m is None:
        return frozenset()
    return permissions_for_role(db, m.role_id) & AGENT_PERMISSION_CEILING


def _base_ctx(db: Session, task: AiTask, perms: frozenset[str]) -> AgentContext:
    return AgentContext(db=db, tenant_id=task.tenant_id, user_id=task.created_by,
                        permissions=perms, task_id=task.id, brand_id=task.brand_id,
                        locale=task.locale, params=dict(task.params or {}))


def _plan_free_text(db: Session, task: AiTask, perms: frozenset[str]) -> tuple[str, AgentRun]:
    """LLM routing restricted to an enum of playbooks; sees only the member's request."""
    run = AgentRun(id=uuid.uuid4(), tenant_id=task.tenant_id, task_id=task.id, position=0,
                   agent="orchestrator", agent_version=ORCHESTRATOR_VERSION, status="running",
                   input={"goal": task.goal})
    db.add(run)
    db.flush()
    brands = {b.name: b.id for b in db.scalars(select(Brand).where(
        Brand.tenant_id == task.tenant_id, Brand.status == "active").limit(100))}
    ctx = _base_ctx(db, task, perms)
    ctx.deadline = time.monotonic() + get_settings().agent_step_timeout_seconds
    started = time.monotonic()
    try:
        out = llm_json(ctx, system=PLANNER_SYSTEM, user=planner_user(task.goal),
                       schema=planner_schema(sorted(brands)), name="task_routing",
                       prompt_version=PLANNER_PROMPT_VERSION)
        kind = out.get("task")
        if kind not in [*PLAYBOOKS, "none"] or kind == "respond_to_mention":
            raise AgentError("planner_invalid_output")    # schema should prevent it
        brand = out.get("brand")
        if brand not in brands and brand != "ALL":
            raise AgentError("planner_invalid_output")
        if task.brand_id is None and brand in brands:
            task.brand_id = brands[brand]
        run.output = {"task": kind, "brand": brand, "reason": str(out.get("reason", ""))[:300]}
        run.status = "completed"
    except NotConfigured as e:
        run.status, run.error, kind = "skipped", e.reason, "error:" + e.reason
    except AgentError as e:
        run.status, run.error, kind = "failed", e.reason, "error:" + e.reason
    run.llm_calls, run.llm_model, run.prompt_version = (ctx.llm_calls, ctx.llm_model,
                                                        ctx.prompt_version)
    run.finished_at = datetime.now(UTC)
    run.duration_ms = int((time.monotonic() - started) * 1000)
    return kind, run


def _run_step(db: Session, task: AiTask, perms: frozenset[str], position: int, name: str,
              previous: dict[str, dict[str, Any]], task_deadline: float) -> AgentRun:
    agent = AGENTS[name]
    run = AgentRun(id=uuid.uuid4(), tenant_id=task.tenant_id, task_id=task.id,
                   position=position, agent=name, agent_version=agent.version,
                   status="running",
                   input={"brand_id": str(task.brand_id) if task.brand_id else None,
                          "params": task.params or {}})
    db.add(run)
    db.flush()
    ctx = _base_ctx(db, task, perms)
    ctx.previous = previous
    ctx.allowed_tools = agent.tools
    ctx.deadline = min(time.monotonic() + agent.timeout_seconds, task_deadline)
    started = time.monotonic()
    try:
        with db.begin_nested():            # an agent's failure leaves no partial writes
            out = agent.run(ctx)
            out = agent.Output.model_validate(out.model_dump())     # typed output contract
        run.output = out.model_dump(mode="json")
        run.status = "completed"
    except NotConfigured as e:
        run.status, run.error = "skipped", e.reason
    except DeadlineExceeded:
        run.status, run.error = "timeout", "step_timeout"
    except AgentError as e:
        run.status, run.error = "failed", e.reason[:300]
    except Exception as e:  # noqa: BLE001 - recorded, the task continues with other agents
        log.exception("agent_crashed", extra={"agent": name, "task_id": str(task.id)})
        run.status, run.error = "failed", f"internal_error:{type(e).__name__}"
    run.tools_used = list(ctx.tools_used)
    run.llm_calls, run.llm_model, run.prompt_version = (ctx.llm_calls, ctx.llm_model,
                                                        ctx.prompt_version)
    run.finished_at = datetime.now(UTC)
    run.duration_ms = int((time.monotonic() - started) * 1000)
    db.flush()
    if run.status == "completed":
        previous[name] = run.output
        for p in ctx.proposals:
            n = db.scalar(select(func.count()).select_from(AiAction).where(
                AiAction.task_id == task.id)) or 0
            record_proposal(db, tenant_id=task.tenant_id, task_id=task.id, run_id=run.id,
                            kind=p.kind, params=p.params, rationale=p.rationale,
                            permissions=perms, requested_by=task.created_by,
                            actions_in_task=n)
    log.info("agent_step", extra={"agent": name, "status": run.status,
                                  "duration_ms": run.duration_ms, "task_id": str(task.id)})
    return run


def finalize(db: Session, task: AiTask) -> None:
    runs = list(db.scalars(select(AgentRun).where(AgentRun.task_id == task.id)
                           .order_by(AgentRun.position)))
    actions = list(db.scalars(select(AiAction).where(AiAction.task_id == task.id)))
    findings = [{"agent": r.agent, **f} for r in runs if r.status == "completed"
                for f in (r.output or {}).get("findings", [])]
    by_status: dict[str, int] = {}
    for a in actions:
        by_status[a.status] = by_status.get(a.status, 0) + 1
    task.result = {"findings": findings,
                   "steps": {r.agent: r.status for r in runs},
                   "actions": by_status}
    task.status = "waiting_approval" if by_status.get("pending_approval") else "completed"
    task.finished_at = task.finished_at or datetime.now(UTC)


def run_task(db: Session, task_id: uuid.UUID) -> None:
    task = db.get(AiTask, task_id)
    if task is None or task.status != "queued":
        return
    s = get_settings()
    task.status, task.started_at = "running", datetime.now(UTC)
    task.orchestrator_version = ORCHESTRATOR_VERSION
    perms = effective_permissions(db, task.tenant_id, task.created_by)
    if "ai:read" not in perms:
        task.status, task.error = "failed", "requester_not_authorized"
        task.finished_at = datetime.now(UTC)
        return
    position = 1
    kind = task.kind
    if kind == "ask":
        kind, _ = _plan_free_text(db, task, perms)
        if kind.startswith("error:"):
            task.status, task.error = "failed", kind[6:]
            task.finished_at = datetime.now(UTC)
            return
        if kind == "none":
            task.resolved_kind = None
            task.result = {"findings": [{"agent": "orchestrator",
                                         "code": "orchestrator.out_of_scope",
                                         "kind": "inference", "level": "info", "values": {}}],
                           "steps": {"orchestrator": "completed"}, "actions": {}}
            task.status, task.finished_at = "completed", datetime.now(UTC)
            return
    task.resolved_kind = kind
    if kind == "report" and not (task.params or {}).get("report_format"):
        task.params = {**(task.params or {}), "report_format": "pdf", "report_kind": "weekly"}
    steps = PLAYBOOKS[kind]
    task.plan = [{"agent": n, "version": AGENTS[n].version} for n in steps]
    db.flush()
    deadline = time.monotonic() + s.agent_task_timeout_seconds
    previous: dict[str, dict[str, Any]] = {}
    for name in steps:
        if time.monotonic() > deadline:
            db.add(AgentRun(id=uuid.uuid4(), tenant_id=task.tenant_id, task_id=task.id,
                            position=position, agent=name, agent_version=AGENTS[name].version,
                            status="timeout", error="task_timeout",
                            finished_at=datetime.now(UTC), duration_ms=0))
        else:
            _run_step(db, task, perms, position, name, previous, deadline)
        position += 1
    finalize(db, task)
