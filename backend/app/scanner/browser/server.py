"""scanner-browser service: the WebSocket endpoint behind Scanner's built-in browser.

Run with `uvicorn app.scanner.browser.server:app --port 8100` (container `scanner-browser`, on
the isolated `browser` network in production). One live browser per Scanner account.

Protocol on /api/v1/scanner/browser/ws:
  client → server (JSON): start {session_id, width, height, mobile}; navigate {url};
    click {x, y}; scroll {dx, dy, x?, y?}; type {text}; key {key}; back; forward; reload;
    capture {full_page?}; ping
  server → client: binary JPEG frames; JSON ready, url {url, title}, captured {result},
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


def _store_capture(account_id: uuid.UUID, session_id: uuid.UUID, png: bytes, url: str
                   ) -> dict:
    cfg = get_settings()
    get_limiter().hit(f"scanner:uploads:{account_id}", cfg.scanner_uploads_per_hour, 3600)
    host = (urlsplit(url).hostname or "page").replace(".", "-")[:60]
    name = f"capture-{host}-{datetime.now(UTC):%Y%m%d-%H%M%S}.png"
    with session_scope() as db:
        set_scanner_context(db, account_id=account_id)
        s = sessions.get(db, session_id)
        result = uploads.add_screenshots(db, s, [(name, png)], source_url=url,
                                         platform=platform_of(url))[0]
        if result.status == "added":
            record(db, account_id=account_id, action="screenshots.added",
                   target_type="collection_session", target_id=s.id,
                   meta={"added": 1, "via": "built_in_browser", "platform": platform_of(url)})
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


async def _handle(conn: Connection, msg: dict) -> None:
    s = conn.session
    assert s is not None
    kind = msg.get("type")
    s.touch()
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
                                         cap.png, cap.url)
        await conn.send_json({"type": "captured", "result": result, "url": cap.url,
                              "platform": platform_of(cap.url)})
        state = await s.storage_state()
        if state is not None:
            asyncio.ensure_future(asyncio.to_thread(_save_profile, conn.account_id, state))
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
        if first.get("url"):
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
