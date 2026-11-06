"""Job queue API over the PostgreSQL outbox (see migration 0003).

`enqueue` runs inside the caller's transaction: the job exists only if that transaction
commits. Workers claim jobs with FOR UPDATE SKIP LOCKED, so several can run in parallel.
Delivery is at-least-once: handlers must tolerate an occasional repeat.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Retry delays (seconds) after the 1st, 2nd, … failure. Beyond the list, the last value.
BACKOFF = [30, 120, 600, 3600, 6 * 3600]


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def enqueue(db: Session, kind: str, payload: dict[str, Any], *,
            tenant_id: uuid.UUID | None = None, run_after: datetime | None = None,
            max_attempts: int = 5, sensitive: bool = False) -> uuid.UUID:
    return db.execute(
        text("SELECT enqueue_job(:k, CAST(:p AS jsonb), :t, :r, :m, :s)"),
        {"k": kind, "p": json.dumps(payload), "t": tenant_id, "r": run_after,
         "m": max_attempts, "s": sensitive},
    ).scalar_one()


def claim(db: Session, worker_id: str, limit: int) -> list[Job]:
    rows = db.execute(text("SELECT * FROM claim_jobs(:w, :n)"),
                      {"w": worker_id, "n": limit}).all()
    return [Job(r.id, r.tenant_id, r.kind, r.payload or {}, r.attempts, r.max_attempts)
            for r in rows]


def complete(db: Session, job_id: uuid.UUID) -> None:
    db.execute(text("SELECT complete_job(:i)"), {"i": job_id})


def fail(db: Session, job: Job, error: str, *, retry: bool = True) -> str:
    delay = BACKOFF[min(job.attempts - 1, len(BACKOFF) - 1)] if retry else None
    return db.execute(text("SELECT fail_job(:i, :e, :d)"),
                      {"i": job.id, "e": error, "d": delay}).scalar_one()


def requeue_stale(db: Session, older_than_seconds: int) -> int:
    return db.execute(text("SELECT requeue_stale_jobs(:s)"),
                      {"s": older_than_seconds}).scalar_one()


def schedule_due_monitoring(db: Session) -> int:
    return db.execute(text("SELECT schedule_due_monitoring()")).scalar_one()


def stats(db: Session) -> dict[str, int]:
    return {r.status: r.count for r in db.execute(text("SELECT * FROM job_stats()"))}
