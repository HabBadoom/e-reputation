"""Aynpulse Scanner, phase 5: pasted links — cleaning, once per collection, isolation, and
captures taken from a link in the built-in browser (real Chromium)."""

import pytest

from app.scanner import links
from tests.test_scanner import TOKEN, scanner_signup
from tests.test_scanner_browser import browser, connect, site, wait_for  # noqa: F401
from tests.test_scanner_uploads import current

S = "/api/v1/scanner"


def test_clean_and_key():
    assert links.clean("https://www.facebook.com/share/p/1AbC/?mibextid=WC7FNe") == \
        "https://www.facebook.com/share/p/1AbC/"
    assert links.clean("m.facebook.com/story.php?story_fbid=1&id=2&fbclid=IwAR") == \
        "https://m.facebook.com/story.php?story_fbid=1&id=2"
    assert links.clean("  <https://youtu.be/abc?si=x&t=42>  ") == "https://youtu.be/abc?t=42"
    same = [links.key_of(links.clean(u)) for u in (
        "https://m.facebook.com/story.php?story_fbid=1&id=2",
        "https://web.facebook.com/story.php?id=2&story_fbid=1&_rdr",
        "https://www.facebook.com/story.php/?story_fbid=1&id=2#comments")]
    assert len(set(same)) == 1
    assert links.key_of(links.clean("facebook.com/groups/a/posts/1/?comment_id=5")) != \
        links.key_of(links.clean("facebook.com/groups/a/posts/1/"))
    for bad in ("", "javascript:alert(1)", "ftp://x.com/a", "localhost/x",
                "https://user:pw@evil.com/", "https://x.com/" + "a" * 2100):
        with pytest.raises(links.BadLink):
            links.clean(bad)


def test_pasted_link_is_kept_once(client):
    sc = scanner_signup(client)
    sid = current(sc)
    r = sc.post(f"{S}/sessions/{sid}/links",
                json={"url": "https://m.facebook.com/story.php?story_fbid=1&id=2&fbclid=X"})
    assert r.status_code == 200, r.text
    link = r.json()
    assert link["created"] is True and link["platform"] == "facebook"
    assert link["url"] == "https://m.facebook.com/story.php?story_fbid=1&id=2"
    again = sc.post(f"{S}/sessions/{sid}/links",
                    json={"url": "web.facebook.com/story.php?id=2&story_fbid=1"}).json()
    assert again == {**link, "created": False}

    items = sc.get(f"{S}/sessions/{sid}/items").json()
    assert len(items) == 1
    it = items[0]
    assert it["source_type"] == "url" and it["status"] == "pending" and it["captures"] == 0
    assert it["file"] is None and it["extraction"] is None
    s = sc.get(f"{S}/sessions/current").json()
    assert s["status"] == "collecting" and s["counters"]["urls"] == 1

    r = sc.post(f"{S}/sessions/{sid}/links", json={"url": "javascript:alert(1)"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_url"


def test_links_are_private(client):
    a, b = scanner_signup(client), scanner_signup(client)
    sid_a = current(a)
    assert b.post(f"{S}/sessions/{sid_a}/links",
                  json={"url": "https://facebook.com/x"}).status_code == 404


def test_captures_taken_from_a_link_are_attached(client, browser, site):  # noqa: F811
    sc = scanner_signup(client)
    sid = current(sc)
    link = sc.post(f"{S}/sessions/{sid}/links", json={"url": site}).json()

    with connect(browser, sc) as ws:
        ws.send_json({"type": "start", "session_id": sid, "width": 1000, "height": 700,
                      "link_item_id": link["item_id"]})
        wait_for(ws, "ready")
        linked = wait_for(ws, "linked")
        assert linked["item_id"] == link["item_id"] and linked["url"] == site
        url = wait_for(ws, "url")
        while (url["url"], url["title"]) != (site, "Page test"):
            url = wait_for(ws, "url")
        ws.send_json({"type": "capture"})
        assert wait_for(ws, "captured")["result"]["status"] == "added"
        ws.send_json({"type": "link", "item_id": None})          # detach
        assert wait_for(ws, "linked")["item_id"] is None
        ws.send_json({"type": "navigate", "url": site + "autre-page"})
        url = wait_for(ws, "url")
        while (url["url"], url["title"]) != (site + "autre-page", "Page test"):
            url = wait_for(ws, "url")
        ws.send_json({"type": "capture"})
        assert wait_for(ws, "captured")["result"]["status"] == "added"
        ws.send_json({"type": "link", "item_id": "00000000-0000-0000-0000-000000000000"})
        err = wait_for_error_code(ws)
        assert err == "link_not_found"

    items = {i["source_type"] + str(bool(i["link_item_id"])): i
             for i in sc.get(f"{S}/sessions/{sid}/items").json()}
    assert items["urlFalse"]["status"] == "ready" and items["urlFalse"]["captures"] == 1
    assert items["screenshotTrue"]["link_item_id"] == link["item_id"]
    assert "screenshotFalse" in items                            # taken after detaching

    # Removing the link keeps its capture, no longer attached.
    r = sc.c.delete(f"{S}/items/{link['item_id']}",
                    headers={"authorization": f"Bearer {sc.access}", **TOKEN})
    assert r.status_code == 200, r.text
    left = sc.get(f"{S}/sessions/{sid}/items").json()
    assert [i["source_type"] for i in left] == ["screenshot", "screenshot"]
    assert all(i["link_item_id"] is None for i in left)


def wait_for_error_code(ws) -> str:
    import json
    for _ in range(400):
        msg = ws.receive()
        if msg.get("text"):
            data = json.loads(msg["text"])
            if data["type"] == "error":
                return data["code"]
    raise AssertionError("no error")
