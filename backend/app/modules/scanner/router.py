"""Aynpulse Scanner HTTP API: /api/v1/scanner/*.

Scanner accounts are separate from AnyPulse users. Browsers get httpOnly `sc_*` cookies (never
the AnyPulse `ri_*` ones); API clients send `X-Auth-Mode: token` and use Bearer tokens.
"""

import dataclasses
import uuid

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import client_ip
from app.core.config import get_settings
from app.core.errors import AppError, Unauthorized
from app.core.rate_limit import get_limiter
from app.core.security import new_csrf_token
from app.db.models import ScannerAccount, ScannerBrowserProfile, ScannerCollectionSession
from app.db.session import get_db
from app.modules.scanner.schemas import (
    AnyPulseAccessOut,
    BrowserInfoOut,
    CollectionSessionOut,
    CollectionSessionPage,
    CollectionSessionPatch,
    CountersOut,
    FileResultOut,
    ItemOut,
    ReorderIn,
    ScannerLoginIn,
    ScannerMeOut,
    ScannerRefreshIn,
    ScannerRegisterIn,
    ScannerTokenOut,
    UploadedFileOut,
    UploadOut,
)
from app.scanner import auth as service
from app.scanner import sessions, uploads
from app.scanner.audit import record
from app.scanner.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    ScannerContext,
    get_scanner_auth,
)

router = APIRouter(prefix="/scanner", tags=["scanner"])
REFRESH_PATH = "/api/v1/scanner/auth"


# ---- auth ------------------------------------------------------------------------------------
def _token_mode(request: Request) -> bool:
    return request.headers.get("x-auth-mode", "").lower() == "token"


def _deliver(request: Request, response: Response, t: service.IssuedTokens) -> ScannerTokenOut:
    if _token_mode(request):
        return ScannerTokenOut(access_token=t.access_token, refresh_token=t.refresh_token,
                               expires_in=t.access_ttl)
    s = get_settings()
    common = {"secure": s.cookie_secure, "samesite": "lax", "domain": s.scanner_cookie_domain}
    response.set_cookie(ACCESS_COOKIE, t.access_token, max_age=t.access_ttl, httponly=True,
                        path="/", **common)
    response.set_cookie(REFRESH_COOKIE, t.refresh_token, max_age=t.refresh_ttl, httponly=True,
                        path=REFRESH_PATH, **common)
    response.set_cookie(CSRF_COOKIE, new_csrf_token(), max_age=t.refresh_ttl, httponly=False,
                        path="/", **common)
    return ScannerTokenOut(expires_in=t.access_ttl)


def _clear(response: Response) -> None:
    s = get_settings()
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_PATH), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path, domain=s.scanner_cookie_domain)


def _ua(request: Request) -> str | None:
    return request.headers.get("user-agent")


@router.post("/auth/register", response_model=ScannerTokenOut, status_code=201)
def register(body: ScannerRegisterIn, request: Request, response: Response,
             db: Session = Depends(get_db)) -> ScannerTokenOut:
    s = get_settings()
    get_limiter().hit(f"scanner:register:{client_ip(request)}", s.register_rate_limit_per_hour,
                      3600)
    account_id = service.register(
        db, email=body.email, password=body.password, full_name=body.full_name,
        locale=body.locale)
    record(db, account_id=account_id, action="account.created", target_type="scanner_account",
           target_id=account_id, request=request)
    tokens = service.issue_tokens(db, account_id=account_id, user_agent=_ua(request),
                                  ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)


@router.post("/auth/login", response_model=ScannerTokenOut)
def login(body: ScannerLoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)) -> ScannerTokenOut:
    s = get_settings()
    lim = get_limiter()
    lim.hit(f"scanner:login:ip:{client_ip(request)}", s.login_rate_limit_per_minute * 3, 60)
    lim.hit(f"scanner:login:email:{body.email.lower()}", s.login_rate_limit_per_minute, 60)
    account_id = service.authenticate(db, email=body.email, password=body.password)
    record(db, account_id=account_id, action="auth.login", request=request)
    tokens = service.issue_tokens(db, account_id=account_id, user_agent=_ua(request),
                                  ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)


@router.post("/auth/refresh", response_model=ScannerTokenOut)
def refresh(request: Request, response: Response, body: ScannerRefreshIn | None = None,
            db: Session = Depends(get_db)) -> ScannerTokenOut:
    raw = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise Unauthorized("Refresh token required")
    tokens = service.rotate_refresh(db, raw, user_agent=_ua(request), ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, body: ScannerRefreshIn | None = None,
           db: Session = Depends(get_db)) -> Response:
    raw = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if raw:
        service.revoke_refresh(db, raw)
        db.commit()
    out = Response(status_code=204)
    _clear(out)
    return out


