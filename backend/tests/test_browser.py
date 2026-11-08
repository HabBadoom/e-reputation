"""Milestone 6 end to end with a real headless Chromium against a local web server.

The address checks are relaxed for exactly one host, 127.0.0.1 (the test server); every other
private address (localhost, 10.0.0.1, cloud metadata…) keeps being refused, which the tests
verify. Static fetches (httpx) are served by a mock transport from the same pages.
"""

import asyncio
import http.server
import importlib.util
import json
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.browser import guard
from app.browser.missions import TRANSITIONS
from app.browser.worker import BrowserWorker
from app.collection import fetcher
from app.connectors import serper as serper_mod
from app.connectors import web as web_mod
from app.core.config import get_settings
from tests import browser_pages as P
from tests.conftest import drain, join

pytest.importorskip("playwright")

HOST = "127.0.0.1"

ROUTES = {
    "/robots.txt": (200, P.ROBOTS, {"content-type": "text/plain"}),
    "/js": (200, P.JS_SHELL, {}),
    "/article": (200, P.ARTICLE, {}),
    "/cf": (403, P.CLOUDFLARE, {"cf-mitigated": "challenge"}),
    "/turnstile": (200, P.TURNSTILE, {}),
    "/internal-redirect": (302, "", {"location": "http://localhost/admin"}),
}


@pytest.fixture
def site(monkeypatch, owner_engine):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: ANN002
            pass

        def do_GET(self):  # noqa: N802
            status, body, headers = ROUTES.get(urlsplit(self.path).path, (404, "not found", {}))
            self.send_response(status)
            ctype = headers.get("content-type", "text/html; charset=utf-8")
            self.send_header("content-type", ctype)
            for k, v in headers.items():
                if k != "content-type":
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body.encode())

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_port

    serper_hits: list[dict] = []

    def transport(req: httpx.Request) -> httpx.Response:
        if req.url.host == "google.serper.dev":
            body = json.loads(req.content)
            hits = serper_hits if body.get("hl") == "fr" and req.url.path == "/search" else []
            return httpx.Response(200, json={"organic": hits})
        status, body, headers = ROUTES.get(req.url.path, (404, "not found", {}))
        return httpx.Response(status, text=body, headers={"content-type": "text/html", **headers})

    client = httpx.Client(transport=httpx.MockTransport(transport), follow_redirects=False)
    monkeypatch.setattr(serper_mod, "serper_client", lambda: client)
    monkeypatch.setattr(fetcher, "http_client", lambda: client)

    original = fetcher.check_url

    def check_url(url: str) -> None:
        if urlsplit(url).hostname == HOST:
            return
        original(url)

    for mod in (fetcher, web_mod, guard):
        monkeypatch.setattr(mod, "check_url", check_url)
    s = get_settings()
    monkeypatch.setattr(s, "serper_api_key", SecretStr("k"))
    monkeypatch.setattr(s, "search_endpoints", ["search"])
    monkeypatch.setattr(s, "browser_stale_seconds", 600)
    with owner_engine.begin() as c:     # no spacing between test visits
        c.execute(text("INSERT INTO browser_domain_policies (domain, max_concurrency, "
                       "min_interval_seconds) VALUES (:d, 5, 0) ON CONFLICT DO NOTHING"),
                  {"d": HOST})
    yield {"base": f"http://{HOST}:{port}", "serper": serper_hits}
    srv.shutdown()
    with owner_engine.begin() as c:
        c.execute(text("DELETE FROM browser_domain_policies WHERE domain = :d"), {"d": HOST})


def run_browser(rounds: int = 1) -> BrowserWorker:
    w = BrowserWorker()

    async def go() -> None:
        try:
            for _ in range(rounds):
                await w.run_once()
        finally:
            await w.runtime.stop()
    asyncio.run(go())
    return w


def _project(api, site, urls):
    site["serper"][:] = [{"title": "Djezzy", "link": u, "snippet": "Djezzy panne réseau",
                          "position": i} for i, u in enumerate(urls)]
    b = api.post("/api/v1/brands", json={"name": "Djezzy"}).json()
    p = api.post("/api/v1/monitoring-projects", json={
        "name": "Veille", "brand_id": b["id"], "languages": ["fr"], "status": "paused",
        "include_competitors": False}).json()
    assert api.post(f"/api/v1/monitoring-projects/{p['id']}/runs").status_code == 202
    drain()
    return api.get(f"/api/v1/monitoring-projects/{p['id']}/runs").json()[0]


