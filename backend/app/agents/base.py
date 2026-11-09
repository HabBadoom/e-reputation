"""Agent framework: typed inputs/outputs, tool allowlist, deadline, findings, proposed actions.

An agent receives an AgentContext, never a database session: everything it reads or does goes
through `ctx.call(tool, ...)`, which checks (1) the tool is in the agent's allowlist, (2) the
requesting member's effective permissions, (3) the step deadline.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

FindingKind = Literal["fact", "inference", "limitation"]
Level = Literal["info", "warning", "critical"]

# What any agent may ever do on behalf of a member — never members, billing, settings,
# approvals or platform administration, whatever the member's own role allows.
AGENT_PERMISSION_CEILING = frozenset({
    "brands:read", "mentions:read", "monitoring:read", "monitoring:write", "sources:read",
    "sources:write", "alerts:read", "alerts:manage", "reports:read", "reports:create",
    "browser:read", "browser:run", "ai:read",
})


class Finding(BaseModel):
    """Rendered by the UI from `code` + `values` in the reader's language.
    `fact` = counted from stored data; `inference` = interpretation by rule or model;
    `limitation` = what the data cannot tell."""
    model_config = ConfigDict(extra="forbid")
    code: str
    kind: FindingKind = "fact"
    level: Level = "info"
    values: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[Finding] = Field(default_factory=list)


class AgentError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ToolNotAllowed(AgentError):
    pass


class DeadlineExceeded(AgentError):
    pass


class NotConfigured(AgentError):
    """A capability the agent needs (e.g. the AI provider) is not available: step skipped."""


@dataclass
class ProposedAction:
    kind: str
    params: dict[str, Any]
    rationale: dict[str, Any]


@dataclass
class AgentContext:
    db: Session
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    permissions: frozenset[str]
    task_id: uuid.UUID
    brand_id: uuid.UUID | None
    locale: str
    params: dict[str, Any]
    previous: dict[str, dict[str, Any]] = field(default_factory=dict)   # outputs by agent
    allowed_tools: frozenset[str] = frozenset()
    deadline: float = 0.0
    tools_used: list[str] = field(default_factory=list)
    proposals: list[ProposedAction] = field(default_factory=list)
    llm_calls: int = 0
    llm_model: str | None = None
    prompt_version: str | None = None

    def check_time(self) -> None:
        if self.deadline and time.monotonic() > self.deadline:
            raise DeadlineExceeded("step_timeout")

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic()) if self.deadline else 60.0

    def call(self, name: str, **kwargs: Any) -> Any:
        from app.agents.tools import TOOLS
        if name not in self.allowed_tools:
            raise ToolNotAllowed(f"tool_not_allowed:{name}")
        tool = TOOLS[name]
        if tool.permission not in self.permissions:
            raise ToolNotAllowed(f"permission_missing:{tool.permission}")
        self.check_time()
        if name not in self.tools_used:
            self.tools_used.append(name)
        out = tool.fn(self, **kwargs)
        self.check_time()
        return out

    def propose(self, kind: str, params: dict[str, Any], **rationale: Any) -> None:
        self.proposals.append(ProposedAction(kind, params, rationale))


class Agent:
    """Subclasses set name/version/timeout/tools/Output and implement run()."""
    name: ClassVar[str]
    version: ClassVar[str] = "1.0"
    responsibility: ClassVar[str]
    timeout_seconds: ClassVar[int] = 30
    tools: ClassVar[frozenset[str]] = frozenset()
    Output: ClassVar[type[AgentOutput]] = AgentOutput

    def run(self, ctx: AgentContext) -> AgentOutput:
        raise NotImplementedError
