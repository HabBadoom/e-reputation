"""Milestone 3 end to end: Serper + web pages + RSS (all HTTP mocked), registry, isolation."""

import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.collection import fetcher
from app.connectors import serper as serper_mod
from app.core.config import get_settings
from tests.conftest import drain

PAGE = """<html><head><title>Panne géante chez Djezzy</title>
<meta property="article:published_time" content="2026-11-05T08:30:00+01:00"></head>
<body><article><p>Depuis ce matin, les abonnés de Djezzy signalent une panne du réseau
4G à Alger et Oran.</p></article></body></html>"""
OTHER = "<html><head><title>Rien</title></head><body><p>Météo du jour.</p></body></html>"
FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>APS Economie</title>
<item><title>Djezzy annonce une nouvelle offre</title><link>https://www.aps.dz/eco/1</link>
<description>L'opérateur Djezzy lance une offre 4G.</description>
<pubDate>Thu, 05 Nov 2026 10:00:00 +0100</pubDate></item>
<item><title>Sonatrach signe un contrat</title><link>https://www.aps.dz/eco/2</link>
<description>Energie.</description></item></channel></rss>"""
HOME = """<html><head><title>El Khabar</title>
<link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body>x</body></html>"""

NEWS = [
    {"title": "Panne Djezzy", "link": "https://www.tsa-algerie.com/panne-djezzy?utm_source=gn",
     "snippet": "Les abonnés de Djezzy signalent une panne", "date": "3 hours ago",
     "source": "TSA"},
    {"title": "Panne Djezzy", "link": "https://elwatan-dz.com/a",
     "snippet": "Les abonnés de Djezzy signalent une panne", "date": "2 hours ago",
     "source": "El Watan"},
    {"title": "Djezzy FC gagne", "link": "https://noise.dz/fc",
     "snippet": "Le club Djezzy FC remporte le match", "date": "1 day ago"},
]
ORGANIC = [
    {"title": "Avis Djezzy", "link": "https://blocked.dz/avis", "snippet": "Djezzy service client",
     "position": 1},
    {"title": "Annuaire", "link": "https://ghost.dz/x", "snippet": "Djezzy", "position": 2},
    {"title": "bad", "link": "javascript:alert(1)", "snippet": "Djezzy", "position": 3},
]


@pytest.fixture
def internet(monkeypatch):
    calls: list[httpx.Request] = []
    pages = {
        "https://www.tsa-algerie.com/panne-djezzy?utm_source=gn": (200, PAGE, "text/html"),
        "https://elwatan-dz.com/a": (200, PAGE, "text/html"),
        "https://ghost.dz/x": (200, OTHER, "text/html"),
        "https://blocked.dz/robots.txt": (200, "User-agent: *\nDisallow: /\n", "text/plain"),
        "https://www.aps.dz/rss": (200, FEED, "application/rss+xml"),
        "https://www.aps.dz/html": (200, OTHER, "text/html"),
        "https://elkhabar.dz/": (200, HOME, "text/html"),
    }
    state = {"serper_status": 200}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.host == "google.serper.dev":
            if state["serper_status"] != 200:
                return httpx.Response(state["serper_status"], json={"message": "nope"})
            body = json.loads(req.content)
            if body.get("hl") != "fr":
                return httpx.Response(200, json={"news": [], "organic": []})
            return httpx.Response(200, json={"news": NEWS} if req.url.path == "/news"
                                  else {"organic": ORGANIC})
        if str(req.url) in pages:
            status, content, ctype = pages[str(req.url)]
            return httpx.Response(status, text=content, headers={"content-type": ctype})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr(serper_mod, "serper_client", lambda: client)
    monkeypatch.setattr(fetcher, "http_client", lambda: client)
    monkeypatch.setattr(fetcher, "resolve", lambda host: ["196.20.1.10"])   # public address
    monkeypatch.setattr(get_settings(), "serper_api_key", SecretStr("test-key"))
    monkeypatch.setattr(get_settings(), "search_endpoints", ["news", "search"])
    return {"calls": calls, "state": state}


def _setup(api, *, rss=True):
    b = api.post("/api/v1/brands", json={"name": "Djezzy", "aliases": [{"alias": "جازي"}]}).json()
    api.post(f"/api/v1/brands/{b['id']}/keywords", json={"expression": "djezzy fc",
                                                         "kind": "exclude"})
    if rss:
        r = api.post("/api/v1/sources", json={"kind": "rss", "url": "https://www.aps.dz/rss"})
        assert r.status_code == 201 and r.json()["ok"], r.text
    p = api.post("/api/v1/monitoring-projects", json={
        "name": "Veille", "brand_id": b["id"], "languages": ["fr", "ar"], "status": "paused",
        "include_competitors": False}).json()
    return b["id"], p["id"]


def _run(api, pid):
    assert api.post(f"/api/v1/monitoring-projects/{pid}/runs").status_code == 202
    drain()
    return api.get(f"/api/v1/monitoring-projects/{pid}/runs").json()[0]


def test_full_collection_run(make_user, internet):
    api = make_user()
    brand_id, pid = _setup(api)
    run = _run(api, pid)
    assert run["status"] == "completed", run
    s = run["summary"]
    assert s["connectors"]["serper"]["status"] == "ok"
    assert s["connectors"]["serper"]["queries"] == 4          # 1 group × (fr, ar) × (news, search)
    assert s["connectors"]["rss"] == {"sources": 1, "items": 2, "failed_sources": 0,
                                      "status": "ok", "review_status": "pending_legal_review"}
    assert s["excluded"] == 1 and s["invalid"] == 1 and s["reposts"] == 1
    assert s["mentions_collected"] == 5       # tsa, elwatan (repost), blocked, ghost, rss item
    assert s["verified_on_page"] == 2 and s["page_verification"] == "enabled"

    # Serper was called correctly
    sreq = [c for c in internet["calls"] if c.url.host == "google.serper.dev"]
    assert all(c.headers["x-api-key"] == "test-key" for c in sreq)
    bodies = [json.loads(c.content) for c in sreq]
    assert {b["hl"] for b in bodies} == {"fr", "ar"} and all(b["gl"] == "dz" for b in bodies)
    assert bodies[0]["q"] == '("Djezzy" OR "جازي") -"djezzy fc"' and bodies[0]["tbs"] == "qdr:m"

    items = api.get("/api/v1/mentions").json()["items"]
    by_domain = {m["domain"]: m for m in items}
    assert "noise.dz" not in by_domain                       # excluded keyword
    assert "elwatan-dz.com" not in by_domain                 # repost hidden by default
    tsa = by_domain["tsa-algerie.com"]
    assert tsa["verification_status"] == "verified_on_page"
    assert "panne du réseau" in tsa["original_text"] and tsa["matched_terms"] == ["Djezzy"]
    assert tsa["published_at_source"] == "page_metadata"
    assert tsa["published_at"].startswith("2026-11-05T07:30")
    assert tsa["analysed"] is False                           # no invented sentiment
    assert by_domain["blocked.dz"]["verification_status"] == "blocked_by_robots"
    # Almost empty static page without the brand: handed to the browser fleet for rendering.
    assert by_domain["ghost.dz"]["verification_status"] == "pending_render"
    aps = by_domain["aps.dz"]
    assert aps["verification_status"] == "feed_item" and aps["collection_method"] == "rss"
    assert aps["published_at_source"] == "feed"
    assert "Sonatrach" not in str(items)                     # unrelated feed item ignored

    with_reposts = api.get("/api/v1/mentions", params={"include_reposts": True}).json()
    repost = next(m for m in with_reposts["items"] if m["domain"] == "elwatan-dz.com")
    assert repost["is_repost"] is True
    detail = api.get(f"/api/v1/mentions/{tsa['id']}").json()
    assert detail["canonical_url"] == "https://tsa-algerie.com/panne-djezzy"
    assert detail["provider_fields"]["date"] == "3 hours ago"
    assert detail["connector"] == "serper" and detail["source"]["domain"] == "tsa-algerie.com"

    # Registry: discovered websites with honest coverage states
    srcs = {x["domain"]: x for x in api.get("/api/v1/sources").json()["items"]}
    assert srcs["tsa-algerie.com"]["coverage_status"] == "AVAILABLE"
    assert srcs["tsa-algerie.com"]["discovered_via"] == "search"
    assert srcs["blocked.dz"]["coverage_status"] == "RESTRICTED"
    assert srcs["blocked.dz"]["monitored"] is False

    # Second run: nothing duplicated, sightings counted
    run2 = _run(api, pid)
    assert run2["summary"]["mentions_collected"] == 0
    assert run2["summary"]["already_known"] == 5
    assert run2["summary"]["page_fetches"] == 0              # known URLs are not re-fetched
    assert api.get("/api/v1/mentions").json()["total"] == 4  # reposts hidden by default
    assert api.get(f"/api/v1/mentions/{tsa['id']}").json()["seen_count"] == 2

    dash = api.get("/api/v1/dashboard/summary").json()
    assert dash["mention_stats"]["total"] == 5 and dash["mention_stats"]["reposts"] == 1
    assert dash["mentions"]["available"] is True and dash["sentiment"]["available"] is False


def test_mention_filters_and_csv_export(make_user, internet):
    api = make_user()
    _, pid = _setup(api)
    _run(api, pid)
    r = api.get("/api/v1/mentions", params={"verification": "verified_on_page"}).json()
    assert {m["domain"] for m in r["items"]} == {"tsa-algerie.com"}
    assert api.get("/api/v1/mentions", params={"domain": "aps.dz"}).json()["total"] == 1
    assert api.get("/api/v1/mentions", params={"q": "service client"}).json()["total"] == 1
    later = {"since": "2030-01-01T00:00:00Z"}
    assert api.get("/api/v1/mentions", params=later).json()["total"] == 0

    from sqlalchemy import update

    from app.db.models import Mention
    from app.db.session import session_scope, set_context
    with session_scope() as db:
        set_context(db, tenant_id=api.tenant_id, user_id=None)
        db.execute(update(Mention).where(Mention.domain == "ghost.dz")
                   .values(title='=HYPERLINK("http://evil")'))
    csv_text = api.get("/api/v1/mentions/export.csv").text
    assert "'=HYPERLINK" in csv_text and ',=HYPERLINK' not in csv_text
    assert csv_text.splitlines()[0].lstrip("﻿").startswith("collected_at,")


def test_serper_auth_failure_marks_run_failed(make_user, internet):
    internet["state"]["serper_status"] = 401
    api = make_user()
    _, pid = _setup(api, rss=False)
    run = _run(api, pid)
    assert run["status"] == "failed" and run["summary"]["reason"] == "connectors_failed"
    assert run["summary"]["connectors"]["serper"]["reason"] == "auth_failed"
    assert run["summary"]["connectors"]["serper"]["queries"] == 1   # stopped at first error


def test_production_requires_connector_approval(make_user, internet, monkeypatch, owner_engine):
    api = make_user()
    _, pid = _setup(api, rss=False)
    monkeypatch.setattr(get_settings(), "env", "production")
    run = _run(api, pid)
    assert run["status"] == "skipped"
    assert run["summary"]["reason"] == "connectors_blocked_by_policy"
    assert not [c for c in internet["calls"] if c.url.host == "google.serper.dev"]
    try:
        with owner_engine.begin() as c:
            c.execute(text("UPDATE connector_policies SET review_status='approved' "
                           "WHERE key IN ('serper','web')"))
        assert _run(api, pid)["status"] == "completed"
    finally:
        with owner_engine.begin() as c:
            c.execute(text("UPDATE connector_policies SET review_status='pending_legal_review'"))


def test_sources_api(make_user, internet):
    api = make_user()
    r = api.post("/api/v1/sources", json={"kind": "rss", "url": "https://www.aps.dz/rss"})
    assert r.status_code == 201 and r.json()["source"]["name"] == "APS Economie"
    assert r.json()["source"]["coverage_status"] == "AVAILABLE"
    assert api.post("/api/v1/sources", json={"kind": "rss", "url": "https://www.aps.dz/RSS"}
                    ).status_code == 409
    bad = api.post("/api/v1/sources", json={"kind": "rss", "url": "https://www.aps.dz/html"})
    assert bad.json()["ok"] is False
    assert bad.json()["source"]["coverage_status"] == "CONNECTOR_ERROR"
    assert api.post("/api/v1/sources", json={"kind": "website", "url": "javascript:alert(1)"}
                    ).status_code == 422

    site = api.post("/api/v1/sources", json={"kind": "website", "url": "https://elkhabar.dz/"})
    feeds = site.json()["discovered_feeds"]
    assert [f["url"] for f in feeds] == ["https://elkhabar.dz/feed.xml"]
    assert feeds[0]["enabled"] is False and feeds[0]["discovered_via"] == "feed_discovery"

    # SSRF: registering an internal address is accepted as a record, but never fetched
    before = len(internet["calls"])
    fetcher_resolve = fetcher.resolve
    try:
        fetcher.resolve = lambda host: ["169.254.169.254"]
        r = api.post("/api/v1/sources", json={"kind": "website", "url": "http://metadata.evil/"})
    finally:
        fetcher.resolve = fetcher_resolve
    assert r.json()["ok"] is False and r.json()["detail"] == "blocked_address"
    assert len(internet["calls"]) == before

    sid = site.json()["source"]["id"]
    assert api.patch(f"/api/v1/sources/{sid}", json={"enabled": False}).json()["monitored"] is False
    assert api.delete(f"/api/v1/sources/{sid}").status_code == 204

    conns = {c["key"]: c for c in api.get("/api/v1/connectors").json()}
    assert set(conns) == {"serper", "rss", "web", "browser"}
    assert conns["serper"]["configured"] is True
    assert conns["serper"]["review_status"] == "pending_legal_review"
    assert "Google" in conns["serper"]["legal_basis"]


def test_mentions_and_sources_are_tenant_isolated(make_user, internet):
    a = make_user(org="Org A")
    b = make_user(org="Org B")
    _, pid = _setup(a)
    _run(a, pid)
    m = a.get("/api/v1/mentions").json()["items"][0]["id"]
    s = a.get("/api/v1/sources").json()["items"][0]["id"]
    assert b.get("/api/v1/mentions").json()["total"] == 0
    assert b.get(f"/api/v1/mentions/{m}").status_code == 404
    assert "tsa-algerie" not in b.get("/api/v1/mentions/export.csv").text
    assert b.get("/api/v1/sources").json()["total"] == 0
    for method, path in [("post", f"/api/v1/sources/{s}/check"),
                         ("delete", f"/api/v1/sources/{s}"), ("patch", f"/api/v1/sources/{s}")]:
        kw = {"json": {"enabled": False}} if method == "patch" else {}
        assert getattr(b, method)(path, **kw).status_code == 404
    assert b.get("/api/v1/dashboard/summary").json()["mention_stats"]["total"] == 0


def test_viewer_can_read_but_not_add_sources(make_user, internet):
    from tests.conftest import join
    owner = make_user(org="Org One")
    viewer = join(owner, make_user(org="Org Two"), "viewer")
    assert viewer.get("/api/v1/mentions").status_code == 200
    assert viewer.get("/api/v1/sources").status_code == 200
    assert viewer.post("/api/v1/sources", json={"kind": "rss", "url": "https://www.aps.dz/rss"}
                       ).status_code == 403