def _mention(api, path):
    items = api.get("/api/v1/mentions", params={"limit": 100}).json()["items"]
    return next(m for m in items if m["url"].endswith(path))


def test_transitions_mirror_database():
    path = Path(__file__).parents[1] / "migrations/versions/0007_browser_fleet.py"
    spec = importlib.util.spec_from_file_location("m0007", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.TRANSITIONS == TRANSITIONS


def test_javascript_page_rendered_and_verified(make_user, site):
    api = make_user()
    run = _project(api, site, [site["base"] + "/js",
                                     site["base"] + "/article"])
    assert run["summary"]["browser"]["render_queued"] == 1, run["summary"]
    assert _mention(api, "/article")["verification_status"] == "verified_on_page"
    js = _mention(api, "/js")
    assert js["verification_status"] == "pending_render"

    missions = api.get("/api/v1/browser/missions").json()
    assert missions["by_status"] == {"QUEUED": 1}
    run_browser()
    drain()                                   # re-analysis job queued after rendering
    js = api.get(f"/api/v1/mentions/{js['id']}").json()
    assert js["verification_status"] == "verified_on_page", js
    assert "panne réseau" in js["original_text"]

    mid = api.get("/api/v1/browser/missions").json()["items"][0]["id"]
    d = api.get(f"/api/v1/browser/missions/{mid}").json()
    assert d["status"] == "COMPLETED"
    assert [e["to_status"] for e in d["events"] if e["kind"] == "transition"] == [
        "CREATED", "VALIDATING", "QUEUED", "ASSIGNED", "RUNNING", "COMPLETED"]
    assert d["authorization_record"]["decision"] == "authorized"
    assert d["authorization_record"]["robots"] == "allowed"
    # Fresh context, destroyed afterwards; the page's calls to internal addresses refused.
    assert d["sessions"][0]["cleanup_verified"] is True
    assert d["sessions"][0]["isolation"]["stored_state"] is False
    blocked = next(e for e in d["events"] if e["kind"] == "blocked_request")
    assert set(blocked["detail"]["hosts"]) == {"10.0.0.1", "169.254.169.254"}


def test_static_challenge_answered_autonomously(make_user, site, owner_engine):
    api = make_user()
    run = _project(api, site, [site["base"] + "/cf",
                                     site["base"] + "/other"])
    s = run["summary"]
    assert s["challenges"] == 1 and s["cooldown_skipped"] == 1, s
    assert _mention(api, "/cf")["verification_status"] == "blocked_by_challenge"
    assert _mention(api, "/other")["verification_status"] == "snippet_only"   # not re-knocked

    ch = api.get("/api/v1/browser/challenges").json()
    assert ch["by_kind"] == {"cloudflare_challenge": 1}
    ev = ch["items"][0]
    assert ev["via"] == "http" and ev["state"] == "FALLBACK"
    assert [h["state"] for h in ev["history"]] == ["DETECTED", "PAUSED", "FALLBACK"]
    assert ev["response"]["fallback"] == "search_snippet" and ev["response"]["cooldown_until"]

    src = next(x for x in api.get("/api/v1/sources").json()["items"]
               if x["domain"] == HOST)
    assert src["challenge_count"] == 1 and src["last_challenge_kind"] == "cloudflare_challenge"
    # Manual checks wait for the pause too.
    r = api.post(f"/api/v1/sources/{src['id']}/check").json()
    assert r["detail"].startswith("domain_cooling_down")

    # Third challenge in a row: the source is marked restricted, with the reason.
    with owner_engine.begin() as c:
        c.execute(text("UPDATE sources SET challenge_count = 2, url = :u WHERE domain = :d"),
                  {"u": f"http://{HOST}/cf", "d": HOST})
        c.execute(text("DELETE FROM browser_domain_state"))
    r = api.post(f"/api/v1/sources/{src['id']}/check").json()
    assert r["detail"] == "challenge:cloudflare_challenge"
    assert r["source"]["coverage_status"] == "RESTRICTED"
    assert "not bypassed" in r["source"]["restrictions"]
    assert api.get("/api/v1/browser/challenges").json()["items"][0]["state"] == \
        "SOURCE_RESTRICTED"


def test_browser_challenge_stops_mission(make_user, site):
    api = make_user()
    r = api.post("/api/v1/sources", json={"kind": "website",
                                          "url": site["base"] + "/turnstile"})
    sid = r.json()["source"]["id"]
    assert api.patch(f"/api/v1/sources/{sid}", json={"render_mode": "browser"}).status_code == 200
    m = api.post(f"/api/v1/sources/{sid}/browser-check").json()
    assert m["status"] == "QUEUED", m
    run_browser()
    d = api.get(f"/api/v1/browser/missions/{m['mission_id']}").json()
    assert d["status"] == "CANCELLED" and d["error"] == "challenge:turnstile"
    assert [e["to_status"] for e in d["events"] if e["kind"] == "transition"][-3:] == [
        "RUNNING", "PAUSED", "CANCELLED"]
    assert d["challenges"][0]["via"] == "browser"
    assert d["sessions"][0]["cleanup_verified"] is True
    # Domain paused for everybody: a new visit is refused at validation, not attempted.
    again = api.post(f"/api/v1/sources/{sid}/browser-check").json()
    assert again["status"] == "FAILED" and again["error"] == \
        "not_authorized:domain_cooling_down"


def test_redirect_to_internal_address_blocked(make_user, site):
    api = make_user()
    sid = api.post("/api/v1/sources", json={
        "kind": "website", "url": site["base"] + "/internal-redirect"}
    ).json()["source"]["id"]
    mid = api.post(f"/api/v1/sources/{sid}/browser-check").json()["mission_id"]
    run_browser()
    d = api.get(f"/api/v1/browser/missions/{mid}").json()
    assert d["status"] == "FAILED" and d["error"] == "blocked_address"   # no retry
    assert "localhost" in d["result"]["blocked_hosts"]


def test_missions_permissions_isolation_and_fleet(make_user, site, owner_engine):
    owner = make_user()
    sid = owner.post("/api/v1/sources", json={
        "kind": "website", "url": site["base"] + "/article"}).json()["source"]["id"]
    viewer = join(owner, make_user(), "viewer")
    assert viewer.post(f"/api/v1/sources/{sid}/browser-check").status_code == 403
    mid = owner.post(f"/api/v1/sources/{sid}/browser-check").json()["mission_id"]
    assert viewer.get(f"/api/v1/browser/missions/{mid}").status_code == 200
    assert viewer.post(f"/api/v1/browser/missions/{mid}/cancel").status_code == 403

    other = make_user()
    assert other.get(f"/api/v1/browser/missions/{mid}").status_code == 404
    assert other.post(f"/api/v1/browser/missions/{mid}/cancel").status_code == 404
    assert other.get("/api/v1/browser/missions").json()["total"] == 0
    assert other.post(f"/api/v1/sources/{sid}/browser-check").status_code == 404

    assert owner.post(f"/api/v1/browser/missions/{mid}/cancel").json()["status"] == "CANCELLED"
    assert owner.post(f"/api/v1/browser/missions/{mid}/cancel").status_code == 409

    mid2 = owner.post(f"/api/v1/sources/{sid}/browser-check").json()["mission_id"]
    w = run_browser()
    assert owner.get(f"/api/v1/browser/missions/{mid2}").json()["status"] == "COMPLETED"
    src = next(x for x in owner.get("/api/v1/sources").json()["items"] if x["id"] == sid)
    assert src["coverage_status"] == "AVAILABLE"

    assert owner.get("/api/v1/browser/fleet").status_code == 403
    with owner_engine.begin() as c:
        c.execute(text("UPDATE users SET is_platform_admin = true WHERE email = :e"),
                  {"e": owner.email})
    fleet = owner.get("/api/v1/browser/fleet").json()
    me = next(x for x in fleet["workers"] if x["id"] == str(w.id))
    assert me["max_contexts"] == get_settings().browser_worker_max_contexts
    assert uuid.UUID(me["id"])
