"""Analysis of mentions, corpus feeding, benchmark runs and validation status."""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.connectors.base import Connector
from app.core.config import get_settings
from app.db.models import (
    Brand,
    LanguageAnnotation,
    LanguageEvaluationRun,
    Mention,
    MentionAnalysis,
)
from app.nlp import metrics
from app.nlp.detect import detect
from app.nlp.ensemble import ANALYSIS_VERSION, Ensemble, build_ensemble
from app.nlp.llm import LLMError, get_provider
from app.nlp.normalize import normalize
from app.nlp.prompts import PROMPT_VERSION

log = logging.getLogger("repintel.nlp")
TASKS = ("sentiment", "sarcasm", "about_target", "language", "intents")


class OpenAIPolicy(Connector):
    """Sending text to the AI provider is gated by its compliance record like a connector."""
    key = "openai"
    version = "1"
    category = "ai_provider"
    access_method = "llm_processing"
    capabilities: ClassVar[frozenset[str]] = frozenset({"analyse"})
    limitations = ("Accuracy is only claimed per task once measured on the gold benchmark.",)


def ai_configured() -> bool:
    s = get_settings()
    return bool(s.openai_api_key.get_secret_value() and s.nlp_models)


def make_ensemble() -> Ensemble:
    s = get_settings()
    return build_ensemble(get_provider(), s.nlp_models, s.nlp_annotators, s.nlp_arbiter_model)


def _gate(db: Session) -> tuple[str | None, str]:
    """Return (blocking status or None, note)."""
    if not ai_configured():
        return "not_configured", "OPENAI key or REPINTEL_NLP_MODELS not set"
    perm = OpenAIPolicy().check_permissions(db)
    if not perm.allowed:
        return "blocked_by_policy", perm.reason
    return None, perm.reason


# ---------------------------------------------------------------- mentions
def analyze_mentions(db: Session, tenant_id: uuid.UUID, mention_ids: list[uuid.UUID]) -> dict:
    rows = db.execute(select(Mention, Brand.name).join(Brand, Brand.id == Mention.brand_id)
                      .where(Mention.tenant_id == tenant_id, Mention.id.in_(mention_ids))).all()
    blocked, note = _gate(db)
    ensemble = None if blocked else make_ensemble()
    stats: dict[str, Any] = {"mentions": len(rows), "done": 0, "failed": 0, "skipped": 0,
                             "note": note}
    stop: str | None = None
    for m, brand_name in rows:
        a = db.get(MentionAnalysis, m.id) or MentionAnalysis(mention_id=m.id, tenant_id=tenant_id)
        db.add(a)
        body = m.original_text or m.snippet or ""
        det = detect(f"{m.title}\n{body}")
        a.script, a.heuristic_language = det.script, det.language
        a.code_switching = det.code_switching
        a.normalized_text = normalize(body)[0]
        if blocked or stop:
            a.status = blocked or "pending"
            a.error = note if blocked else stop
            stats["skipped"] += 1
            continue
        try:
            assert ensemble is not None
            res = ensemble.run(target=m.entity_name, target_type=m.entity_type, brand=brand_name,
                               title=m.title, text=body)
        except LLMError as e:
            a.status, a.error = "failed", e.reason
            stats["failed"] += 1
            if e.fatal:
                stop = e.reason       # auth/quota: don't hammer the provider
            continue
        f = res.final
        a.status, a.error = "done", ("; ".join(res.errors) or None)
        a.language, a.dialect = f["language"], f["dialect"]
        a.sentiment, a.sarcasm, a.about_target = f["sentiment"], f["sarcasm"], f["about_target"]
        a.intents, a.emotions, a.topics = f["intents"], f["emotions"], f["topics"]
        a.translation_fr = f["translation_fr"]
        a.agreement, a.arbitration_used = res.agreement, res.arbitration_used
        a.annotators = res.annotations + ([{"arbiter_rationale": f["arbiter_rationale"]}]
                                          if f.get("arbiter_rationale") else [])
        a.models = res.models
        a.prompt_version, a.analysis_version = PROMPT_VERSION, ANALYSIS_VERSION
        a.analysed_at = datetime.now(UTC)
        stats["done"] += 1
        db.flush()
        auto = {k: f[k] for k in ("sentiment", "sarcasm", "about_target", "language", "dialect",
                                  "intents")}
        auto |= {"target": m.entity_name, "target_type": m.entity_type, "brand": brand_name,
                 "title": m.title, "agreement": res.agreement}
        db.execute(text("SELECT corpus_add(:m, :t, :n, :u, :s, CAST(:l AS jsonb), :st)"), {
            "m": m.id, "t": body, "n": a.normalized_text, "u": m.url, "s": det.script,
            "l": _json(auto), "st": res.auto_status})
    if stop:
        stats["stopped"] = stop
    return stats


