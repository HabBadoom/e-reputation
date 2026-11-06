"""Transparent statistics behind trends, spikes, crises and the reputation index.

Everything here is a documented formula on counted mentions (docs/REPUTATION_METHODOLOGY.md);
nothing is an AI-generated score.
"""

import math
from dataclasses import dataclass

SEVERITY_ORDER = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


def severity_at_least(sev: str, minimum: str) -> bool:
    return SEVERITY_ORDER.index(sev) >= SEVERITY_ORDER.index(minimum)


def raise_severity(sev: str, steps: int = 1) -> str:
    return SEVERITY_ORDER[min(len(SEVERITY_ORDER) - 1, SEVERITY_ORDER.index(sev) + steps)]


def poisson_z(observed: float, expected: float) -> float:
    """How unusual a count is versus its baseline: (observed - expected) / sqrt(expected).
    The denominator is floored at 1 so a zero baseline doesn't make any count infinite."""
    return round((observed - expected) / math.sqrt(max(expected, 1.0)), 3)


def baseline(history: list[int]) -> float:
    """Mean daily count over the baseline window (excluding the day being tested)."""
    return round(sum(history) / len(history), 3) if history else 0.0


@dataclass
class ReputationIndex:
    value: float | None        # 0–100, None when the sample is too small
    net_sentiment: float | None
    sample: int
    positive: int
    negative: int
    neutral: int
    mixed: int


def reputation_index(positive: int, negative: int, neutral: int, mixed: int,
                     min_sample: int = 10) -> ReputationIndex:
    """50 + 50 × (positive − negative) / analysed. 50 = balanced, 100 = all positive,
    0 = all negative. Mixed and neutral count in the denominator only. Uncertain and
    unanalysed mentions are excluded. Below `min_sample` analysed mentions: no value."""
    n = positive + negative + neutral + mixed
    if n < min_sample:
        return ReputationIndex(None, None, n, positive, negative, neutral, mixed)
    net = (positive - negative) / n
    return ReputationIndex(round(50 + 50 * net, 1), round(net, 3), n, positive, negative,
                           neutral, mixed)


@dataclass
class Emerging:
    topic: str
    count: int
    expected: float
    z: float


def emerging_topics(series: dict[str, list[int]], *, min_count: int = 3,
                    z_threshold: float = 3.0) -> list[Emerging]:
    """series[topic] = daily counts, oldest → newest; the last value is the day tested."""
    out = []
    for topic, counts in series.items():
        if not counts:
            continue
        today, history = counts[-1], counts[:-1]
        exp = baseline(history)
        z = poisson_z(today, exp)
        if today >= min_count and z >= z_threshold:
            out.append(Emerging(topic, today, exp, z))
    return sorted(out, key=lambda e: e.z, reverse=True)


@dataclass
class Spike:
    count: int
    expected: float
    z: float
    severity: str


def detect_spike(daily: list[int], *, min_count: int = 3, z_threshold: float = 3.0
                 ) -> Spike | None:
    if not daily:
        return None
    today, history = daily[-1], daily[:-1]
    exp = baseline(history)
    z = poisson_z(today, exp)
    if today < min_count or z < z_threshold:
        return None
    return Spike(today, exp, z, "HIGH" if z >= 2 * z_threshold else "MEDIUM")


@dataclass
class CrisisAssessment:
    severity: str
    negative_today: int
    expected: float
    z: float
    negative_share: float
    domains: int
    escalation_share: float


def assess_crisis(negative_daily: list[int], total_today: int, *, domains: int,
                  escalation_share: float, min_negative: int = 5, z_threshold: float = 3.0,
                  min_negative_share: float = 0.4) -> CrisisAssessment | None:
    """A crisis = an unusual surge of negative mentions that dominates the conversation.
    Severity grows with how unusual it is, how many distinct sources carry it (propagation)
    and the share of boycott / escalation intents."""
    if not negative_daily:
        return None
    today, history = negative_daily[-1], negative_daily[:-1]
    exp = baseline(history)
    z = poisson_z(today, exp)
    share = round(today / total_today, 3) if total_today else 0.0
    if today < min_negative or z < z_threshold or share < min_negative_share:
        return None
    sev = "MEDIUM"
    if z >= 2 * z_threshold or domains >= 3:
        sev = "HIGH"
    if (z >= 3 * z_threshold and domains >= 5) or (sev == "HIGH" and escalation_share >= 0.3):
        sev = "CRITICAL"
    return CrisisAssessment(sev, today, exp, z, share, domains, escalation_share)


def share_of_voice(counts: dict[str, int]) -> dict[str, float]:
    total = sum(counts.values())
    return {k: round(v / total, 4) if total else 0.0 for k, v in counts.items()}
