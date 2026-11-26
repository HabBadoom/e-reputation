"""scanner-browser service: the WebSocket endpoint behind Scanner's built-in browser.

Run with `uvicorn app.scanner.browser.server:app --port 8100` (container `scanner-browser`, on
the isolated `browser` network in production). One live browser per Scanner account.

Protocol on /api/v1/scanner/browser/ws:
  client → server (JSON): start {session_id, width, height, mobile, url?, link_item_id?};
    link {item_id | null} (captures are attached to this pasted link, which opens); navigate {url};
    click {x, y}; scroll {dx, dy, x?, y?}; type {text}; key {key}; back; forward; reload;
    capture {full_page?}; capture_thread (scroll and capture the thread under the last click
    until its end); stop_thread; ping
  server → client: binary JPEG frames; JSON ready, url {url, title}, linked {item_id, url},
    captured {result}, thread_progress {n, result}, thread_done {added, duplicates,
    rejected, reason: end|limit|stopped|full|error},
    error {code}, closed {reason}

Security: the Origin must be the Scanner app's; the `sc_access` cookie must be a valid Scanner
token with a live session and an active account (re-checked every 30 s, so logging out or a
disabled account closes the browser); the collection must be the account's own open one.
Keystrokes and page contents are never logged.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import get_limiter
from app.core.security import decode_scanner_access_token
from app.db.session import session_scope, set_scanner_context
from app.scanner import sessions, uploads
from app.scanner.audit import record
from app.scanner.browser import profiles
from app.scanner.browser.session import AgentSession, BrowserError, RemoteBrowser, Viewport
from app.scanner.deps import ACCESS_COOKIE, check_session
from app.scanner.platforms import platform_of

log = logging.getLogger("repintel.scanner.browser")
PATH = "/api/v1/scanner/browser/ws"


def _url_allowed(url: str) -> bool:
    if get_settings().scanner_browser_allow_private_networks:   # tests only
        return urlsplit(url).scheme in ("http", "https")
    from app.collection.fetcher import FetchError, check_url
    try:
        check_url(url)
    except FetchError:
        return False
    return True


class Service:
    def __init__(self) -> None:
        s = get_settings()
        self.browser = RemoteBrowser(url_allowed=_url_allowed, extra_args=s.browser_extra_args,
                                     max_sessions=s.scanner_browser_max_sessions)
        self.live: dict[uuid.UUID, Connection] = {}


service: Service | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):  # noqa: ANN201
    global service
    service = Service()
    await service.browser.start()
    try:
        yield
    finally:
        for conn in list(service.live.values()):
            await conn.end("shutdown")
        await service.browser.stop()


app = FastAPI(title="Aynpulse Scanner browser", lifespan=lifespan, docs_url=None,
              redoc_url=None, openapi_url=None)


@app.get("/healthz")
def healthz() -> dict:
    ok = bool(service and service.browser.browser and service.browser.browser.is_connected())
    return {"status": "ok" if ok else "starting",
            "free_sessions": service.browser.free_slots if service else 0}


def _allowed_origins() -> set[str]:
    s = get_settings()
    if s.scanner_browser_allowed_origins:
        return {o.rstrip("/") for o in s.scanner_browser_allowed_origins}
    p = urlsplit(s.scanner_base_url)
    return {f"{p.scheme}://{p.netloc}"}


def _authenticate(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    claims = decode_scanner_access_token(token)
    with session_scope() as db:
        check_session(db, claims.account_id, claims.session_id)
    return claims.account_id, claims.session_id


def _open_collection(account_id: uuid.UUID, session_id: Any) -> tuple[uuid.UUID, dict | None]:
    with session_scope() as db:
        set_scanner_context(db, account_id=account_id)
        s = sessions.get(db, uuid.UUID(str(session_id))) if session_id \
            else sessions.current(db, account_id)[0]
        uploads.require_open(s)
        return s.id, profiles.load(db, account_id)


def _link(account_id: uuid.UUID, session_id: uuid.UUID, item_id: Any) -> tuple[uuid.UUID, str]:
    from app.scanner import links
    with session_scope() as db:
        set_scanner_context(db, account_id=account_id)
        item = links.get_link(db, uuid.UUID(str(item_id)), session_id)
        return item.id, item.source_url or ""


def _store_capture(account_id: uuid.UUID, session_id: uuid.UUID, png: bytes, url: str,
                   link_item_id: uuid.UUID | None = None) -> dict:
    cfg = get_settings()
    get_limiter().hit(f"scanner:uploads:{account_id}", cfg.scanner_uploads_per_hour, 3600)
    host = (urlsplit(url).hostname or "page").replace(".", "-")[:60]
    name = f"capture-{host}-{datetime.now(UTC):%Y%m%d-%H%M%S}.png"
    with session_scope() as db:
        set_scanner_context(db, account_id=account_id)
        s = sessions.get(db, session_id)
        result = uploads.add_screenshots(db, s, [(name, png)], source_url=url,
                                         platform=platform_of(url),
                                         link_item_id=link_item_id)[0]
        if result.status == "added":
            record(db, account_id=account_id, action="screenshots.added",
                   target_type="collection_session", target_id=s.id,
                   meta={"added": 1, "via": "built_in_browser", "platform": platform_of(url),
                         "from_link": bool(link_item_id)})
        return {"filename": result.filename, "status": result.status, "code": result.code,
                "item_id": str(result.item_id) if result.item_id else None}


def _save_profile(account_id: uuid.UUID, state: dict) -> None:
    with session_scope() as db:
        set_scanner_context(db, account_id=account_id)
        profiles.save(db, account_id, state)


class Connection:
    def __init__(self, ws: WebSocket, account_id: uuid.UUID, family_id: uuid.UUID):
        self.ws, self.account_id, self.family_id = ws, account_id, family_id
        self.session: AgentSession | None = None
        self.collection_id: uuid.UUID | None = None
        self.link_item_id: uuid.UUID | None = None
        self.thread_task: asyncio.Task | None = None
        self.thread_stop = False
        self._send_lock = asyncio.Lock()
        self.ended = False

    async def send_json(self, data: dict) -> None:
        async with self._send_lock:
            await self.ws.send_json(data)

    async def send_frame(self, data: bytes) -> None:
        async with self._send_lock:
            await self.ws.send_bytes(data)

    async def on_url(self, url: str, title: str) -> None:
        await self.send_json({"type": "url", "url": url, "title": title,
                              "platform": platform_of(url)})

    async def end(self, reason: str) -> None:
        if self.ended:
            return
        self.ended = True
        if self.thread_task:
            self.thread_stop = True
            self.thread_task.cancel()
            with contextlib.suppress(BaseException):
                await self.thread_task
        if self.session:
            state = await self.session.storage_state()
            await self.session.close()
            if state is not None:
                try:
                    await asyncio.to_thread(_save_profile, self.account_id, state)
                except Exception:  # noqa: BLE001 - never fail closing on a profile write
                    log.warning("scanner_browser_profile_save_failed")
        with contextlib.suppress(Exception):
            await self.send_json({"type": "closed", "reason": reason})
        with contextlib.suppress(Exception):
            await self.ws.close(code=1000)


def _still_signed_in(account_id: uuid.UUID, family_id: uuid.UUID) -> bool:
    try:
        with session_scope() as db:
            check_session(db, account_id, family_id)
    except AppError:
        return False
    return True


async def _watch(conn: Connection, idle: int, every: float = 30) -> None:
    while not conn.ended:
        await asyncio.sleep(min(every, idle))
        if conn.session and time.monotonic() - conn.session.last_activity > idle:
            await conn.end("idle")
        elif not await asyncio.to_thread(_still_signed_in, conn.account_id, conn.family_id):
            await conn.end("session_revoked")


async def _set_link(conn: Connection, item_id: Any, *, navigate: bool) -> None:
    """Attach the next captures to a pasted link of the collection (None: stop) and open it."""
    assert conn.collection_id is not None and conn.session is not None
    if not item_id:
        conn.link_item_id = None
        await conn.send_json({"type": "linked", "item_id": None, "url": None})
        return
    try:
        link_id, url = await asyncio.to_thread(_link, conn.account_id, conn.collection_id,
                                               item_id)
    except (AppError, ValueError) as e:
        raise BrowserError("link_not_found", "Link not found in this collection") from e
    conn.link_item_id = link_id
    await conn.send_json({"type": "linked", "item_id": str(link_id), "url": url})
    if navigate:
        await conn.session.navigate(url)


async def _thread(conn: Connection) -> None:
    """Background run of "Capturer tout le fil": the agent keeps seeing the page scroll and can
    press "Arrêter"; each capture is stored like a single one (same link, same checks)."""
    assert conn.session is not None and conn.collection_id is not None
    cfg = get_settings()
    counts = {"added": 0, "duplicate": 0, "rejected": 0}
    reason = "end"

    async def on_shot(cap: Any) -> None:
        nonlocal reason
        conn.session.touch()                   # a long thread is not inactivity
        result = await asyncio.to_thread(_store_capture, conn.account_id, conn.collection_id,
                                         cap.png, cap.url, conn.link_item_id)
        counts[result["status"] if result["status"] in counts else "rejected"] += 1
        await conn.send_json({"type": "thread_progress", "n": sum(counts.values()),
                              "result": result})
        if result["code"] == "collection_full":
            conn.thread_stop, reason = True, "full"

    try:
        why = await conn.session.capture_thread(
            on_shot, stop=lambda: conn.thread_stop, max_shots=cfg.scanner_thread_max_captures,
            settle_ms=cfg.scanner_thread_settle_ms)
        reason = reason if reason == "full" else why
    except asyncio.CancelledError:
        raise
    except AppError as e:                                  # rate limit, collection closed…
        reason = e.code
    except Exception:  # noqa: BLE001
        log.warning("scanner_thread_capture_failed")
        reason = "error"
    finally:
        conn.thread_task = None
    if counts["added"]:
        state = await conn.session.storage_state()
        if state is not None:
            asyncio.ensure_future(asyncio.to_thread(_save_profile, conn.account_id, state))
    with contextlib.suppress(Exception):
        await conn.send_json({"type": "thread_done", "added": counts["added"],
                              "duplicates": counts["duplicate"], "rejected": counts["rejected"],
                              "reason": reason, "platform": platform_of(conn.session.page.url)})


async def _handle(conn: Connection, msg: dict) -> None:
    s = conn.session
    assert s is not None
    kind = msg.get("type")
    s.touch()
    if kind == "stop_thread":
        conn.thread_stop = True
        return
    if conn.thread_task is not None and kind != "ping":
        raise BrowserError("thread_running", "Wait for the thread capture to finish or stop it")
    if kind == "capture_thread":
        conn.thread_stop = False
        conn.thread_task = asyncio.create_task(_thread(conn))
        return
    if kind == "navigate":
        await s.navigate(str(msg.get("url", "")))
    elif kind == "click":
        await s.click(msg.get("x", 0), msg.get("y", 0))
    elif kind == "scroll":
        await s.scroll(msg.get("dx", 0), msg.get("dy", 0), msg.get("x"), msg.get("y"))
    elif kind == "type":
        await s.type(str(msg.get("text", "")))
    elif kind == "key":
        await s.key(str(msg.get("key", "")))
    elif kind in ("back", "forward", "reload"):
        await s.history(kind)
    elif kind == "capture":
        cap = await s.capture(full_page=bool(msg.get("full_page")))
        assert conn.collection_id is not None
        result = await asyncio.to_thread(_store_capture, conn.account_id, conn.collection_id,
                                         cap.png, cap.url, conn.link_item_id)
        await conn.send_json({"type": "captured", "result": result, "url": cap.url,
                              "platform": platform_of(cap.url)})
        state = await s.storage_state()
        if state is not None:
            asyncio.ensure_future(asyncio.to_thread(_save_profile, conn.account_id, state))
    elif kind == "link":
        await _set_link(conn, msg.get("item_id"), navigate=True)
    elif kind == "ping":
        await conn.send_json({"type": "pong"})
    else:
        raise BrowserError("bad_message", "Unknown message")


@app.websocket(PATH)
async def browser_ws(ws: WebSocket) -> None:
    if (ws.headers.get("origin") or "").rstrip("/") not in _allowed_origins():
        await ws.close(code=4403)
        return
    token = ws.cookies.get(ACCESS_COOKIE) or ""
    try:
        account_id, family_id = await asyncio.to_thread(_authenticate, token)
    except AppError:
        await ws.close(code=4401)
        return
    await ws.accept()
    assert service is not None
    previous = service.live.get(account_id)
    if previous:
        await previous.end("replaced")      # one live browser per account (e.g. a second tab)
    conn = Connection(ws, account_id, family_id)
    service.live[account_id] = conn
    watcher: asyncio.Task | None = None
    try:
        first = await asyncio.wait_for(ws.receive_json(), timeout=30)
        if first.get("type") != "start":
            raise BrowserError("bad_message", "Expected start")
        try:
            conn.collection_id, state = await asyncio.to_thread(
                _open_collection, account_id, first.get("session_id"))
        except (AppError, ValueError) as e:
            raise BrowserError(getattr(e, "code", "session_not_found"),
                               "Collection not available") from e
        vp = Viewport.clamp(first.get("width"), first.get("height"), first.get("mobile"))
        conn.session = await service.browser.open(viewport=vp, storage_state=state,
                                                  on_frame=conn.send_frame, on_url=conn.on_url)
        await conn.send_json({"type": "ready", "width": vp.width, "height": vp.height,
                              "mobile": vp.mobile, "collection_id": str(conn.collection_id),
                              "signed_in_before": state is not None})
        idle = get_settings().scanner_browser_idle_seconds
        watcher = asyncio.create_task(_watch(conn, idle))
        if first.get("link_item_id"):
            with contextlib.suppress(BrowserError):
                await _set_link(conn, first["link_item_id"], navigate=True)
        elif first.get("url"):
            with contextlib.suppress(BrowserError):
                await conn.session.navigate(str(first["url"]))
        while not conn.ended:
            msg = await ws.receive_json()
            try:
                await _handle(conn, msg if isinstance(msg, dict) else {})
            except BrowserError as e:
                await conn.send_json({"type": "error", "code": e.code})
            except AppError as e:      # e.g. collection closed meanwhile, rate limit
                await conn.send_json({"type": "error", "code": e.code})
    except BrowserError as e:
        with contextlib.suppress(Exception):
            await conn.send_json({"type": "error", "code": e.code})
    except (TimeoutError, WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if watcher:
            watcher.cancel()
        await conn.end("closed")
        if service.live.get(account_id) is conn:
            service.live.pop(account_id, None)
