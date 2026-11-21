"""Scanner's built-in browser: a real Chromium on our servers, shown live in the Scanner app.

The agent drives it (clicks, scrolling, typing) and signs in to the dedicated social-media account
of their choice inside it, like in any browser. "Capture" takes a sharp screenshot of what the agent
sees, which then goes through the same sanitisation as uploaded screenshots.

What this module does NOT do, on purpose: no CAPTCHA solving, no fingerprint spoofing or other
anti-detection, no automated browsing — every action comes from the agent. Mobile mode is the
standard Playwright phone emulation (to get the mobile layout), nothing more.

Network safety (defence in depth): every request goes through a filter that only lets http(s)
reach public addresses (same rule as the HTTP fetcher); in production the service also runs on
the isolated `browser` network behind the egress firewall, which also covers redirects and
WebSockets opened by pages.
"""

import asyncio
import base64
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("repintel.scanner.browser")

ALLOWED_KEYS = {
    "Enter", "Backspace", "Tab", "Escape", "Delete", "Home", "End", "PageUp", "PageDown",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space",
}
DESKTOP = {"width": 1280, "height": 800}
MOBILE_DEVICE = "Pixel 7"
MAX_TEXT = 2000


class BrowserError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Viewport:
    width: int
    height: int
    mobile: bool

    @classmethod
    def clamp(cls, width: Any, height: Any, mobile: Any) -> "Viewport":
        def num(v: Any, lo: int, hi: int, default: int) -> int:
            try:
                return max(lo, min(hi, int(v)))
            except (TypeError, ValueError):
                return default
        mobile = bool(mobile)
        if mobile:
            return cls(num(width, 320, 480, 412), num(height, 480, 1000, 860), True)
        return cls(num(width, 800, 1600, DESKTOP["width"]), num(height, 500, 1200,
                                                                  DESKTOP["height"]), False)


@dataclass(frozen=True)
class Capture:
    png: bytes
    url: str
    title: str


def normalise_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        raise BrowserError("bad_url", "Empty address")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        p = urlsplit(raw)
    except ValueError as e:
        raise BrowserError("bad_url", "Invalid address") from e
    if p.scheme not in ("http", "https") or not p.hostname or len(raw) > 2000:
        raise BrowserError("bad_url", "Only web addresses (http/https) can be opened")
    return raw


UrlCheck = Callable[[str], bool]   # blocking; True if the URL may be requested


class RemoteBrowser:
    """One Chromium process for the service; one isolated context per agent session."""

    def __init__(self, *, url_allowed: UrlCheck, extra_args: list[str] | None = None,
                 max_sessions: int = 4):
        self._url_allowed = url_allowed
        self._extra_args = extra_args or []
        self._slots = asyncio.Semaphore(max_sessions)
        self._pw: Any = None
        self.browser: Any = None

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(args=[
            "--disable-dev-shm-usage", "--disable-background-networking",
            "--disable-component-update", "--disable-sync", "--no-first-run",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            *self._extra_args,
        ])

    async def stop(self) -> None:
        if self.browser:
            with contextlib.suppress(Exception):
                await self.browser.close()
        if self._pw:
            with contextlib.suppress(Exception):
                await self._pw.stop()

    @property
    def free_slots(self) -> int:
        return self._slots._value  # noqa: SLF001 - informational only

    async def open(self, *, viewport: Viewport, storage_state: dict | None,
                   on_frame: Callable[[bytes], Awaitable[None]],
                   on_url: Callable[[str, str], Awaitable[None]]) -> "AgentSession":
        if self._slots.locked():
            raise BrowserError("browser_busy", "All built-in browsers are in use; try again soon")
        await self._slots.acquire()
        try:
            if not self.browser or not self.browser.is_connected():
                await self.start()
            opts: dict[str, Any]
            if viewport.mobile:
                opts = dict(self._pw.devices[MOBILE_DEVICE])
                opts["viewport"] = {"width": viewport.width, "height": viewport.height}
                opts.pop("default_browser_type", None)
            else:
                opts = {"viewport": {"width": viewport.width, "height": viewport.height},
                        "device_scale_factor": 1}
            ctx = await self.browser.new_context(
                **opts, storage_state=storage_state or None, accept_downloads=False,
                locale="fr-FR", timezone_id="Africa/Algiers", permissions=[],
                service_workers="block")
            session = AgentSession(ctx, viewport, self._url_allowed, on_frame, on_url,
                                   release=self._slots.release)
            await session._init()
            return session
        except BrowserError:
            self._slots.release()
            raise
        except Exception as e:
            self._slots.release()
            log.exception("scanner_browser_open_failed")
            raise BrowserError("browser_unavailable", "The built-in browser could not start") from e


