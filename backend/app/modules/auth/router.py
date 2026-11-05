from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    AuthContext,
    client_ip,
    get_auth,
    load_membership,
)
from app.core.config import get_settings
from app.core.errors import Unauthorized
from app.core.rate_limit import get_limiter
from app.core.security import new_csrf_token
from app.db.models import Membership, Role, Tenant, User
from app.db.session import get_db, set_context
from app.modules.audit.service import record
from app.modules.auth import service
from app.modules.auth.schemas import (
    LoginIn,
    MeOut,
    RefreshIn,
    RegisterIn,
    SwitchTenantIn,
    TenantSummary,
    TokenOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])
REFRESH_PATH = "/api/v1/auth"


def _token_mode(request: Request) -> bool:
    """API clients opt in to receiving tokens in the body. Browsers get httpOnly cookies only."""
    return request.headers.get("x-auth-mode", "").lower() == "token"


def _deliver(request: Request, response: Response, t: service.IssuedTokens) -> TokenOut:
    if _token_mode(request):
        return TokenOut(access_token=t.access_token, refresh_token=t.refresh_token,
                        expires_in=t.access_ttl, tenant_id=t.tenant_id)
    s = get_settings()
    common = {"secure": s.cookie_secure, "samesite": "lax", "domain": s.cookie_domain}
    response.set_cookie(ACCESS_COOKIE, t.access_token, max_age=t.access_ttl, httponly=True,
                        path="/", **common)
    response.set_cookie(REFRESH_COOKIE, t.refresh_token, max_age=t.refresh_ttl, httponly=True,
                        path=REFRESH_PATH, **common)
    # Readable by JS on purpose: the frontend echoes it in X-CSRF-Token (double submit).
    response.set_cookie(CSRF_COOKIE, new_csrf_token(), max_age=t.refresh_ttl, httponly=False,
                        path="/", **common)
    return TokenOut(expires_in=t.access_ttl, tenant_id=t.tenant_id)


def _clear(response: Response) -> None:
    s = get_settings()
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, REFRESH_PATH), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path, domain=s.cookie_domain)


def _ua(request: Request) -> str | None:
    return request.headers.get("user-agent")


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterIn, request: Request, response: Response,
             db: Session = Depends(get_db)) -> TokenOut:
    s = get_settings()
    get_limiter().hit(f"register:{client_ip(request)}", s.register_rate_limit_per_hour, 3600)
    user_id, tenant_id = service.register(
        db, email=body.email, password=body.password, full_name=body.full_name,
        org_name=body.organization_name, locale=body.locale,
    )
    record(db, tenant_id=tenant_id, actor_user_id=user_id, action="tenant.created",
           target_type="tenant", target_id=tenant_id, request=request)
    tokens = service.issue_tokens(db, user_id=user_id, tenant_id=tenant_id,
                                  user_agent=_ua(request), ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)) -> TokenOut:
    s = get_settings()
    lim = get_limiter()
    lim.hit(f"login:ip:{client_ip(request)}", s.login_rate_limit_per_minute * 3, 60)
    lim.hit(f"login:email:{body.email.lower()}", s.login_rate_limit_per_minute, 60)
    user_id, tenant_id = service.authenticate(db, email=body.email, password=body.password,
                                              tenant_id=body.tenant_id)
    record(db, tenant_id=tenant_id, actor_user_id=user_id, action="auth.login", request=request)
    tokens = service.issue_tokens(db, user_id=user_id, tenant_id=tenant_id,
                                  user_agent=_ua(request), ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, body: RefreshIn | None = None,
            db: Session = Depends(get_db)) -> TokenOut:
    raw = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise Unauthorized("Refresh token required")
    tokens = service.rotate_refresh(db, raw, user_agent=_ua(request), ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, body: RefreshIn | None = None,
           db: Session = Depends(get_db)) -> Response:
    raw = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if raw:
        service.revoke_refresh(db, raw)
        db.commit()
    response.status_code = 204
    _clear(response)
    return response


@router.get("/me", response_model=MeOut)
def me(ctx: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> MeOut:
    user = db.get(User, ctx.user_id)
    assert user is not None
    rows = db.execute(
        select(Tenant, Role.key)
        .join(Membership, Membership.tenant_id == Tenant.id)
        .join(Role, Role.id == Membership.role_id)
        .where(Membership.user_id == ctx.user_id, Membership.status == "active",
               Tenant.status == "active")
        .order_by(Tenant.name)
    ).all()
    tenants = [TenantSummary(id=t.id, name=t.name, slug=t.slug, role=r) for t, r in rows]
    current = next(t for t in tenants if t.id == ctx.tenant_id)
    return MeOut(user_id=user.id, email=user.email, full_name=user.full_name, locale=user.locale,
                 tenant=current, permissions=sorted(ctx.permissions), tenants=tenants)


@router.post("/switch-tenant", response_model=TokenOut)
def switch_tenant(body: SwitchTenantIn, request: Request, response: Response,
                  ctx: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> TokenOut:
    set_context(db, tenant_id=body.tenant_id, user_id=ctx.user_id)
    load_membership(db, ctx.user_id, body.tenant_id)  # 401 if not an active member
    old = request.cookies.get(REFRESH_COOKIE)
    if old:
        service.revoke_refresh(db, old)
        set_context(db, tenant_id=body.tenant_id, user_id=ctx.user_id)
    record(db, tenant_id=body.tenant_id, actor_user_id=ctx.user_id, action="auth.switch_tenant",
           request=request)
    tokens = service.issue_tokens(db, user_id=ctx.user_id, tenant_id=body.tenant_id,
                                  user_agent=_ua(request), ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)
