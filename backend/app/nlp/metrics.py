"""Benchmark metrics (ALGERIAN_NLP_BENCHMARK). Pure functions.

Single-label tasks: macro-F1 / precision / recall over the gold classes, confusion matrix,
and coverage (share of items where the system did not answer UNCERTAIN). Abstentions are
reported separately and also counted as errors in `macro_f1_strict`.
Multi-label tasks (intents): per-label P/R/F1 and macro average over labels.
"""

from collections import Counter

UNCERTAIN = "UNCERTAIN"


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def single_label(gold: list[str], pred: list[str]) -> dict:
    assert len(gold) == len(pred)
    n = len(gold)
    labels = sorted(set(gold))
    covered = [(g, p) for g, p in zip(gold, pred, strict=True) if p != UNCERTAIN]
    confusion: dict[str, Counter] = {g: Counter() for g in labels}
    for g, p in zip(gold, pred, strict=True):
        confusion[g][p] += 1

    def per_class(pairs: list[tuple[str, str]]) -> dict[str, dict[str, float]]:
        out = {}
        for c in labels:
            tp = sum(1 for g, p in pairs if g == c and p == c)
            fp = sum(1 for g, p in pairs if g != c and p == c)
            fn = sum(1 for g, p in pairs if g == c and p != c)
            pr, rc, f1 = _prf(tp, fp, fn)
            out[c] = {"precision": pr, "recall": rc, "f1": f1, "support": tp + fn}
        return out

    pc_cov = per_class(covered)
    pc_all = per_class(list(zip(gold, pred, strict=True)))
    macro = lambda d: round(sum(v["f1"] for v in d.values()) / len(d), 4) if d else 0.0  # noqa: E731
    return {
        "n": n,
        "coverage": round(len(covered) / n, 4) if n else 0.0,
        "accuracy": round(sum(g == p for g, p in covered) / len(covered), 4) if covered else 0.0,
        "macro_f1": macro(pc_cov),
        "macro_f1_strict": macro(pc_all),
        "per_class": pc_cov,
        "confusion": {g: dict(c) for g, c in confusion.items()},
    }


def multi_label(gold: list[set[str]], pred: list[set[str]]) -> dict:
    labels = sorted(set().union(*gold, *pred)) if gold else []
    per = {}
    for c in labels:
        tp = sum(1 for g, p in zip(gold, pred, strict=True) if c in g and c in p)
        fp = sum(1 for g, p in zip(gold, pred, strict=True) if c not in g and c in p)
        fn = sum(1 for g, p in zip(gold, pred, strict=True) if c in g and c not in p)
        pr, rc, f1 = _prf(tp, fp, fn)
        per[c] = {"precision": pr, "recall": rc, "f1": f1, "support": tp + fn}
    supported = [v["f1"] for v in per.values() if v["support"]]
    return {"n": len(gold),
            "macro_f1": round(sum(supported) / len(supported), 4) if supported else 0.0,
            "per_label": per}
