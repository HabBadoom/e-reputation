"""Automatic annotation: several independent annotators + arbitration (master prompt §10.5,
founder decision 2026-11-05: annotation is automatic; a small gold set is human-verified).

Each annotator is (model, prompt variant). Labels agreed by a strict majority are accepted;
for disagreement on sentiment / about_target / sarcasm an arbiter decides; if it can't, the
label is UNCERTAIN. Every annotator's raw output is kept for audit.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.nlp.detect import UNCERTAIN, Detection, detect
from app.nlp.lexicon import SEED
from app.nlp.llm import LLMError, LLMProvider
from app.nlp.normalize import normalize
from app.nlp.prompts import (
    ANNOTATION_SCHEMA,
    ARBITER_RULES,
    ARBITRATION_SCHEMA,
    DIALECTS,
    EMOTIONS,
    INTENTS,
    LANGUAGES,
    PROMPT_VERSION,
    SENTIMENTS,
    TRI,
    VARIANTS,
    build_arbiter_message,
    build_user_message,
)

ANALYSIS_VERSION = "m4.1"
ARBITRATED_FIELDS = ("sentiment", "about_target", "sarcasm")


class Annotation(BaseModel):
    """Re-validation of the model output (never trust the provider's schema enforcement)."""
    about_target: str
    language: str
    dialect: str
    sentiment: str
    sarcasm: str
    intents: list[str]
    emotions: list[str]
    topics: list[str]
    translation_fr: str
    rationale: str
    confidence: str

    def check(self) -> "Annotation":
        ok = (self.about_target in TRI and self.sarcasm in TRI and self.sentiment in SENTIMENTS
              and self.language in LANGUAGES and self.dialect in DIALECTS
              and set(self.intents) <= set(INTENTS) and set(self.emotions) <= set(EMOTIONS))
        if not ok:
            raise ValueError("label outside the allowed set")
        self.topics = [t[:60] for t in self.topics[:5]]
        self.translation_fr = self.translation_fr[:4000]
        self.rationale = self.rationale[:500]
        return self


@dataclass
class Annotator:
    model: str
    variant: str

    @property
    def id(self) -> str:
        return f"{self.model}#{self.variant}"


@dataclass
class EnsembleResult:
    detection: Detection
    normalized_text: str
    final: dict[str, Any]
    agreement: float | None
    arbitration_used: bool
    auto_status: str             # auto_agreed | auto_arbitrated | uncertain
    annotations: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    models: dict[str, Any] = field(default_factory=dict)


def lexicon_hints(text: str) -> dict[str, Any]:
    low = f" {text.lower()} "
    found = []
    for canonical, arabic, variants, meaning, polarity in SEED:
        if any(f" {v} " in low or f" {v}," in low or f" {v}." in low for v in variants) \
                or arabic in text:
            found.append({"expression": canonical, "arabic": arabic, "meaning_fr": meaning,
                          "usual_polarity": polarity})
    return {"darija_expressions": found[:15]}


def _majority(values: list[str]) -> tuple[str | None, float]:
    if not values:
        return None, 0.0
    (top, n), *rest = Counter(values).most_common()
    share = n / len(values)
    tie = bool(rest) and rest[0][1] == n
    return (top if share > 0.5 and not tie else None), share


class Ensemble:
    def __init__(self, provider: LLMProvider, annotators: list[Annotator], arbiter_model: str):
        self.provider = provider
        self.annotators = annotators
        self.arbiter_model = arbiter_model

    def run(self, *, target: str, target_type: str, brand: str, title: str,
            text: str) -> EnsembleResult:
        det = detect(f"{title}\n{text}")
        norm, _changes = normalize(text)
        user = build_user_message(target=target, target_type=target_type, brand=brand,
                                  title=title, text=text, hints={
                                      **lexicon_hints(f"{title} {text}"),
                                      "script": det.script, "heuristic_language": det.language,
                                      "normalized_text": norm})
        anns: list[dict[str, Any]] = []
        errors: list[str] = []
        for a in self.annotators:
            try:
                res = self.provider.complete_json(model=a.model, system=VARIANTS[a.variant],
                                                  user=user, schema=ANNOTATION_SCHEMA,
                                                  schema_name="annotation")
                ann = Annotation.model_validate(res.data).check().model_dump()
                anns.append({**ann, "annotator": a.id, "model_reported": res.model})
            except LLMError as e:
                errors.append(f"{a.id}: {e.reason}")
                if e.fatal:
                    raise
            except (ValidationError, ValueError) as e:
                errors.append(f"{a.id}: invalid_output ({type(e).__name__})")

        models = {"annotators": [a.id for a in self.annotators], "arbiter": self.arbiter_model,
                  "prompt_version": PROMPT_VERSION, "analysis_version": ANALYSIS_VERSION}
        if len(anns) < 2:
            raise LLMError("too_few_annotations", "; ".join(errors) or "no annotator answered")

        final: dict[str, Any] = {}
        disputed = []
        sentiment_share = 0.0
        for f in ARBITRATED_FIELDS:
            label, share = _majority([x[f] for x in anns])
            if f == "sentiment":
                sentiment_share = share
            if label is None:
                disputed.append(f)
            final[f] = label
        for f in ("language", "dialect"):
            label, _ = _majority([x[f] for x in anns])
            final[f] = label or (det.language if f == "language" else UNCERTAIN)
        for f in ("intents", "emotions"):
            votes = Counter(v for x in anns for v in set(x[f]))
            final[f] = sorted(v for v, n in votes.items() if n / len(anns) > 0.5)
        final["topics"] = sorted({t.lower() for x in anns for t in x["topics"]})[:8]
        anchor = next((x for x in anns if x["sentiment"] == final.get("sentiment")), anns[0])
        final["translation_fr"] = anchor["translation_fr"]

        arbitration_used = False
        if disputed:
            arbitration_used = True
            try:
                res = self.provider.complete_json(
                    model=self.arbiter_model, system=ARBITER_RULES,
                    user=build_arbiter_message(user, anns), schema=ARBITRATION_SCHEMA,
                    schema_name="arbitration")
                arb = res.data
                for f in disputed:
                    allowed = SENTIMENTS if f == "sentiment" else TRI
                    final[f] = arb.get(f) if arb.get(f) in allowed else UNCERTAIN
                final["arbiter_rationale"] = str(arb.get("rationale", ""))[:500]
            except LLMError as e:
                errors.append(f"arbiter: {e.reason}")
                if e.fatal:
                    raise
                for f in disputed:
                    final[f] = UNCERTAIN
        for f in ARBITRATED_FIELDS:
            final[f] = final[f] or UNCERTAIN

        status = ("uncertain" if final["sentiment"] == UNCERTAIN
                  else "auto_arbitrated" if arbitration_used else "auto_agreed")
        return EnsembleResult(det, norm, final, round(sentiment_share, 3), arbitration_used,
                              status, anns, errors, models)


def build_ensemble(provider: LLMProvider, models: list[str], n: int, arbiter: str) -> Ensemble:
    """n annotators cycling over the configured models and the prompt variants A, B, C."""
    variants = list(VARIANTS)
    annotators = [Annotator(models[i % len(models)], variants[i % len(variants)])
                  for i in range(n)]
    return Ensemble(provider, annotators, arbiter or models[0])
