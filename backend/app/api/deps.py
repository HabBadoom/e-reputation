"""Request authentication, tenant context and permission checks.

Order of operations for every authenticated request:
  1. Read the access token (cookie for browsers, Bearer for API clients).
  2. Verify signature/expiry → (user_id, tenant_id) from the *token*, never from client input.
  3. Set the DB context for this session (RLS now scopes every query).
  4. Re-load the active membership + role permissions from the DB (revocation is immediate).
  5. For cookie-authenticated unsafe methods, enforce double-submit CSRF.
"""

import ipaddress
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, Unauthorized
from app.core.logging import tenant_id_var
from app.core.security import constant_time_eq, decode_access_token
from app.db.models import Membership, Permission, Role, RolePermission, User
from app.db.session import get_db, set_context

ACCESS_COOKIE = "ri_access"
REFRESH_COOKIE = "ri_refresh"
CSRF_COOKIE = "ri_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class AuthContext:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    role_key: str
    role_rank: int
    permissions: frozenset[str]
    via_cookie: bool

    def has(self, perm: str) -> bool:
        return perm in self.permissions


def _extract_token(request: Request) -> tuple[str, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    cookie = request.cookies.get(ACCESS_COOKIE)
    if cookie:
        return cookie, True
    raise Unauthorized("Authentication required")


def load_membership(db: Session, user_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[Membership, Role]:
    row = db.execute(
        select(Membership, Role)
        .join(Role, Role.id == Membership.role_id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.user_id == user_id,
            Membership.tenant_id == tenant_id,
            Membership.status == "active",
            User.status == "active",
        )
    ).one_or_none()
    if row is None:
        raise Unauthorized("No active membership for this organization", code="membership_revoked")
    return row[0], row[1]


def permissions_for_role(db: Session, role_id: uuid.UUID) -> frozenset[str]:
    keys = db.scalars(
        select(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    ).all()
    return frozenset(keys)


def get_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    token, via_cookie = _extract_token(request)
    claims = decode_access_token(token)

    if via_cookie and request.method not in SAFE_METHODS:
        if not constant_time_eq(request.headers.get(CSRF_HEADER), request.cookies.get(CSRF_COOKIE)):
            raise Forbidden("CSRF token missing or invalid", code="csrf_failed")

    set_context(db, tenant_id=claims.tenant_id, user_id=claims.user_id)
    membership, role = load_membership(db, claims.user_id, claims.tenant_id)
    tenant_id_var.set(str(claims.tenant_id))
    request.state.user_id = claims.user_id
    return AuthContext(
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        membership_id=membership.id,
        role_key=role.key,
        role_rank=role.rank,
        permissions=permissions_for_role(db, role.id),
        via_cookie=via_cookie,
    )


def require(*perms: str) -> Callable[..., AuthContext]:
    """Dependency factory: `ctx: AuthContext = Depends(require("brands:write"))`."""

    def _dep(ctx: AuthContext = Depends(get_auth)) -> AuthContext:
        missing = [p for p in perms if not ctx.has(p)]
        if missing:
            raise Forbidden(f"Missing permission: {', '.join(missing)}")
        return ctx

    return _dep


def client_ip(request: Request) -> str | None:
    """Client IP if it is a valid address, else None (safe to store in an INET column).

    Behind a proxy, configure uvicorn --proxy-headers --forwarded-allow-ips so this is trustworthy.
    """
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host