def _json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def effective(a: MentionAnalysis | None) -> dict[str, Any]:
    """Labels as shown to users: human corrections take precedence over the AI."""
    if a is None:
        return {"status": "pending"}
    return {
        "status": a.status, "script": a.script, "heuristic_language": a.heuristic_language,
        "code_switching": a.code_switching, "normalized_text": a.normalized_text,
        "language": a.language, "dialect": a.dialect,
        "sentiment": a.corrected_sentiment or a.sentiment,
        "sarcasm": a.corrected_sarcasm or a.sarcasm,
        "about_target": a.corrected_about_target or a.about_target,
        "intents": (a.corrected_intents if a.corrected_intents is not None
                    else list(a.intents or [])),
        "emotions": list(a.emotions or []), "topics": list(a.topics or []),
        "translation_fr": a.translation_fr, "agreement": a.agreement,
        "arbitration_used": a.arbitration_used, "corrected": a.corrected_at is not None,
        "analysed_at": a.analysed_at, "error": a.error,
    }


# ---------------------------------------------------------------- benchmark
def _gold_value(labels: dict, task: str) -> Any:
    v = labels.get(task)
    if task == "intents":
        return set(v) if isinstance(v, list) else None
    return v if isinstance(v, str) and v else None


def run_benchmark(db: Session, run: LanguageEvaluationRun) -> None:
    s = get_settings()
    run.status = "running"
    items = list(db.scalars(
        select(LanguageAnnotation).where(LanguageAnnotation.split == run.split,
                                         LanguageAnnotation.status.in_(
                                             ["human_verified", "human_corrected"]))
        .order_by(LanguageAnnotation.created_at).limit(s.nlp_benchmark_max_items)))
    blocked, note = _gate(db)
    if not items or blocked:
        run.status, run.error = "failed", (note if blocked else "no human-verified gold items")
        run.finished_at = datetime.now(UTC)
        return
    ensemble = make_ensemble()
    gold: dict[str, list] = {t: [] for t in TASKS}
    pred: dict[str, list] = {t: [] for t in TASKS}
    heur_gold, heur_pred = [], []
    failures = 0
    for it in items:
        labels = it.labels or {}
        auto = it.auto_labels or {}
        if (g := _gold_value(labels, "language")) is not None:
            heur_gold.append(g)
            heur_pred.append(detect(it.text).language)
        try:
            res = ensemble.run(target=auto.get("target", ""), target_type=auto.get(
                "target_type", "brand"), brand=auto.get("brand", ""), title=auto.get("title", ""),
                text=it.text)
        except LLMError as e:
            failures += 1
            if e.fatal:
                run.status, run.error = "failed", e.reason
                run.finished_at = datetime.now(UTC)
                return
            res = None
        for t in TASKS:
            g = _gold_value(labels, t)
            if g is None:
                continue
            gold[t].append(g)
            if res is None:
                pred[t].append(set() if t == "intents" else "UNCERTAIN")
            else:
                p = res.final.get(t)
                pred[t].append(set(p) if t == "intents" else p)
    out: dict[str, Any] = {}
    for t in TASKS:
        if gold[t]:
            out[t] = (metrics.multi_label(gold[t], pred[t]) if t == "intents"
                      else metrics.single_label(gold[t], pred[t]))
    if heur_gold:
        out["language_heuristic_baseline"] = metrics.single_label(heur_gold, heur_pred)
    out["provider_failures"] = failures
    run.metrics, run.n_items = out, len(items)
    run.models = ensemble_models()
    run.prompt_version, run.analysis_version = PROMPT_VERSION, ANALYSIS_VERSION
    run.status, run.finished_at = "completed", datetime.now(UTC)


def ensemble_models() -> dict[str, Any]:
    s = get_settings()
    return {"models": s.nlp_models, "arbiter": s.nlp_arbiter_model or (s.nlp_models or [""])[0],
            "annotators": s.nlp_annotators}


def validation_status(db: Session) -> dict[str, Any]:
    """Which tasks have been measured good enough to be presented as validated."""
    s = get_settings()
    run = db.scalars(select(LanguageEvaluationRun).where(
        LanguageEvaluationRun.status == "completed",
        LanguageEvaluationRun.prompt_version == PROMPT_VERSION,
        LanguageEvaluationRun.analysis_version == ANALYSIS_VERSION)
        .order_by(LanguageEvaluationRun.finished_at.desc()).limit(1)).one_or_none()
    tasks = {}
    for t in TASKS:
        m = (run.metrics or {}).get(t) if run else None
        ok = bool(m and m["n"] >= s.nlp_min_gold_items and m["macro_f1"] >= s.nlp_min_macro_f1
                  and m.get("coverage", 1.0) >= s.nlp_min_coverage)
        tasks[t] = {"validated": ok, "macro_f1": m["macro_f1"] if m else None,
                    "coverage": m.get("coverage") if m else None, "n": m["n"] if m else 0}
    return {"run_id": str(run.id) if run else None,
            "finished_at": run.finished_at if run else None, "tasks": tasks,
            "thresholds": {"macro_f1": s.nlp_min_macro_f1, "coverage": s.nlp_min_coverage,
                           "gold_items": s.nlp_min_gold_items},
            "ai_configured": ai_configured()}
