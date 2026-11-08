"""Browser Context Manager: one Chromium per worker, one fresh isolated context per mission.

Isolation: new context per mission (no stored profile, cookies, local storage, cache or
service workers carried over), destroyed right after the visit; downloads, popups,
permissions and dialogs refused. Every request — navigation, redirects, scripts, XHR, iframes —
is checked against the same address rules as the HTTP fetcher; heavy resources are skipped.
The browser identifies itself honestly (crawler user agent) and does not interact with pages.
"""

import asyncio
import contextlib
import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.browser.detect import Challenge, Observation, detect
from app.core.config import get_settings

log = logging.getLogger("repintel.browser")

MAX_HTML = 2_000_000
MAX_REDIRECTS = 5


@dataclass
class VisitResult:
    url: str
    final_url: str = ""
    status: int = 0
    title: str = ""
    html: str = ""
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    frame_urls: list[str] = field(default_factory=list)
    challenge: Challenge | None = None
    blocked: dict[str, int] = field(default_factory=dict)
    blocked_hosts: list[str] = field(default_factory=list)
    redirects: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None
    cleanup_verified: bool = False

    def summary(self) -> dict[str, Any]:
        return {"final_url": self.final_url[:500], "status": self.status,
                "title": self.title[:200], "text_length": len(self.text),
                "elapsed_ms": self.elapsed_ms, "blocked": self.blocked,
                "blocked_hosts": self.blocked_hosts[:10], "redirects": self.redirects[:5],
                "error": self.error,
                "challenge": self.challenge.kind if self.challenge else None}


ISOLATION = {"fresh_context": True, "stored_state": False, "service_workers": "block",
             "downloads": False, "permissions": [], "javascript": True,
             "user_agent": "crawler (honest)"}


