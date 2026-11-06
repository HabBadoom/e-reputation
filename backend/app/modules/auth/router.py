import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select, text, update
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
from app.core.errors import Conflict, NotFound, Unauthorized
from app.core.rate_limit import get_limiter
from app.core.security import hash_token, new_csrf_token
from app.db.models import Membership, RefreshToken, Role, Tenant, User
from app.db.session import get_db, set_context
from app.modules.audit.service import record
from app.modules.auth import account, service
from app.modules.auth.schemas import (
    ChangePasswordIn,
    EmailIn,
    LoginIn,
    MeOut,
    MfaChallengeOut,
    MfaCodeIn,
    MfaDisableIn,
    MfaLoginIn,
    MfaSetupOut,
    MfaStatusOut,
    RecoveryCodesOut,
    RefreshIn,
    RegisterIn,
    ResetPasswordIn,
    SessionOut,
    SwitchTenantIn,
    TenantSummary,
    TokenIn,
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
        invitation_token=body.invitation_token,
    )
    if body.invitation_token:
        record(db, tenant_id=tenant_id, actor_user_id=user_id, action="invitation.accepted",
               target_type="user", target_id=user_id, meta={"via": "signup"}, request=request)
    else:
        record(db, tenant_id=tenant_id, actor_user_id=user_id, action="tenant.created",
               target_type="tenant", target_id=tenant_id, request=request)
    tokens = service.issue_tokens(db, user_id=user_id, tenant_id=tenant_id,
                                  user_agent=_ua(request), ip=client_ip(request))
    user = db.get(User, user_id)
    assert user is not None
    account.send_verification(db, user)  # queued in the same transaction as the signup
    db.commit()
    return _deliver(request, response, tokens)


@router.post("/login", response_model=TokenOut | MfaChallengeOut)
def login(body: LoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)) -> TokenOut | MfaChallengeOut:
    s = get_settings()
    lim = get_limiter()
    lim.hit(f"login:ip:{client_ip(request)}", s.login_rate_limit_per_minute * 3, 60)
    lim.hit(f"login:email:{body.email.lower()}", s.login_rate_limit_per_minute, 60)
    user_id, tenant_id = service.authenticate(db, email=body.email, password=body.password,
                                              tenant_id=body.tenant_id)
    if account.mfa_enabled(db, user_id):
        raw = account.issue_user_token(db, user_id=user_id, purpose="mfa_login",
                                       tenant_id=tenant_id,
                                       ttl_seconds=s.mfa_challenge_ttl_seconds)
        db.commit()
        return MfaChallengeOut(mfa_token=raw, expires_in=s.mfa_challenge_ttl_seconds)
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
                 email_verified=user.email_verified_at is not None,
                 is_platform_admin=user.is_platform_admin,
                 mfa_enabled=account.mfa_enabled(db, user.id),
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


# ---------------------------------------------------------------- MFA login step
@router.post("/login/mfa", response_model=TokenOut)
def login_mfa(body: MfaLoginIn, request: Request, response: Response,
              db: Session = Depends(get_db)) -> TokenOut:
    # At most 5 guesses per challenge (and per IP) — a 6-digit code can't be brute-forced.
    get_limiter().hit(f"mfa:tok:{hash_token(body.mfa_token)}", 5, 300)
    get_limiter().hit(f"mfa:ip:{client_ip(request)}", 30, 300)
    tok = account.find_valid_token(db, body.mfa_token, "mfa_login")
    method = account.mfa_check(db, tok.user_id, body.code)
    if method is None:
        db.commit()
        raise Unauthorized("Invalid code", code="invalid_mfa_code")
    account.mark_used(db, tok.id)
    assert tok.tenant_id is not None
    set_context(db, tenant_id=tok.tenant_id, user_id=tok.user_id)
    load_membership(db, tok.user_id, tok.tenant_id)  # access may have been revoked meanwhile
    record(db, tenant_id=tok.tenant_id, actor_user_id=tok.user_id, action="auth.login",
           meta={"mfa": method}, request=request)
    tokens = service.issue_tokens(db, user_id=tok.user_id, tenant_id=tok.tenant_id,
                                  user_agent=_ua(request), ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)


# ---------------------------------------------------------------- email verification
@router.post("/email/verify")
def verify_email(body: TokenIn, db: Session = Depends(get_db)) -> dict:
    account.verify_email(db, body.token)
    db.commit()
    return {"verified": True}


@router.post("/email/resend", status_code=202)
def resend_verification(ctx: AuthContext = Depends(get_auth),
                        db: Session = Depends(get_db)) -> dict:
    user = db.get(User, ctx.user_id)
    assert user is not None
    if user.email_verified_at is not None:
        raise Conflict("Email address already verified", code="already_verified")
    get_limiter().hit(f"email:verify:{user.id}", get_settings().email_rate_limit_per_hour, 3600)
    account.send_verification(db, user)
    db.commit()
    return {"queued": True}


