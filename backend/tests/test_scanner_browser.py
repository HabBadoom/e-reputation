"""AynPulse Scanner: built-in browser (scanner-browser service) — WebSocket access rules, live
frames, capture into the agent's collection with provenance, saved session, isolation.

Runs a real headless Chromium against a local test site (private addresses are allowed only via
`scanner_browser_allow_private_networks`, a test setting)."""

import http.server
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import get_settings
from app.scanner.browser import server
from app.scanner.platforms import platform_of
from tests.test_scanner import TOKEN, scanner_signup

pytest.importorskip("playwright")

S = "/api/v1/scanner"
ORIGIN = "http://scanner.test"
PAGE = (b"<!doctype html><title>Page test</title><body style='margin:0'>"
        b"<button style='width:300px;height:80px'>Publication</button>"
        b"<p>Commentaire : service rapide, merci</p></body>")


THREAD = ("<!doctype html><title>Fil</title><body style='margin:0'>"
          "<div style='height:1500px;background:#ddd'>fil d'actualité derrière</div>"
          "<div id=dlg style='position:fixed;left:100px;top:40px;width:600px;height:500px;"
          "overflow-y:auto;background:#fff'>"
          + "".join(f"<p style='height:60px;margin:0'>Commentaire {i} — salam khouya</p>"
                    for i in range(40))
          + "</div></body>").encode()


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            if self.path.startswith("/fil"):                 # a post's comments in a dialog
                self.wfile.write(THREAD)
                return
            self.wfile.write(PAGE + f"<p>{self.path}</p>".encode())   # differs per page

        def log_message(self, *a):  # noqa: ANN002
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture
def browser(monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "scanner_browser_allowed_origins", [ORIGIN])
    monkeypatch.setattr(cfg, "scanner_browser_allow_private_networks", True)
    monkeypatch.setattr(cfg, "scanner_browser_enabled", True)
    with TestClient(server.app) as c:
        yield c


def connect(bc: TestClient, sc, origin: str = ORIGIN):
    headers = {"origin": origin}
    if sc is not None:
        headers["cookie"] = f"sc_access={sc.access}"
    return bc.websocket_connect(server.PATH, headers=headers)


def wait_for(ws, kind: str, limit: int = 400) -> dict:
    frames = 0
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            frames += 1
            continue
        import json
        data = json.loads(msg["text"])
        data["_frames"] = frames
        if data["type"] == kind:
            return data
        if data["type"] == "error":
            raise AssertionError(f"error {data['code']} while waiting for {kind}")
    raise AssertionError(f"no {kind}")


def test_url_filter_blocks_private_addresses_outside_tests():
    assert server._url_allowed("http://127.0.0.1/") is False
    assert server._url_allowed("http://169.254.169.254/latest") is False
    assert server._url_allowed("ftp://example.com/") is False


def test_platform_of():
    assert platform_of("https://m.facebook.com/story.php?id=1") == "facebook"
    assert platform_of("https://www.instagram.com/p/x") == "instagram"
    assert platform_of("https://notfacebook.com/") == "web"
    assert platform_of(None) is None


def test_refuses_foreign_origin_and_missing_login(client, browser):
    sc = scanner_signup(client)
    with pytest.raises(WebSocketDisconnect) as e, connect(browser, sc, "https://evil.example"):
        pass
    assert e.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as e, connect(browser, None):
        pass
    assert e.value.code == 4401


def test_browse_capture_and_saved_session(client, browser, site):
    sc = scanner_signup(client)
    info = sc.get(f"{S}/browser").json()
    assert info["enabled"] is True and info["has_saved_session"] is False
    sid = sc.get(f"{S}/sessions/current").json()["id"]

    with connect(browser, sc) as ws:
        ws.send_json({"type": "start", "session_id": sid, "width": 1000, "height": 700,
                      "mobile": False, "url": site})
        ready = wait_for(ws, "ready")
        assert ready["collection_id"] == sid and ready["signed_in_before"] is False
        url = wait_for(ws, "url")
        while (url["url"], url["title"]) != (site, "Page test"):   # until loaded
            url = wait_for(ws, "url")
        assert url["platform"] == "web"
        ws.send_json({"type": "click", "x": 100, "y": 40})
        ws.send_json({"type": "key", "key": "Control+w"})
        assert wait_for_error(ws) == "bad_key"
        ws.send_json({"type": "navigate", "url": "file:///etc/passwd"})
        assert wait_for_error(ws) == "bad_url"
        ws.send_json({"type": "capture"})
        cap = wait_for(ws, "captured")
        assert cap["_frames"] >= 1          # live JPEG frames reached the client
        assert cap["result"]["status"] == "added" and cap["platform"] == "web"
        ws.send_json({"type": "capture"})   # same screen again → exact duplicate, not stored
        assert wait_for(ws, "captured")["result"]["code"] == "duplicate_image"

    items = sc.get(f"{S}/sessions/{sid}/items").json()
    assert len(items) == 1
    item = items[0]
    assert item["source_type"] == "screenshot" and item["source_url"] == site
    assert item["platform"] == "web" and item["file"]["mime_type"] == "image/png"
    assert item["file"]["original_filename"].startswith("capture-127-0-0-1-")
    assert sc.get(f"{S}/sessions/current").json()["status"] == "collecting"

    # Closing saved the (encrypted) browser state; the agent can forget it.
    info = sc.get(f"{S}/browser").json()
    assert info["has_saved_session"] is True
    r = sc.c.delete(f"{S}/browser/profile",
                    headers={"authorization": f"Bearer {sc.access}", **TOKEN})
    assert r.status_code == 204
    assert sc.get(f"{S}/browser").json()["has_saved_session"] is False


