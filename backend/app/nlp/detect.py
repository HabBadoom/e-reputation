"""Deterministic language/script heuristics (baseline, no AI).

Fast, free and explainable. Used to (1) pre-label texts, (2) give the AI annotators hints, and
(3) serve as a baseline in the benchmark. It is NOT a reliable Darija classifier on its own;
when evidence is thin it returns UNCERTAIN.
"""

import re
from dataclasses import dataclass, field

from app.nlp.lexicon import (
    DARIJA_MARKERS_ARABIC,
    DARIJA_MARKERS_LATIN,
    ENGLISH_STOPWORDS,
    FRENCH_STOPWORDS,
    MSA_MARKERS,
    variant_map,
)

UNCERTAIN = "UNCERTAIN"
_TOKEN = re.compile(r"[\w'’]+", re.UNICODE)
_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_TIFINAGH = re.compile(r"[ⴰ-⵿]")
_LATIN = re.compile(r"[A-Za-zÀ-ÿ]")
_ARABIZI_DIGIT = re.compile(r"(?<=[a-z])[23579]|[23579](?=[a-z])", re.IGNORECASE)
_VARIANTS = variant_map()
_DARIJA_LATIN = DARIJA_MARKERS_LATIN | set(_VARIANTS)
_AR_DIACRITICS = re.compile(r"[ً-ْـ]")


@dataclass
class Detection:
    script: str                     # arabic | latin | arabizi | tifinagh | mixed | none
    language: str                   # ar-DZ | ar | fr | en | UNCERTAIN
    code_switching: bool
    confidence: float               # heuristic margin, not a calibrated probability
    shares: dict[str, float] = field(default_factory=dict)
    darija_markers: list[str] = field(default_factory=list)


def _script_of(tok: str) -> str:
    if _TIFINAGH.search(tok):
        return "tifinagh"
    if _ARABIC.search(tok):
        return "arabic"
    if _LATIN.search(tok):
        return "arabizi" if _ARABIZI_DIGIT.search(tok) else "latin"
    return "other"


def detect(text: str) -> Detection:
    toks = [t.lower().replace("’", "'") for t in _TOKEN.findall(text or "")]
    toks = [t for t in toks if not t.isdigit()]
    if not toks:
        return Detection("none", UNCERTAIN, False, 0.0)

    scripts = [_script_of(t) for t in toks]
    counts = {s: scripts.count(s) for s in set(scripts) if s != "other"}
    total_scripted = sum(counts.values()) or 1
    latinish = counts.get("latin", 0) + counts.get("arabizi", 0)
    if counts.get("tifinagh", 0) / total_scripted > 0.5:
        script = "tifinagh"
    elif (counts.get("arabic", 0) and latinish
          and min(counts["arabic"], latinish) / total_scripted >= 0.2):
        script = "mixed"
    elif counts.get("arabic", 0) >= latinish:
        script = "arabic"
    else:
        script = "arabizi" if counts.get("arabizi", 0) else "latin"

    votes = {"ar-DZ": 0.0, "ar": 0.0, "fr": 0.0, "en": 0.0}
    markers: list[str] = []
    for tok, sc in zip(toks, scripts, strict=True):
        if sc == "arabic":
            bare = _AR_DIACRITICS.sub("", tok)
            if bare in DARIJA_MARKERS_ARABIC:
                votes["ar-DZ"] += 2
                markers.append(bare)
            elif bare in MSA_MARKERS:
                votes["ar"] += 1.5
            else:
                votes["ar"] += 0.3       # Arabic script, dialect unknown
                votes["ar-DZ"] += 0.25
        elif sc == "arabizi":
            votes["ar-DZ"] += 2
            markers.append(tok)
        elif sc == "latin":
            if tok in _DARIJA_LATIN:
                votes["ar-DZ"] += 2
                markers.append(tok)
            if tok in FRENCH_STOPWORDS:
                votes["fr"] += 1
            if tok in ENGLISH_STOPWORDS:
                votes["en"] += 1
            french_suffix = tok.endswith(("tion", "ment", "ée", "eux", "ais", "ait"))
            if french_suffix and tok not in _DARIJA_LATIN:
                votes["fr"] += 0.3

    total = sum(votes.values())
    if total == 0:
        return Detection(script, UNCERTAIN, False, 0.0, {}, markers)
    shares = {k: round(v / total, 3) for k, v in votes.items() if v}
    ranked = sorted(shares.items(), key=lambda kv: kv[1], reverse=True)
    top, top_share = ranked[0]
    second_share = ranked[1][1] if len(ranked) > 1 else 0.0
    # Code-switching: a second language with real weight (Darija/French mixing is common).
    code_switching = (second_share >= 0.25 and
                      {top, ranked[1][0]} != {"ar", "ar-DZ"})   # MSA vs Darija ≠ switching
    confidence = round(min(1.0, (top_share - second_share) + min(total, 10) / 20), 3)
    language = top if (len(toks) >= 3 and confidence >= 0.35) else UNCERTAIN
    return Detection(script, language, code_switching, confidence, shares,
                     list(dict.fromkeys(markers)))
