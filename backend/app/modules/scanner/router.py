"""Aynpulse Scanner HTTP API: /api/v1/scanner/*.

Scanner accounts are separate from AnyPulse users. Browsers get httpOnly `sc_*` cookies (never
the AnyPulse `ri_*` ones); API clients send `X-Auth-Mode: token` and use Bearer tokens.
"""

import dataclasses
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import client_ip
from app.core.config import get_settings
from app.core.errors import Conflict, Unauthorized
from app.core.rate_limit import get_limiter
from app.core.security import new_csrf_token
from app.db.models import ScannerAccount, ScannerCollectionSession, ScannerLink
from app.db.session import get_db
from app.modules.scanner.schemas import (
    AnyPulseAccessOut,
    BrandCreateIn,
    BrandOut,
    CollectionSessionOut,
    CollectionSessionPage,
    CollectionSessionPatch,
    CountersOut,
    DestinationOut,
    InvitationOut,
    ScannerLoginIn,
    ScannerMeOut,
    ScannerRefreshIn,
    ScannerRegisterIn,
    ScannerTokenOut,
)
from app.scanner import anypulse, sessions
from app.scanner import auth as service
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
    # The account's own AnyPulse organization is created and linked automatically. If AnyPulse
    # is unreachable now, it is created the next time the account looks at its AnyPulse access.
    anypulse.try_provision(account_id)
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
def _out(s: ScannerCollectionSession, c: sessions.Counters,
         link: ScannerLink | None) -> CollectionSessionOut:
    dest = None
    if s.link_id is not None and link is not None:
        dest = DestinationOut(organization_name=link.tenant_name,
                              access_active=link.status == "active",
                              brand_id=s.anypulse_brand_id, brand_name=s.anypulse_brand_name)
    return CollectionSessionOut(
        id=s.id, status=s.status, title=s.title, created_at=s.created_at,
        updated_at=s.updated_at, transmitted_at=s.transmitted_at,
        anypulse_collection_id=s.anypulse_collection_id,
        is_open=s.status in sessions.OPEN_STATUSES,
        counters=CountersOut(**dataclasses.asdict(c)), destination=dest)


def _one(db: Session, s: ScannerCollectionSession) -> CollectionSessionOut:
    return _out(s, sessions.counters(db, [s.id])[s.id], anypulse.own_link(db))


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
    link = anypulse.own_link(db)
    return CollectionSessionPage(items=[_out(r, c[r.id], link) for r in rows],
                                 total=total, limit=limit, offset=offset)


@router.get("/sessions/{session_id}", response_model=CollectionSessionOut)
def get_session(session_id: uuid.UUID, ctx: ScannerContext = Depends(get_scanner_auth),
                db: Session = Depends(get_db)) -> CollectionSessionOut:
    return _one(db, sessions.get(db, session_id))


@router.patch("/sessions/{session_id}", response_model=CollectionSessionOut)
def update_session(session_id: uuid.UUID, body: CollectionSessionPatch, request: Request,
                   ctx: ScannerContext = Depends(get_scanner_auth),
                   db: Session = Depends(get_db)) -> CollectionSessionOut:
    s = sessions.get(db, session_id)
    if body.title is not None:
        sessions.rename(db, s, body.title)
    if "brand_id" in body.model_fields_set:
        try:
            sessions.set_destination(db, s, account_id=ctx.account_id, brand_id=body.brand_id)
        except DBAPIError as e:   # the DB trigger disagreed (e.g. brand archived meanwhile)
            if getattr(e.orig, "sqlstate", None) != "23514":
                raise
            db.rollback()
            raise Conflict("Invalid destination for this collection",
                           code="invalid_destination") from e
        record(db, account_id=ctx.account_id, action="session.destination_set",
               target_type="collection_session", target_id=s.id,
               meta={"brand_id": str(s.anypulse_brand_id) if s.anypulse_brand_id else None},
               request=request)
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


# ---- AnyPulse access (automatic, one organization per account) -----------------------------
@router.get("/anypulse", response_model=AnyPulseAccessOut)
def anypulse_access(ctx: ScannerContext = Depends(get_scanner_auth),
                    db: Session = Depends(get_db)) -> AnyPulseAccessOut:
    link = anypulse.ensure_link(db, ctx.account_id)
    url = get_settings().app_base_url
    if link is None:
        return AnyPulseAccessOut(state="pending", organization_name=None, anypulse_url=url,
                                 linked_at=None, revoked_at=None, revoke_reason=None)
    return AnyPulseAccessOut(
        state=link.status, organization_name=link.tenant_name, anypulse_url=url,
        linked_at=link.created_at, revoked_at=link.revoked_at,
        revoke_reason=(link.revoke_reason or None) if link.status == "revoked" else None)


@router.post("/anypulse/invitation", response_model=InvitationOut)
def resend_invitation(ctx: ScannerContext = Depends(get_scanner_auth),
                      db: Session = Depends(get_db)) -> InvitationOut:
    """E-mail again the invitation to open the account's organization in AnyPulse."""
    return InvitationOut(**anypulse.resend_invitation(anypulse.active_link(db, ctx.account_id)))


@router.get("/brands", response_model=list[BrandOut])
def list_brands(ctx: ScannerContext = Depends(get_scanner_auth),
                db: Session = Depends(get_db)) -> list[BrandOut]:
    return [BrandOut(**b) for b in anypulse.brands(anypulse.active_link(db, ctx.account_id))]


@router.post("/brands", response_model=BrandOut, status_code=201)
def create_brand(body: BrandCreateIn, request: Request,
                 ctx: ScannerContext = Depends(get_scanner_auth),
                 db: Session = Depends(get_db)) -> BrandOut:
    b = anypulse.create_brand(anypulse.active_link(db, ctx.account_id), body.name)
    record(db, account_id=ctx.account_id, action="brand.created", target_type="brand",
           target_id=uuid.UUID(b["id"]), meta={"name": b["name"]}, request=request)
    db.commit()
    return BrandOut(**b)


@router.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "module": "scanner"}
