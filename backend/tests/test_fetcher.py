"""SSRF protections and robots.txt handling of the page/feed fetcher."""

import httpx
import pytest

from app.collection import fetcher
from app.collection.fetcher import FetchError, check_url, safe_get
from app.collection.robots import RobotsCache

PUBLIC = {"news.example.dz": ["196.20.1.10"], "redirect.example.dz": ["196.20.1.11"],
          "evil.example.com": ["169.254.169.254"], "mixed.example.com": ["8.8.8.8", "10.0.0.5"],
          "v6.example.com": ["::ffff:127.0.0.1"]}


@pytest.fixture
def web(monkeypatch):
    routes: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    monkeypatch.setattr(fetcher, "http_client", lambda: client)
    monkeypatch.setattr(fetcher, "resolve", lambda host: PUBLIC.get(host, []))
    return routes


@pytest.mark.parametrize("url,reason", [
    ("http://127.0.0.1/admin", "blocked_address"),
    ("http://169.254.169.254/latest/meta-data/", "blocked_address"),
    ("http://[::1]/", "blocked_address"),
    ("http://10.1.2.3/", "blocked_address"),
    ("http://evil.example.com/", "blocked_address"),        # DNS points at metadata service
    ("http://mixed.example.com/", "blocked_address"),       # any private address is enough
    ("http://v6.example.com/", "blocked_address"),          # IPv4-mapped loopback
    ("http://news.example.dz:8080/", "blocked_port"),
    ("file:///etc/passwd", "bad_url"),
    ("gopher://news.example.dz/", "bad_url"),
    ("http://user:pw@news.example.dz/", "bad_url"),
    ("http://unknown.example.dz/", "dns_failure"),
])
def test_unsafe_urls_are_refused(web, url, reason):
    with pytest.raises(FetchError) as e:
        check_url(url)
    assert e.value.reason == reason


def test_redirect_to_internal_address_is_refused(web):
    web["https://redirect.example.dz/a"] = httpx.Response(
        302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    with pytest.raises(FetchError) as e:
        safe_get("https://redirect.example.dz/a")
    assert e.value.reason == "blocked_address"


def test_follows_safe_redirects_and_caps_size(web):
    web["https://redirect.example.dz/a"] = httpx.Response(
        301, headers={"location": "https://news.example.dz/article"})
    web["https://news.example.dz/article"] = httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, content="مرحبا".encode())
    r = safe_get("https://redirect.example.dz/a")
    assert r.url == "https://news.example.dz/article" and r.text() == "مرحبا"
    web["https://news.example.dz/big"] = httpx.Response(200, content=b"x" * 5000)
    with pytest.raises(FetchError) as e:
        safe_get("https://news.example.dz/big", max_bytes=1000)
    assert e.value.reason == "too_large"


def test_redirect_loop(web):
    web["https://news.example.dz/loop"] = httpx.Response(
        302, headers={"location": "https://news.example.dz/loop"})
    with pytest.raises(FetchError) as e:
        safe_get("https://news.example.dz/loop")
    assert e.value.reason == "too_many_redirects"


def test_robots(web):
    web["https://news.example.dz/robots.txt"] = httpx.Response(200, text=(
        "User-agent: *\nDisallow: /private/\n\nUser-agent: RepIntelBot\nDisallow: /nobots/\n"))
    rc = RobotsCache()
    assert rc.allowed("https://news.example.dz/article")
    assert not rc.allowed("https://news.example.dz/nobots/x")
    # no robots.txt (404) → allowed; server error → disallowed
    assert rc.allowed("https://redirect.example.dz/x")
    web["https://redirect.example.dz/robots.txt"] = httpx.Response(503)
    assert not RobotsCache().allowed("https://redirect.example.dz/x")
