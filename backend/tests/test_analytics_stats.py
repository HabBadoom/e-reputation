from app.analytics.stats import (
    assess_crisis,
    detect_spike,
    emerging_topics,
    poisson_z,
    raise_severity,
    reputation_index,
    severity_at_least,
    share_of_voice,
)


def test_poisson_z():
    assert poisson_z(10, 4) == 3.0
    assert poisson_z(3, 0) == 3.0          # baseline floored at 1
    assert poisson_z(2, 4) == -1.0


def test_reputation_index():
    r = reputation_index(6, 2, 2, 0)
    assert r.value == 70.0 and r.net_sentiment == 0.4 and r.sample == 10
    assert reputation_index(0, 10, 0, 0).value == 0.0
    assert reputation_index(10, 0, 0, 0).value == 100.0
    small = reputation_index(3, 1, 0, 0)
    assert small.value is None and small.sample == 4          # not enough data: no score


def test_emerging_topics():
    series = {"réseau": [0] * 27 + [1, 9], "prix": [2] * 28 + [3], "4g": [0] * 28 + [2]}
    found = emerging_topics(series)
    assert [e.topic for e in found] == ["réseau"]              # prix normal, 4g too small
    assert found[0].count == 9 and found[0].z > 3


def test_spike():
    assert detect_spike([1] * 28 + [2]) is None
    s = detect_spike([1] * 28 + [9])
    assert s and s.severity == "HIGH" and s.z == 8.0
    assert detect_spike([0] * 28 + [2]) is None                 # below min count


def test_crisis_assessment():
    calm = [1] * 28
    assert assess_crisis(calm + [3], 10, domains=1, escalation_share=0) is None
    c = assess_crisis(calm + [6], 10, domains=1, escalation_share=0)
    assert c and c.severity == "MEDIUM" and c.negative_share == 0.6
    assert assess_crisis(calm + [6], 30, domains=1, escalation_share=0) is None   # not dominant
    assert assess_crisis(calm + [6], 10, domains=3, escalation_share=0).severity == "HIGH"
    assert assess_crisis(calm + [6], 10, domains=3, escalation_share=0.5).severity == "CRITICAL"
    assert assess_crisis(calm + [12], 15, domains=6, escalation_share=0).severity == "CRITICAL"


def test_severity_helpers_and_share():
    assert severity_at_least("HIGH", "MEDIUM") and not severity_at_least("LOW", "MEDIUM")
    assert raise_severity("HIGH", 3) == "CRITICAL"
    assert share_of_voice({"Djezzy": 60, "Ooredoo": 40}) == {"Djezzy": 0.6, "Ooredoo": 0.4}
    assert share_of_voice({}) == {}