class AgentSession:
    def __init__(self, ctx: Any, viewport: Viewport, url_allowed: UrlCheck,
                 on_frame: Callable[[bytes], Awaitable[None]],
                 on_url: Callable[[str, str], Awaitable[None]], *, release: Callable[[], None]):
        self.ctx, self.viewport = ctx, viewport
        self._url_allowed, self._on_frame, self._on_url = url_allowed, on_frame, on_url
        self._release = release
        self.page: Any = None
        self._cdp: Any = None
        self._allowed: dict[str, bool] = {}
        self._sending = False
        self.closed = False
        self.last_activity = time.monotonic()
        self.blocked_requests = 0

    # -- setup -----------------------------------------------------------------------------
    async def _host_ok(self, url: str) -> bool:
        p = urlsplit(url)
        key = f"{p.scheme}://{p.hostname}:{p.port}"
        if key not in self._allowed:
            self._allowed[key] = await asyncio.to_thread(self._url_allowed, url)
        return self._allowed[key]

    async def _route(self, route: Any) -> None:
        url = route.request.url
        scheme = urlsplit(url).scheme
        local = scheme in ("data", "blob", "about")
        if local or (scheme in ("http", "https") and await self._host_ok(url)):
            await route.continue_()
        else:
            self.blocked_requests += 1
            await route.abort("blockedbyclient")

    async def _init(self) -> None:
        await self.ctx.route("**/*", self._route)
        self.ctx.on("page", self._adopt_popup)
        self.page = await self.ctx.new_page()
        self.page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        self.page.on("framenavigated", self._navigated)
        self.page.on("load", lambda _: asyncio.ensure_future(self._report_url()))  # final title
        self._cdp = await self.ctx.new_cdp_session(self.page)
        self._cdp.on("Page.screencastFrame", self._frame)
        scale = 2 if self.viewport.mobile else 1
        await self._cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": 60, "everyNthFrame": 1,
            "maxWidth": self.viewport.width * scale, "maxHeight": self.viewport.height * scale})

    def _adopt_popup(self, popup: Any) -> None:
        """Links that open a new tab are opened in the single visible tab instead."""
        if self.page is None or popup == self.page:
            return

        async def adopt() -> None:
            with contextlib.suppress(Exception):
                await popup.wait_for_load_state("domcontentloaded", timeout=10_000)
            url = popup.url
            with contextlib.suppress(Exception):
                await popup.close()
            if url and url != "about:blank":
                with contextlib.suppress(Exception):
                    await self.page.goto(url, wait_until="commit")
        asyncio.ensure_future(adopt())

    def _navigated(self, frame: Any) -> None:
        if frame == self.page.main_frame:
            asyncio.ensure_future(self._report_url())

    async def _report_url(self) -> None:
        title = ""
        with contextlib.suppress(Exception):
            title = await self.page.title()
        with contextlib.suppress(Exception):
            await self._on_url(self.page.url, title)

    def _frame(self, params: dict) -> None:
        asyncio.ensure_future(self._deliver(params))

    async def _deliver(self, params: dict) -> None:
        with contextlib.suppress(Exception):
            await self._cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        if self._sending or self.closed:
            return     # drop frames while the previous one is still being sent (slow links)
        self._sending = True
        try:
            await self._on_frame(base64.b64decode(params["data"]))
        except Exception:  # noqa: BLE001 - the client went away; the server loop ends the session
            log.debug("scanner_browser_frame_dropped")
        finally:
            self._sending = False

    # -- agent actions ----------------------------------------------------------------------
    def touch(self) -> None:
        self.last_activity = time.monotonic()

    async def navigate(self, raw: str) -> None:
        url = normalise_url(raw)
        if not await self._host_ok(url):
            raise BrowserError("blocked_address", "This address cannot be opened")
        try:
            await self.page.goto(url, wait_until="commit", timeout=30_000)
        except Exception as e:
            raise BrowserError("navigation_failed", "The page could not be opened") from e

    async def click(self, x: float, y: float) -> None:
        x = max(0.0, min(float(x), self.viewport.width - 1))
        y = max(0.0, min(float(y), self.viewport.height - 1))
        if self.viewport.mobile:
            await self.page.touchscreen.tap(x, y)
        else:
            await self.page.mouse.click(x, y)

    async def scroll(self, dx: float, dy: float, x: float | None = None,
                     y: float | None = None) -> None:
        dx = max(-3000.0, min(float(dx), 3000.0))
        dy = max(-3000.0, min(float(dy), 3000.0))
        await self.page.mouse.move(float(x if x is not None else self.viewport.width / 2),
                                   float(y if y is not None else self.viewport.height / 2))
        await self.page.mouse.wheel(dx, dy)

    async def type(self, text: str) -> None:
        await self.page.keyboard.type(str(text)[:MAX_TEXT])

    async def key(self, key: str) -> None:
        if key not in ALLOWED_KEYS:
            raise BrowserError("bad_key", "Key not allowed")
        await self.page.keyboard.press(" " if key == "Space" else key)

    async def history(self, action: str) -> None:
        with contextlib.suppress(Exception):
            if action == "back":
                await self.page.go_back(wait_until="commit", timeout=15_000)
            elif action == "forward":
                await self.page.go_forward(wait_until="commit", timeout=15_000)
            elif action == "reload":
                await self.page.reload(wait_until="commit", timeout=30_000)

    async def capture(self, *, full_page: bool = False) -> Capture:
        try:
            png = await self.page.screenshot(type="png", full_page=full_page,
                                             animations="disabled", timeout=20_000)
        except Exception as e:
            raise BrowserError("capture_failed", "The screen could not be captured") from e
        title = ""
        with contextlib.suppress(Exception):
            title = await self.page.title()
        return Capture(png=png, url=self.page.url, title=title)

    async def storage_state(self) -> dict | None:
        try:
            return await self.ctx.storage_state()
        except Exception:  # noqa: BLE001
            return None

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            with contextlib.suppress(Exception):
                await self._cdp.send("Page.stopScreencast")
            with contextlib.suppress(Exception):
                await self.ctx.close()
        finally:
            self._release()
