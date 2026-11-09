"""Usage metering. One row per event (or batch), summed per calendar month (Algiers time).
Recorded in the same transaction as the work it measures."""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import UsageEvent

METRICS = ("mentions_collected", "search_queries", "ai_calls", "ai_tokens", "browser_missions",
           "reports_generated", "agent_tasks", "emails_sent")


def record(db: Session, tenant_id: uuid.UUID | None, metric: str, quantity: int = 1,
           **ref: Any) -> None:
    if tenant_id is None or quantity <= 0:
        return
    assert metric in METRICS, metric
    db.add(UsageEvent(tenant_id=tenant_id, metric=metric, quantity=int(quantity),
                      ref={k: str(v) for k, v in ref.items()}))


def this_month(db: Session, metric: str) -> int:
    """For the tenant of the current database context."""
    db.flush()
    return int(db.execute(text("SELECT usage_this_month(:m)"), {"m": metric}).scalar_one())


def month_summary(db: Session) -> dict[str, int]:
    return {m: this_month(db, m) for m in METRICS}


class CountingProvider:
    """Wraps an LLM provider to meter successful calls and tokens."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = getattr(inner, "name", "llm")
        self.calls = 0
        self.tokens = 0

    def complete_json(self, **kwargs: Any) -> Any:
        res = self.inner.complete_json(**kwargs)
        self.calls += 1
        self.tokens += int(getattr(res, "input_tokens", 0) or 0) + \
            int(getattr(res, "output_tokens", 0) or 0)
        return res