@router.get("/auth/me", response_model=ScannerMeOut)
def me(ctx: ScannerContext = Depends(get_scanner_auth),
       db: Session = Depends(get_db)) -> ScannerMeOut:
    a = db.get(ScannerAccount, ctx.account_id)
    assert a is not None
    return ScannerMeOut(account_id=a.id, email=a.email, full_name=a.full_name,
                        locale=a.locale, created_at=a.created_at)


# ---- collection sessions ---------------------------------------------------------------------
def _out(s: ScannerCollectionSession, c: sessions.Counters) -> CollectionSessionOut:
    return CollectionSessionOut(
        id=s.id, status=s.status, title=s.title, created_at=s.created_at,
        updated_at=s.updated_at, transmitted_at=s.transmitted_at,
        anypulse_collection_id=s.anypulse_collection_id,
        is_open=s.status in sessions.OPEN_STATUSES,
        counters=CountersOut(**dataclasses.asdict(c)))


def _one(db: Session, s: ScannerCollectionSession) -> CollectionSessionOut:
    return _out(s, sessions.counters(db, [s.id])[s.id])


@router.get("/sessions/current", response_model=CollectionSessionOut)
def current_session(request: Request, ctx: ScannerContext = Depends(get_scanner_auth),
                    db: Session = Depends(get_db)) -> CollectionSessionOut:
    """The active collection; created on first use. The add-item screen always comes back
    here, so the session is never lost between additions."""
    s, created = sessions.current(db, ctx.account_id)
    if created:
        record(db, account_id=ctx.account_id, action="session.created",
               target_type="collection_session", target_id=s.id, request=request)
        db.commit()
    return _one(db, s)


@router.get("/sessions", response_model=CollectionSessionPage)
def list_sessions(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                  ctx: ScannerContext = Depends(get_scanner_auth),
                  db: Session = Depends(get_db)) -> CollectionSessionPage:
    rows, total = sessions.list_sessions(db, limit=limit, offset=offset)
    c = sessions.counters(db, [r.id for r in rows])
    return CollectionSessionPage(items=[_out(r, c[r.id]) for r in rows],
                                 total=total, limit=limit, offset=offset)


@router.get("/sessions/{session_id}", response_model=CollectionSessionOut)
def get_session(session_id: uuid.UUID, ctx: ScannerContext = Depends(get_scanner_auth),
                db: Session = Depends(get_db)) -> CollectionSessionOut:
    return _one(db, sessions.get(db, session_id))


@router.patch("/sessions/{session_id}", response_model=CollectionSessionOut)
def rename_session(session_id: uuid.UUID, body: CollectionSessionPatch,
                   ctx: ScannerContext = Depends(get_scanner_auth),
                   db: Session = Depends(get_db)) -> CollectionSessionOut:
    s = sessions.rename(db, sessions.get(db, session_id), body.title)
    db.commit()
    return _one(db, s)


@router.post("/sessions/{session_id}/cancel", response_model=CollectionSessionOut)
def cancel_session(session_id: uuid.UUID, request: Request,
                   ctx: ScannerContext = Depends(get_scanner_auth),
                   db: Session = Depends(get_db)) -> CollectionSessionOut:
    s = sessions.cancel(db, sessions.get(db, session_id))
    record(db, account_id=ctx.account_id, action="session.cancelled",
           target_type="collection_session", target_id=s.id, request=request)
    db.commit()
    return _one(db, s)


# ---- AnyPulse access ------------------------------------------------------------------------
@router.get("/anypulse", response_model=AnyPulseAccessOut)
def anypulse_access(ctx: ScannerContext = Depends(get_scanner_auth),
                    db: Session = Depends(get_db)) -> AnyPulseAccessOut:
    """Whether this account may send its collections to AnyPulse. Granted at sign-up; only a
    platform administrator revokes it. Nothing about AnyPulse's data."""
    a = db.get(ScannerAccount, ctx.account_id)
    assert a is not None
    revoked = a.anypulse_access == "revoked"
    return AnyPulseAccessOut(state=a.anypulse_access, revoked_at=a.access_revoked_at,
                             revoke_reason=(a.access_revoke_reason or None) if revoked else None)


# ---- screenshots (phase 3) -------------------------------------------------------------------
def _items(db: Session, session_id: uuid.UUID) -> list[ItemOut]:
    out = []
    for item, f in uploads.list_items(db, session_id):
        fo = None if f is None else UploadedFileOut(
            id=f.id, original_filename=f.original_filename, mime_type=f.mime_type,
            width=f.width, height=f.height, size_bytes=f.size_bytes,
            url=f"/api/v1/scanner/files/{f.id}/content")
        out.append(ItemOut(id=item.id, source_type=item.source_type, status=item.status,
                           platform=item.platform, content_type=item.content_type,
                           position=item.position, source_url=item.source_url,
                           possible_duplicate_of=item.possible_duplicate_of,
                           created_at=item.created_at, file=fo))
    return out