def wait_for_error(ws) -> str:
    import json
    for _ in range(400):
        msg = ws.receive()
        if msg.get("text"):
            data = json.loads(msg["text"])
            if data["type"] == "error":
                return data["code"]
    raise AssertionError("no error")


def test_cannot_capture_into_someone_elses_collection(client, browser, site):
    a, b = scanner_signup(client), scanner_signup(client)
    sid_b = b.get(f"{S}/sessions/current").json()["id"]
    with connect(browser, a) as ws:
        ws.send_json({"type": "start", "session_id": sid_b, "width": 1000, "height": 700})
        assert wait_for_error(ws) in ("session_not_found", "not_found")
    assert b.get(f"{S}/sessions/{sid_b}/items").json() == []


def test_logged_out_session_cannot_open_the_browser(client, browser):
    sc = scanner_signup(client)
    r = sc.post(f"{S}/auth/logout", json={"refresh_token": sc.refresh})
    assert r.status_code in (200, 204), r.text
    with pytest.raises(WebSocketDisconnect) as e, connect(browser, sc):
        pass
    assert e.value.code == 4401


def _open(ws, sid, url):
    ws.send_json({"type": "start", "session_id": sid, "width": 1000, "height": 700, "url": url})
    wait_for(ws, "ready")
    u = wait_for(ws, "url")
    while (u["url"], u["title"]) != (url, "Fil"):
        u = wait_for(ws, "url")


def test_capture_whole_thread(client, browser, site, monkeypatch):
    monkeypatch.setattr(get_settings(), "scanner_thread_settle_ms", 300)
    sc = scanner_signup(client)
    sid = sc.get(f"{S}/sessions/current").json()["id"]
    with connect(browser, sc) as ws:
        _open(ws, sid, site + "fil")
        ws.send_json({"type": "click", "x": 300, "y": 200})        # in the comment dialog
        ws.send_json({"type": "capture_thread"})
        progress = wait_for(ws, "thread_progress", limit=4000)
        assert progress["n"] == 1 and progress["result"]["status"] == "added"
        ws.send_json({"type": "capture"})                          # busy meanwhile
        assert wait_for_error(ws) == "thread_running"
        done = wait_for(ws, "thread_done", limit=8000)
    # 40 × 60 px of comments in a 500 px dialog, 375 px per step: 6 to 7 captures, then the end
    assert done["reason"] == "end" and 5 <= done["added"] <= 8, done
    assert done["duplicates"] == 0 and done["rejected"] == 0
    items = sc.get(f"{S}/sessions/{sid}/items").json()
    assert len(items) == done["added"]
    assert all(i["source_url"] == site + "fil" for i in items)


def test_thread_capture_stops_at_the_limit_or_on_request(client, browser, site, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "scanner_thread_settle_ms", 300)
    monkeypatch.setattr(cfg, "scanner_thread_max_captures", 2)
    sc = scanner_signup(client)
    sid = sc.get(f"{S}/sessions/current").json()["id"]
    with connect(browser, sc) as ws:
        _open(ws, sid, site + "fil")
        ws.send_json({"type": "click", "x": 300, "y": 200})
        ws.send_json({"type": "capture_thread"})
        done = wait_for(ws, "thread_done", limit=8000)
        assert done["reason"] == "limit" and done["added"] == 2
        monkeypatch.setattr(cfg, "scanner_thread_max_captures", 20)
        monkeypatch.setattr(cfg, "scanner_thread_settle_ms", 2000)
        ws.send_json({"type": "capture_thread"})
        wait_for(ws, "thread_progress", limit=4000)
        ws.send_json({"type": "stop_thread"})
        done = wait_for(ws, "thread_done", limit=8000)
        assert done["reason"] == "stopped" and done["added"] <= 2