class BrowserRuntime:
    def __init__(self) -> None:
        self._pw: Any = None
        self.browser: Any = None
        self.visits = 0
        self.restarts = 0

    async def start(self) -> None:
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        await self._launch()

    async def _launch(self) -> None:
        self.browser = await self._pw.chromium.launch(args=[
            "--disable-dev-shm-usage", "--disable-background-networking",
            "--disable-component-update", "--disable-sync", "--no-first-run",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            *get_settings().browser_extra_args,
        ])

    async def ensure_healthy(self) -> None:
        """Browser Health Monitor: relaunch a crashed browser, recycle it periodically."""
        limit = get_settings().browser_restart_after_missions
        if self.browser is None or not self.browser.is_connected() or (
                limit and self.visits and self.visits % limit == 0 and not self._busy()):
            if self.browser is not None:
                with contextlib.suppress(Exception):    # already gone
                    await self.browser.close()
            await self._launch()
            self.restarts += 1

    def _busy(self) -> bool:
        return bool(self.browser and any(c.pages for c in self.browser.contexts))

    async def stop(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self._pw is not None:
            await self._pw.stop()
        self.browser = self._pw = None

    async def visit(self, url: str, *, timeout_seconds: int, block_resources: list[str],
                    allow: Callable[[str], bool]) -> VisitResult:
        s = get_settings()
        res = VisitResult(url=url)
        blocked: Counter[str] = Counter()
        hosts: set[str] = set()
        allowed_cache: dict[str, bool] = {}
        started = time.monotonic()
        ctx = await self.browser.new_context(
            user_agent=s.crawler_user_agent, locale="fr-DZ", timezone_id="Africa/Algiers",
            java_script_enabled=True, accept_downloads=False, service_workers="block",
            permissions=[], viewport={"width": 1280, "height": 900}, ignore_https_errors=False,
            extra_http_headers={"accept-language": "fr,ar;q=0.9,en;q=0.5"})
        self.visits += 1

        pending: dict[str, str] = {}      # main-frame redirect we must re-check

        async def checked(u: str) -> bool:
            p = urlsplit(u)
            if p.scheme not in ("http", "https"):
                return False
            key = f"{p.scheme}://{p.netloc}/"
            if key not in allowed_cache:
                allowed_cache[key] = await asyncio.to_thread(allow, key)
            return allowed_cache[key]

        def refuse(reason: str, u: str) -> None:
            blocked[reason] += 1
            if reason == "address":
                hosts.add(urlsplit(u).hostname or "")

        async def route(r: Any) -> None:
            req = r.request
            if urlsplit(req.url).scheme in ("data", "blob", "about"):
                await r.continue_()
                return
            if urlsplit(req.url).scheme not in ("http", "https"):
                refuse("scheme", req.url)
                await r.abort("blockedbyclient")
                return
            if req.resource_type in block_resources:
                refuse("resource", req.url)
                await r.abort("blockedbyclient")
                return
            if not await checked(req.url):
                refuse("address", req.url)
                await r.abort("blockedbyclient")
                return
            # Requests are fetched here WITHOUT following redirects, and every hop is checked:
            # Chromium's own redirect handling bypasses route interception, so a public URL
            # could otherwise bounce the browser to an internal address.
            target = req.url
            for _ in range(MAX_REDIRECTS + 1):
                try:
                    upstream = await r.fetch(url=target, max_redirects=0,
                                             timeout=timeout_seconds * 1000)
                except Exception:  # noqa: BLE001 - network error: the page sees a failure
                    blocked["network_error"] += 1
                    await r.abort("failed")
                    return
                location = upstream.headers.get("location")
                if not (300 <= upstream.status < 400 and location):
                    await r.fulfill(response=upstream)
                    return
                nxt = urljoin(target, location)
                if not await checked(nxt):
                    refuse("address", nxt)
                    await r.abort("blockedbyclient")
                    return
                if req.is_navigation_request() and req.frame.parent_frame is None:
                    pending["url"] = nxt            # re-navigate so page.url is right
                    await r.abort("blockedbyclient")
                    return
                target = nxt
            refuse("too_many_redirects", target)
            await r.abort("blockedbyclient")

        try:
            await ctx.route("**/*", route)
            async def new_page() -> Any:
                pg = await ctx.new_page()
                pg.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
                pg.on("popup", lambda p: asyncio.ensure_future(p.close()))
                pg.set_default_timeout(timeout_seconds * 1000)
                return pg

            page = await new_page()
            current, resp = url, None
            for _ in range(MAX_REDIRECTS + 1):
                pending.clear()
                try:
                    resp = await page.goto(current, wait_until="domcontentloaded",
                                           timeout=timeout_seconds * 1000)
                except Exception:
                    if "url" not in pending:
                        raise
                    current = pending["url"]
                    res.redirects.append(current)
                    await page.close()      # a fresh tab: the aborted one shows an error page
                    page = await new_page()
                    continue
                break
            else:
                raise RuntimeError("too many redirects")
            with contextlib.suppress(Exception):    # long-polling pages never go idle
                await page.wait_for_load_state("networkidle",
                                               timeout=min(8, timeout_seconds) * 1000)
            res.final_url = page.url
            res.status = resp.status if resp is not None else 0
            res.headers = await resp.all_headers() if resp is not None else {}
            res.title = (await page.title())[:500]
            res.html = (await page.content())[:MAX_HTML]
            try:
                res.text = (await page.inner_text("body", timeout=3000))[:500_000]
            except Exception:  # noqa: BLE001 - no body (e.g. XML)
                res.text = ""
            res.frame_urls = [f.url for f in page.frames if f.url and f.url != "about:blank"]
            res.challenge = detect(Observation(
                status=res.status, url=res.final_url, html=res.html, title=res.title,
                text_length=len(" ".join(res.text.split())), headers=res.headers,
                frame_urls=res.frame_urls, rendered=True))
        except Exception as e:  # noqa: BLE001 - navigation errors are mission results
            name = type(e).__name__
            msg = str(e).split("\n")[0][:200]
            if "Timeout" in name:
                res.error = "timeout"
            elif "ERR_BLOCKED_BY_CLIENT" in msg and blocked["address"]:
                res.error = "blocked_address"
            else:
                res.error = f"navigation_error: {msg}"
        finally:
            try:
                await ctx.close()
                res.cleanup_verified = ctx not in self.browser.contexts
            except Exception:  # noqa: BLE001
                res.cleanup_verified = False
            res.blocked = dict(blocked)
            res.blocked_hosts = sorted(h for h in hosts if h)
            res.elapsed_ms = int((time.monotonic() - started) * 1000)
        return res