@router.post("/sessions/{session_id}/screenshots", response_model=UploadOut)
def upload_screenshots(session_id: uuid.UUID, request: Request,
                       files: list[UploadFile] = File(...),
                       ctx: ScannerContext = Depends(get_scanner_auth),
                       db: Session = Depends(get_db)) -> UploadOut:
    """Add screenshots to an open collection (multipart field `files`, PNG/JPEG/WebP).
    Each file is validated and re-encoded without metadata; the answer lists what happened to
    each one, then the collection's items, so the add-item screen can carry on."""
    cfg = get_settings()
    if len(files) > cfg.scanner_upload_max_files_per_request:
        raise AppError("Too many files in one upload", code="too_many_files", status_code=413)
    lim = get_limiter()
    for _ in files:
        lim.hit(f"scanner:uploads:{ctx.account_id}", cfg.scanner_uploads_per_hour, 3600)
    s = sessions.get(db, session_id)
    payload = [(f.filename or "", f.file.read(cfg.scanner_upload_max_bytes + 1)) for f in files]
    results = uploads.add_screenshots(db, s, payload)
    added = sum(r.status == "added" for r in results)
    if added:
        record(db, account_id=ctx.account_id, action="screenshots.added",
               target_type="collection_session", target_id=s.id,
               meta={"added": added, "refused": len(results) - added}, request=request)
    db.commit()
    return UploadOut(results=[FileResultOut(**r.__dict__) for r in results],
                     items=_items(db, s.id), session=_one(db, s))


@router.get("/sessions/{session_id}/items", response_model=list[ItemOut])
def list_items(session_id: uuid.UUID, ctx: ScannerContext = Depends(get_scanner_auth),
               db: Session = Depends(get_db)) -> list[ItemOut]:
    return _items(db, sessions.get(db, session_id).id)


@router.put("/sessions/{session_id}/items/order", response_model=list[ItemOut])
def reorder_items(session_id: uuid.UUID, body: ReorderIn,
                  ctx: ScannerContext = Depends(get_scanner_auth),
                  db: Session = Depends(get_db)) -> list[ItemOut]:
    s = sessions.get(db, session_id)
    uploads.reorder(db, s, body.item_ids)
    db.commit()
    return _items(db, s.id)


@router.delete("/items/{item_id}", response_model=CollectionSessionOut)
def remove_item(item_id: uuid.UUID, request: Request,
                ctx: ScannerContext = Depends(get_scanner_auth),
                db: Session = Depends(get_db)) -> CollectionSessionOut:
    item = uploads.get_item(db, item_id)
    s = sessions.get(db, item.session_id)
    keys = uploads.remove_item(db, item, s)
    record(db, account_id=ctx.account_id, action="item.removed", target_type="collection_item",
           target_id=item_id, request=request)
    db.commit()
    uploads.delete_objects(keys)   # after commit: never delete a file still referenced
    return _one(db, s)


@router.get("/files/{file_id}/content")
def file_content(file_id: uuid.UUID, ctx: ScannerContext = Depends(get_scanner_auth),
                 db: Session = Depends(get_db)) -> Response:
    """The sanitised image, to its owner only. Never cached by shared caches."""
    f, data = uploads.read_file(db, file_id)
    return Response(content=data, media_type=f.mime_type, headers={
        "cache-control": "private, max-age=300",
        "content-disposition": "inline",
        "x-content-type-options": "nosniff",
        "content-security-policy": "default-src 'none'; sandbox",
    })


@router.get("/browser", response_model=BrowserInfoOut)
def browser_info(ctx: ScannerContext = Depends(get_scanner_auth),
                 db: Session = Depends(get_db)) -> BrowserInfoOut:
    cfg = get_settings()
    row = db.get(ScannerBrowserProfile, ctx.account_id)
    return BrowserInfoOut(enabled=cfg.scanner_browser_enabled,
                          ws_url=cfg.scanner_browser_public_url,
                          has_saved_session=row is not None,
                          saved_at=row.updated_at if row else None)


@router.delete("/browser/profile", status_code=204)
def forget_browser_session(request: Request, ctx: ScannerContext = Depends(get_scanner_auth),
                           db: Session = Depends(get_db)) -> Response:
    """"Forget my signed-in sessions": deletes the saved cookies of the built-in browser.
    A browser open at that moment saves again when it closes, so the app closes it first."""
    from app.scanner.browser import profiles
    if profiles.forget(db, ctx.account_id):
        record(db, account_id=ctx.account_id, action="browser.session_forgotten",
               request=request)
    return Response(status_code=204)


@router.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "module": "scanner"}
