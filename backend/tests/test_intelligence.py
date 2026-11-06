"""Milestone 5: analytics, crisis lifecycle, alert rules and channels, webhooks, reports."""

import hashlib
import hmac
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.collection import fetcher
from app.core.config import get_settings
from app.db.models import Mention, MentionAnalysis
from app.db.session import session_scope, set_context
from tests.conftest import drain, join, outbox

NOW = datetime.now(UTC)


def _brand(api, name="Djezzy"):
    r = api.post("/api/v1/brands", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _seed(api, brand_id, n, *, sentiment="negative", entity="Djezzy", etype="brand",
          published=NOW, domains=("a.dz",), intents=(), escalation=0, topics=(),
          about="yes", repost_of=None):
    """Insert analysed mentions directly (collection and AI analysis are tested elsewhere)."""
    ids = []
    with session_scope() as db:
        set_context(db, tenant_id=uuid.UUID(api.tenant_id), user_id=None)
        for i in range(n):
            url = f"https://{domains[i % len(domains)]}/{uuid.uuid4().hex[:10]}"
            m = Mention(id=uuid.uuid4(), tenant_id=uuid.UUID(api.tenant_id),
                        brand_id=uuid.UUID(brand_id), entity_type=etype, entity_name=entity,
                        url=url, canonical_url=url, domain=domains[i % len(domains)],
                        title=f"Titre {i}", original_text=f"texte {i}", snippet="",
                        published_at=published, published_at_source="page_metadata"
                        if published else None, collection_method="rss", connector="rss",
                        connector_version="1", verification_status="feed_item",
                        content_hash=hashlib.sha256(url.encode()).hexdigest(),
                        duplicate_of=uuid.UUID(repost_of) if repost_of else None)
            db.add(m)
            db.flush()
            its = list(intents) + (["boycott_intent"] if i < escalation else [])
            db.add(MentionAnalysis(mention_id=m.id, tenant_id=m.tenant_id, status="done",
                                   sentiment=sentiment, sarcasm="no", about_target=about,
                                   intents=its, topics=list(topics), agreement=1.0,
                                   analysed_at=datetime.now(UTC)))
            ids.append(str(m.id))
    return ids


def _evaluate(api):
    r = api.post("/api/v1/alerts/evaluate")
    assert r.status_code == 202, r.text
    drain()


def _alerts(api, **params):
    r = api.get("/api/v1/alerts", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ----------------------------------------------------------------------------- analytics
def test_overview_counts_reputation_and_share_of_voice(make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "reputation_min_sample", 5)
    api = make_user()
    b = _brand(api)
    pos = _seed(api, b, 4, sentiment="positive")
    _seed(api, b, 2, sentiment="negative")
    _seed(api, b, 1, sentiment="negative", about="no")          # homonym: excluded
    _seed(api, b, 1, sentiment="negative", repost_of=pos[0])    # repost: counted once
    _seed(api, b, 2, sentiment="neutral", etype="competitor", entity="Ooredoo")

    r = api.get("/api/v1/analytics/overview", params={"brand_id": b, "days": 7})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["totals"]["total"] == 6 and o["totals"]["positive"] == 4
    assert o["totals"]["negative"] == 2
    # 50 + 50 × (4 − 2) / 6
    assert o["reputation"]["value"] == pytest.approx(66.7)
    assert o["previous_reputation"]["value"] is None          # nothing before: no invented value
    assert sum(d["total"] for d in o["series"]) == 6 and len(o["series"]) == 7
    sov = {e["entity_name"]: e["share_of_voice"] for e in o["entities"]}
    assert sov == {"Djezzy": pytest.approx(0.75), "Ooredoo": pytest.approx(0.25)}

    monkeypatch.setattr(get_settings(), "reputation_min_sample", 50)
    o = api.get("/api/v1/analytics/overview", params={"brand_id": b}).json()
    assert o["reputation"]["value"] is None and o["reputation"]["sample"] == 6

    other = make_user()
    assert other.get("/api/v1/analytics/overview", params={"brand_id": b}).status_code == 404
    assert other.get("/api/v1/analytics/overview").json()["totals"]["total"] == 0


# -------------------------------------------------------------------- crisis and alerts
def test_crisis_lifecycle_with_default_rules(make_user, owner_engine):
    api = make_user()
    b = _brand(api)
    r = api.post("/api/v1/alert-rules/defaults")
    assert r.status_code == 201 and len(r.json()) == 6
    assert api.post("/api/v1/alert-rules/defaults").json() == []        # idempotent

    _seed(api, b, 8, domains=("a.dz", "b.dz", "c.dz", "d.dz"), escalation=3,
          topics=("réseau",))
    outbox().clear()
    _evaluate(api)

    crises = api.get("/api/v1/crises").json()
    assert len(crises) == 1 and crises[0]["status"] == "active"
    # z = 8 (no history), 4 sources, 3/8 escalation intents → CRITICAL
    assert crises[0]["severity"] == "CRITICAL"
    assert crises[0]["metrics"]["negative_today"] == 8

    kinds = sorted(a["kind"] for a in _alerts(api))
    assert kinds.count("crisis") == 1 and kinds.count("negative_spike") == 1
    assert kinds.count("negative_mention") == 3            # escalation-only default rule
    assert "emerging_topic" in kinds
    crisis_alert = next(a for a in _alerts(api) if a["kind"] == "crisis")
    assert "8" in crisis_alert["body"] and crisis_alert["severity"] == "CRITICAL"

    mails = [m for m in outbox().outbox if "alert" in m.tags]
    assert len(mails) == 2                                 # crisis + spike rules, owner only
    assert any("Crise potentielle" in m.text for m in mails)

    # Re-evaluating the same facts creates nothing new.
    n = len(_alerts(api))
    _evaluate(api)
    assert len(_alerts(api)) == n
    assert api.get("/api/v1/alerts/summary").json()["open"] == n

    # Quiet for longer than the resolution window: the crisis and its alerts are resolved.
    with owner_engine.begin() as c:
        c.execute(text("UPDATE mentions SET published_at = published_at - interval '5 days'"))
        c.execute(text("UPDATE crisis_events SET last_detected_at = now() - interval '3 days'"))
    _evaluate(api)
    crises = api.get("/api/v1/crises").json()
    assert crises[0]["status"] == "resolved" and crises[0]["resolved_at"]
    assert all(a["status"] == "resolved" for a in _alerts(api, kind="crisis"))


def test_old_or_undated_mentions_do_not_trigger_spikes(make_user):
    api = make_user()
    b = _brand(api)
    api.post("/api/v1/alert-rules/defaults")
    _seed(api, b, 10, published=None, domains=("a.dz", "b.dz", "c.dz"))   # backfill, no dates
    _seed(api, b, 10, published=NOW - timedelta(days=40), domains=("a.dz", "b.dz", "c.dz"))
    _evaluate(api)
    assert api.get("/api/v1/crises").json() == []
    assert [a for a in _alerts(api) if a["kind"] in ("negative_spike", "crisis")] == []


def test_competitor_spike_and_min_severity(make_user):
    api = make_user()
    b = _brand(api)
    assert api.post(f"/api/v1/brands/{b}/competitors",
                    json={"name": "Ooredoo"}).status_code == 201
    r = api.post("/api/v1/alert-rules", json={"name": "comp", "kind": "competitor_spike",
                                              "brand_id": b})
    assert r.status_code == 201, r.text
    strict = api.post("/api/v1/alert-rules", json={"name": "strict", "kind": "negative_mention",
                                                   "min_severity": "HIGH"}).json()
    _seed(api, b, 5, sentiment="negative", etype="competitor", entity="Ooredoo")
    _seed(api, b, 2, sentiment="negative")
    _evaluate(api)
    alerts = _alerts(api)
    comp = [a for a in alerts if a["kind"] == "competitor_spike"]
    assert len(comp) == 1 and comp[0]["data"]["competitor"] == "Ooredoo"
    assert comp[0]["data"]["count"] == 5
    assert not [a for a in alerts if a["rule_id"] == strict["id"]]   # LOW < HIGH: filtered


def test_rule_validation_and_permissions(make_user):
    owner = make_user()
    b = _brand(owner)
    viewer = join(owner, make_user(), "viewer")
    analyst = join(owner, make_user(), "analyst")
    body = {"name": "r", "kind": "crisis", "brand_id": b}
    assert viewer.post("/api/v1/alert-rules", json=body).status_code == 403
    assert viewer.get("/api/v1/alert-rules").status_code == 200
    assert viewer.get("/api/v1/webhooks").status_code == 403
    assert analyst.post("/api/v1/alert-rules", json=body).status_code == 201
    assert owner.post("/api/v1/alert-rules", json={**body, "channels": ["webhook"]}
                      ).status_code == 422
    assert owner.post("/api/v1/alert-rules", json={**body, "params": {"rm -rf": 1}}
                      ).status_code == 422
    assert owner.post("/api/v1/alert-rules", json={**body, "kind": "anything"}).status_code == 422

    other = make_user()
    assert other.post("/api/v1/alert-rules", json=body).status_code == 404   # foreign brand
    rid = owner.get("/api/v1/alert-rules").json()[0]["id"]
    assert other.patch(f"/api/v1/alert-rules/{rid}", json={"enabled": False}).status_code == 404
    assert other.get("/api/v1/alert-rules").json() == []
    r = owner.patch(f"/api/v1/alert-rules/{rid}", json={"min_severity": "HIGH",
                                                         "params": {"z_threshold": 4}})
    assert r.status_code == 200 and r.json()["params"] == {"z_threshold": 4.0}
    assert owner.delete(f"/api/v1/alert-rules/{rid}").status_code == 204


def test_acknowledge_and_resolve(make_user):
    api = make_user()
    b = _brand(api)
    api.post("/api/v1/alert-rules", json={"name": "all", "kind": "negative_mention"})
    _seed(api, b, 2)
    _evaluate(api)
    a1, a2 = _alerts(api)
    viewer = join(api, make_user(), "viewer")
    assert viewer.post(f"/api/v1/alerts/{a1['id']}/acknowledge").status_code == 403
    assert api.post(f"/api/v1/alerts/{a1['id']}/acknowledge").json()["status"] == "acknowledged"
    assert api.post(f"/api/v1/alerts/{a2['id']}/resolve").json()["status"] == "resolved"
    assert api.get("/api/v1/alerts/summary").json()["open"] == 0
    assert make_user().post(f"/api/v1/alerts/{a1['id']}/resolve").status_code == 404


# ------------------------------------------------------------------------------ webhooks
@pytest.fixture
def hooks(monkeypatch):
    received: list[httpx.Request] = []
    state = {"status": 200}

    def handler(req: httpx.Request) -> httpx.Response:
        received.append(req)
        return httpx.Response(state["status"], json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr(fetcher, "http_client", lambda: client)
    monkeypatch.setattr(fetcher, "resolve", lambda host: ["10.0.0.5"] if host == "intranet.dz"
                        else ["196.20.1.10"])
    return received, state


def test_webhook_signed_delivery_and_retry(make_user, hooks):
    received, state = hooks
    api = make_user()
    b = _brand(api)
    assert api.post("/api/v1/webhooks", json={"name": "h", "url": "http://hooks.dz/x"}
                    ).status_code == 422
    assert api.post("/api/v1/webhooks", json={"name": "h", "url": "https://intranet.dz/x"}
                    ).status_code == 422                               # private address
    r = api.post("/api/v1/webhooks", json={"name": "SIEM", "url": "https://hooks.example.dz/in"})
    assert r.status_code == 201, r.text
    hook = r.json()
    secret = hook["secret"]
    assert secret.startswith("whsec_")
    assert "secret" not in api.get("/api/v1/webhooks").json()[0]

    t = api.post(f"/api/v1/webhooks/{hook['id']}/test").json()
    assert t["ok"] and received[-1].headers["x-repintel-event"] == "ping"

    api.post("/api/v1/alert-rules", json={"name": "neg", "kind": "negative_mention",
                                          "channels": ["in_app", "webhook"],
                                          "webhook_id": hook["id"]})
    _seed(api, b, 1)
    received.clear()
    _evaluate(api)
    assert len(received) == 1
    req = received[0]
    ts = req.headers["x-repintel-timestamp"]
    expected = "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + req.content,
                                    hashlib.sha256).hexdigest()
    assert hmac.compare_digest(req.headers["x-repintel-signature"], expected)
    payload = json.loads(req.content)
    assert payload["event"] == "alert.created" and payload["alert"]["kind"] == "negative_mention"
    assert payload["tenant_id"] == api.tenant_id

    # Receiver failing: the attempt is recorded and the job is retried later.
    state["status"] = 500
    _seed(api, b, 1)
    received.clear()
    _evaluate(api)
    assert len(received) == 1
    h = api.get("/api/v1/webhooks").json()[0]
    assert h["last_status"] == 500 and h["last_error"] == "HTTP 500"

    other = make_user()
    assert other.post(f"/api/v1/webhooks/{hook['id']}/test").status_code == 404
    assert other.post("/api/v1/alert-rules", json={
        "name": "x", "kind": "crisis", "channels": ["webhook"], "webhook_id": hook["id"]}
    ).status_code == 404

    assert api.delete(f"/api/v1/webhooks/{hook['id']}").status_code == 204
    rule = api.get("/api/v1/alert-rules").json()[0]
    assert rule["webhook_id"] is None and "webhook" not in rule["channels"]


# ------------------------------------------------------------------------------- reports
@pytest.mark.parametrize("fmt", ["csv", "xlsx", "html"])
def test_reports_generate_and_download(make_user, fmt):
    api = make_user()
    b = _brand(api)
    _seed(api, b, 3, sentiment="positive")
    _seed(api, b, 2, sentiment="negative", domains=("x.dz",))
    r = api.post("/api/v1/reports", json={"kind": "weekly", "format": fmt, "brand_id": b,
                                          "locale": "ar"})
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    assert api.get(f"/api/v1/reports/{rid}/download").status_code == 409     # not ready yet
    drain()
    rep = api.get(f"/api/v1/reports/{rid}").json()
    assert rep["status"] == "ready", rep
    d = api.get(f"/api/v1/reports/{rid}/download")
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"]
    if fmt == "csv":
        rows = d.content.decode("utf-8-sig").splitlines()
        assert rows[0].startswith("day,total") and sum(int(x.split(",")[1]) for x in rows[1:8]) == 5
    elif fmt == "xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(d.content))
        assert len(wb.sheetnames) == 6 and wb.worksheets[0].sheet_view.rightToLeft
    else:
        html = d.text
        assert 'dir="rtl"' in html and "<script" not in html
        assert d.headers["content-security-policy"].startswith("default-src 'none'")

    viewer = join(api, make_user(), "viewer")
    assert viewer.get(f"/api/v1/reports/{rid}/download").status_code == 200
    assert viewer.post("/api/v1/reports", json={"kind": "daily"}).status_code == 403
    other = make_user()
    assert other.get(f"/api/v1/reports/{rid}/download").status_code == 404
    assert other.get("/api/v1/reports").json()["total"] == 0


def test_pdf_report(make_user):
    pytest.importorskip("playwright")
    api = make_user()
    b = _brand(api)
    _seed(api, b, 3, sentiment="negative")
    rid = api.post("/api/v1/reports", json={"kind": "monthly", "format": "pdf",
                                            "brand_id": b}).json()["id"]
    drain()
    rep = api.get(f"/api/v1/reports/{rid}").json()
    assert rep["status"] == "ready", rep
    d = api.get(f"/api/v1/reports/{rid}/download")
    assert d.content[:5] == b"%PDF-" and d.headers["content-type"] == "application/pdf"


def test_report_period_validation(make_user):
    api = make_user()
    assert api.post("/api/v1/reports", json={"kind": "custom"}).status_code == 422
    assert api.post("/api/v1/reports", json={
        "kind": "custom", "period_start": "2026-01-02T00:00:00Z",
        "period_end": "2026-01-01T00:00:00Z"}).status_code == 422
    assert api.post("/api/v1/reports", json={
        "kind": "custom", "period_start": "2025-01-01T00:00:00Z",
        "period_end": "2026-06-01T00:00:00Z"}).status_code == 422
    r = api.post("/api/v1/reports", json={
        "kind": "custom", "format": "csv", "period_start": "2026-01-01T00:00:00+01:00",
        "period_end": "2026-02-01T00:00:00+01:00"})
    assert r.status_code == 202, r.text