# ---------------------------------------------------------------- passwords
@router.post("/password/forgot", status_code=202)
def forgot_password(body: EmailIn, request: Request, db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    get_limiter().hit(f"reset:ip:{client_ip(request)}", s.email_rate_limit_per_hour * 4, 3600)
    get_limiter().hit(f"reset:email:{body.email.lower()}", s.email_rate_limit_per_hour, 3600)
    account.request_password_reset(db, body.email)
    db.commit()
    # Same answer whether or not the account exists.
    return {"accepted": True}


@router.post("/password/reset", status_code=204)
def reset_password(body: ResetPasswordIn, request: Request,
                   db: Session = Depends(get_db)) -> Response:
    get_limiter().hit(f"reset:apply:{client_ip(request)}", 20, 3600)
    account.reset_password(db, body.token, body.password, request)
    db.commit()
    return Response(status_code=204)


@router.post("/password/change", response_model=TokenOut)
def change_password(body: ChangePasswordIn, request: Request, response: Response,
                    ctx: AuthContext = Depends(get_auth),
                    db: Session = Depends(get_db)) -> TokenOut:
    get_limiter().hit(f"pwchange:{ctx.user_id}", 10, 3600)
    account.change_password(db, ctx.user_id, body.current_password, body.new_password)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="auth.password_changed", target_type="user", target_id=ctx.user_id,
           request=request)
    # Every other session was revoked; give this one a fresh pair.
    tokens = service.issue_tokens(db, user_id=ctx.user_id, tenant_id=ctx.tenant_id,
                                  user_agent=_ua(request), ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)


# ---------------------------------------------------------------- MFA management
@router.get("/mfa", response_model=MfaStatusOut)
def mfa_status(ctx: AuthContext = Depends(get_auth),
               db: Session = Depends(get_db)) -> MfaStatusOut:
    return MfaStatusOut(enabled=account.mfa_enabled(db, ctx.user_id),
                        recovery_codes_remaining=account.recovery_codes_remaining(db, ctx.user_id))


@router.post("/mfa/setup", response_model=MfaSetupOut)
def mfa_setup(ctx: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> JSONResponse:
    user = db.get(User, ctx.user_id)
    assert user is not None
    secret, uri = account.mfa_setup(db, user)
    db.commit()
    return JSONResponse(MfaSetupOut(secret=secret, otpauth_uri=uri).model_dump(),
                        headers={"cache-control": "no-store"})


@router.post("/mfa/enable", response_model=RecoveryCodesOut)
def mfa_enable(body: MfaCodeIn, request: Request, ctx: AuthContext = Depends(get_auth),
               db: Session = Depends(get_db)) -> RecoveryCodesOut:
    get_limiter().hit(f"mfa:enable:{ctx.user_id}", 10, 300)
    codes = account.mfa_enable(db, ctx.user_id, body.code)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="auth.mfa_enabled",
           target_type="user", target_id=ctx.user_id, request=request)
    db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/mfa/disable", status_code=204)
def mfa_disable(body: MfaDisableIn, request: Request, ctx: AuthContext = Depends(get_auth),
                db: Session = Depends(get_db)) -> Response:
    get_limiter().hit(f"mfa:disable:{ctx.user_id}", 5, 300)
    account.mfa_disable(db, ctx.user_id, body.password, body.code)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="auth.mfa_disabled",
           target_type="user", target_id=ctx.user_id, request=request)
    db.commit()
    return Response(status_code=204)


@router.post("/mfa/recovery-codes", response_model=RecoveryCodesOut)
def mfa_recovery_codes(body: MfaCodeIn, request: Request, ctx: AuthContext = Depends(get_auth),
                       db: Session = Depends(get_db)) -> RecoveryCodesOut:
    get_limiter().hit(f"mfa:codes:{ctx.user_id}", 5, 300)
    codes = account.mfa_regenerate_codes(db, ctx.user_id, body.code)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="auth.mfa_recovery_regenerated", target_type="user", target_id=ctx.user_id,
           request=request)
    db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


# ---------------------------------------------------------------- sessions
_SESSIONS_SQL = text("""
    SELECT family_id AS id,
           min(created_at) AS started_at,
           max(created_at) AS last_active_at,
           (array_agg(tenant_id ORDER BY created_at DESC))[1] AS tenant_id,
           (array_agg(user_agent ORDER BY created_at DESC))[1] AS user_agent,
           (array_agg(host(ip) ORDER BY created_at DESC))[1] AS ip
    FROM refresh_tokens
    WHERE user_id = :u
    GROUP BY family_id
    HAVING bool_or(revoked_at IS NULL AND expires_at > now())
    ORDER BY max(created_at) DESC
""")


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(ctx: AuthContext = Depends(get_auth),
                  db: Session = Depends(get_db)) -> list[SessionOut]:
    """Active sign-ins of the current user across devices (one per refresh-token family)."""
    return [SessionOut(current=r.id == ctx.session_id, **r._mapping)
            for r in db.execute(_SESSIONS_SQL, {"u": ctx.user_id})]


def _revoke_families(db: Session, user_id: uuid.UUID, family_ids: list[uuid.UUID]) -> int:
    return db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.family_id.in_(family_ids),
               RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    ).rowcount


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(session_id: uuid.UUID, request: Request,
                   ctx: AuthContext = Depends(get_auth),
                   db: Session = Depends(get_db)) -> Response:
    if _revoke_families(db, ctx.user_id, [session_id]) == 0:
        raise NotFound("Session not found")
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="auth.session_revoked",
           meta={"session_id": str(session_id)}, request=request)
    db.commit()
    return Response(status_code=204)


@router.delete("/sessions", status_code=204)
def revoke_other_sessions(request: Request, ctx: AuthContext = Depends(get_auth),
                          db: Session = Depends(get_db)) -> Response:
    """Sign out everywhere except here."""
    others = [r.id for r in db.execute(_SESSIONS_SQL, {"u": ctx.user_id})
              if r.id != ctx.session_id]
    if others:
        _revoke_families(db, ctx.user_id, others)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="auth.other_sessions_revoked", meta={"count": len(others)}, request=request)
    db.commit()
    return Response(status_code=204)
