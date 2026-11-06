"""Algerian NLP: validation status (all users) and corpus/benchmark admin (platform admins).

The corpus is the platform's ALGERIAN_NLP_BENCHMARK material. Gold labels are verified by a
platform admin (founder decision 2026-11-05); everything else is automatic.
"""

import random
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_auth
from app.core.errors import Forbidden, NotFound
from app.core.rate_limit import get_limiter
from app.db.models import DialectExpression, LanguageAnnotation, LanguageEvaluationRun, User
from app.db.session import get_db
from app.jobs.queue import enqueue
from app.nlp.prompts import INTENTS, LANGUAGES
from app.nlp.service import validation_status

router = APIRouter(prefix="/nlp", tags=["nlp"])


def platform_admin(ctx: AuthContext = Depends(get_auth),
                   db: Session = Depends(get_db)) -> AuthContext:
    user = db.get(User, ctx.user_id)
    if user is None or not user.is_platform_admin:
        raise Forbidden("Platform administrators only")
    return ctx


# ---------------------------------------------------------------- everyone
@router.get("/status")
def status(_: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    return validation_status(db)


@router.get("/expressions")
def expressions(_: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> list[dict]:
    return [{"canonical": e.canonical, "arabic": e.arabic, "variants": e.variants,
             "meaning_fr": e.meaning_fr, "polarity": e.polarity, "status": e.status}
            for e in db.scalars(select(DialectExpression).order_by(DialectExpression.canonical))]


# ---------------------------------------------------------------- platform admins
class CorpusItem(BaseModel):
    id: uuid.UUID
    text: str
    normalized_text: str | None
    source_url: str | None
    script: str | None
    auto_labels: dict[str, Any]
    auto_status: str
    labels: dict[str, Any] | None
    status: str
    split: str
    verified_at: datetime | None


def _item(a: LanguageAnnotation) -> CorpusItem:
    return CorpusItem(id=a.id, text=a.text, normalized_text=a.normalized_text,
                      source_url=a.source_url, script=a.script, auto_labels=a.auto_labels or {},
                      auto_status=a.auto_status, labels=a.labels, status=a.status, split=a.split,
                      verified_at=a.verified_at)


@router.get("/corpus/stats")
def corpus_stats(_: AuthContext = Depends(platform_admin), db: Session = Depends(get_db)) -> dict:
    def grouped(*cols) -> list[dict]:  # noqa: ANN002
        rows = db.execute(select(*cols, func.count()).group_by(*cols)).all()
        return [{**{c.key: v for c, v in zip(cols, r[:-1], strict=True)}, "count": r[-1]}
                for r in rows]
    return {"total": db.scalar(select(func.count()).select_from(LanguageAnnotation)) or 0,
            "by_split_status": grouped(LanguageAnnotation.split, LanguageAnnotation.status),
            "by_auto_status": grouped(LanguageAnnotation.auto_status),
            "by_script": grouped(LanguageAnnotation.script)}


class SampleIn(BaseModel):
    n: int = Field(default=100, ge=1, le=1000)
    split: Literal["gold_test", "gold_dev"] = "gold_test"


@router.post("/corpus/sample")
def sample(body: SampleIn, _: AuthContext = Depends(platform_admin),
           db: Session = Depends(get_db)) -> dict:
    """Move unassigned automatic items into a gold split, balanced across writing systems
    (Arabic script, Latin, Arabizi…) so Darija in every script is represented."""
    pool = list(db.scalars(select(LanguageAnnotation).where(
        LanguageAnnotation.split == "unassigned", LanguageAnnotation.status == "auto")))
    rng = random.Random()  # noqa: S311 — sampling, not security
    by_script: dict[str, list[LanguageAnnotation]] = {}
    for a in pool:
        by_script.setdefault(a.script or "none", []).append(a)
    for items in by_script.values():
        rng.shuffle(items)
    chosen: list[LanguageAnnotation] = []
    while len(chosen) < body.n and any(by_script.values()):
        for items in by_script.values():
            if items and len(chosen) < body.n:
                chosen.append(items.pop())
    for a in chosen:
        a.split = body.split
    db.commit()
    return {"assigned": len(chosen), "split": body.split}


@router.get("/corpus/queue", response_model=list[CorpusItem])
def queue(_: AuthContext = Depends(platform_admin), db: Session = Depends(get_db),
          split: Literal["gold_test", "gold_dev"] = "gold_test",
          limit: int = Query(default=20, ge=1, le=100)) -> list[CorpusItem]:
    """Gold items still waiting for human verification (labels pre-filled by the AI)."""
    return [_item(a) for a in db.scalars(
        select(LanguageAnnotation).where(LanguageAnnotation.split == split,
                                         LanguageAnnotation.status == "auto")
        .order_by(LanguageAnnotation.created_at).limit(limit))]


class GoldLabels(BaseModel):
    sentiment: Literal["positive", "negative", "neutral", "mixed"]
    sarcasm: Literal["yes", "no"]
    about_target: Literal["yes", "no"]
    language: str = Field(pattern="^(" + "|".join(x for x in LANGUAGES if x != "UNCERTAIN") + ")$")
    intents: list[str] = Field(default_factory=list, max_length=10)


class VerifyIn(BaseModel):
    labels: GoldLabels


@router.post("/corpus/{item_id}/verify", response_model=CorpusItem)
def verify(item_id: uuid.UUID, body: VerifyIn, request: Request,
           ctx: AuthContext = Depends(platform_admin),
           db: Session = Depends(get_db)) -> CorpusItem:
    a = db.get(LanguageAnnotation, item_id)
    if a is None:
        raise NotFound("Corpus item not found")
    labels = body.labels.model_dump()
    labels["intents"] = sorted(set(labels["intents"]) & set(INTENTS))
    auto = a.auto_labels or {}
    same = all(auto.get(k) == labels[k] for k in ("sentiment", "sarcasm", "about_target",
                                                   "language")) \
        and sorted(auto.get("intents") or []) == labels["intents"]
    a.labels = labels
    a.status = "human_verified" if same else "human_corrected"
    a.verified_by, a.verified_at = ctx.user_id, datetime.now(UTC)
    db.commit()
    return _item(a)


@router.post("/corpus/{item_id}/reject", response_model=CorpusItem)
def reject(item_id: uuid.UUID, ctx: AuthContext = Depends(platform_admin),
           db: Session = Depends(get_db)) -> CorpusItem:
    a = db.get(LanguageAnnotation, item_id)
    if a is None:
        raise NotFound("Corpus item not found")
    a.status, a.verified_by, a.verified_at = "rejected", ctx.user_id, datetime.now(UTC)
    db.commit()
    return _item(a)


class BenchmarkIn(BaseModel):
    split: Literal["gold_test", "gold_dev"] = "gold_test"


class RunOut(BaseModel):
    id: uuid.UUID
    status: str
    split: str
    n_items: int
    prompt_version: str | None
    analysis_version: str | None
    models: dict[str, Any]
    metrics: dict[str, Any]
    error: str | None
    created_at: datetime
    finished_at: datetime | None


def _run(r: LanguageEvaluationRun) -> RunOut:
    return RunOut(id=r.id, status=r.status, split=r.split, n_items=r.n_items,
                  prompt_version=r.prompt_version, analysis_version=r.analysis_version,
                  models=r.models or {}, metrics=r.metrics or {}, error=r.error,
                  created_at=r.created_at, finished_at=r.finished_at)


@router.post("/benchmark", response_model=RunOut, status_code=202)
def start_benchmark(body: BenchmarkIn, ctx: AuthContext = Depends(platform_admin),
                    db: Session = Depends(get_db)) -> RunOut:
    get_limiter().hit(f"nlp:benchmark:{ctx.user_id}", 5, 3600)
    run = LanguageEvaluationRun(split=body.split, requested_by=ctx.user_id)
    db.add(run)
    db.flush()
    enqueue(db, "nlp.benchmark", {"run_id": str(run.id), "requested_by": str(ctx.user_id)},
            tenant_id=None, max_attempts=1)
    db.commit()
    return _run(run)


@router.get("/benchmark", response_model=list[RunOut])
def list_benchmarks(_: AuthContext = Depends(platform_admin),
                    db: Session = Depends(get_db)) -> list[RunOut]:
    return [_run(r) for r in db.scalars(select(LanguageEvaluationRun)
                                        .order_by(LanguageEvaluationRun.created_at.desc())
                                        .limit(20))]
