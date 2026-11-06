"""Authentication flows. All DB access goes through RLS; pre-context lookups use the narrow
SECURITY DEFINER functions defined in the migration."""

import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import load_membership
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, Unauthorized
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_refresh_token,
    verify_password,
)
from app.db.models import Invitation, Membership, RefreshToken, Role, Tenant, User
from app.db.session import set_context
from app.modules.billing.entitlements import create_subscription

log = logging.getLogger("repintel.auth")


@dataclass
class IssuedTokens:
    access_token: str
    access_ttl: int
    refresh_token: str
    refresh_ttl: int
    user_id: uuid.UUID
    tenant_id: uuid.UUID


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    if len(base) < 2:
        base = f"org-{base}"
    return f"{base}-{secrets.token_hex(3)}"


def issue_tokens(
    db: Session,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> IssuedTokens:
    s = get_settings()
    family_id = family_id or uuid.uuid4()
    access, ttl = create_access_token(user_id, tenant_id, family_id)
    raw, hashed = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user_id,
            tenant_id=tenant_id,
            token_hash=hashed,
            family_id=family_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=s.refresh_token_ttl_seconds),
            user_agent=(user_agent or "")[:400] or None,
            ip=ip,
        )
    )
    return IssuedTokens(access, ttl, raw, s.refresh_token_ttl_seconds, user_id, tenant_id)


def _valid_invitation(db: Session, raw: str, email: str):  # noqa: ANN202
    inv = db.execute(
        text("SELECT id, tenant_id, email, role_id, expires_at, accepted_at, revoked_at "
             "FROM auth_find_invitation(:h)"), {"h": hash_token(raw)}
    ).one_or_none()
    if (inv is None or inv.accepted_at or inv.revoked_at
            or inv.expires_at <= datetime.now(UTC) or inv.email != email.lower()):
        raise NotFound("Invitation not found or no longer valid", code="invalid_invitation")
    return inv


def register(
    db: Session, *, email: str, password: str, full_name: str, org_name: str | None,
    locale: str, invitation_token: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a user and either a new organization (as owner) or, with an invitation token
    addressed to this email, a membership in the inviting organization (no new org).
    The email stays unverified either way: the token was also visible to the inviter."""
    inv = _valid_invitation(db, invitation_token, email) if invitation_token else None
    user_id = uuid.uuid4()
    tenant_id = inv.tenant_id if inv else uuid.uuid4()
    set_context(db, tenant_id=tenant_id, user_id=user_id)
    owner_role = db.scalars(select(Role).where(Role.key == "owner")).one()
    try:
        db.add(User(id=user_id, email=email.lower(), password_hash=hash_password(password),
                    full_name=full_name, locale=locale))
        db.flush()
    except IntegrityError as e:
        db.rollback()
        # Note: this reveals that the email is registered. Accepted trade-off for a clear UX;
        # registration is rate-limited. Revisit with email verification in Milestone 1.1.
        raise Conflict("An account with this email already exists", code="email_taken") from e
    if inv is not None:
        db.add(Membership(tenant_id=tenant_id, user_id=user_id, role_id=inv.role_id))
        db.execute(update(Invitation).where(Invitation.id == inv.id)
                   .values(accepted_at=datetime.now(UTC)))
        db.flush()
        return user_id, tenant_id
    assert org_name
    db.add(Tenant(id=tenant_id, name=org_name, slug=_slugify(org_name), default_locale=locale))
    db.flush()
    db.add(Membership(tenant_id=tenant_id, user_id=user_id, role_id=owner_role.id))
    db.flush()
    create_subscription(db, tenant_id)
    return user_id, tenant_id


def authenticate(db: Session, *, email: str, password: str, tenant_id: uuid.UUID | None
                 ) -> tuple[uuid.UUID, uuid.UUID]:
    row = db.execute(
        text("SELECT id, password_hash, status FROM auth_find_user_by_email(:e)"),
        {"e": email.lower()},
    ).one_or_none()
    ok = verify_password(password, row.password_hash if row else None)
    if not row or not ok or row.status != "active":
        log.info("login_failed", extra={"reason": "bad_credentials_or_disabled"})
        raise Unauthorized("Invalid email or password", code="invalid_credentials")

    user_id: uuid.UUID = row.id
    set_context(db, tenant_id=None, user_id=user_id)
    q = (
        select(Membership.tenant_id)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(Membership.user_id == user_id, Membership.status == "active",
               Tenant.status == "active")
        .order_by(Membership.created_at)
    )
    tenant_ids = list(db.scalars(q))
    if not tenant_ids:
        raise Unauthorized("This account has no active organization", code="no_membership")
    chosen = tenant_id if tenant_id in tenant_ids else tenant_ids[0]
    if tenant_id and tenant_id not in tenant_ids:
        raise Unauthorized("Not a member of the requested organization", code="not_member")

    set_context(db, tenant_id=chosen, user_id=user_id)
    db.execute(update(User).where(User.id == user_id).values(last_login_at=datetime.now(UTC)))
    return user_id, chosen


def rotate_refresh(db: Session, raw: str, *, user_agent: str | None, ip: str | None
                   ) -> IssuedTokens:
    row = db.execute(
        text("SELECT id, user_id, tenant_id, family_id, expires_at, revoked_at "
             "FROM auth_find_refresh_token(:h)"),
        {"h": hash_token(raw)},
    ).one_or_none()
    if row is None:
        raise Unauthorized("Invalid refresh token", code="invalid_refresh")

    set_context(db, tenant_id=row.tenant_id, user_id=row.user_id)
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        # A rotated-out token was presented again → likely stolen. Kill the whole family.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.commit()
        log.warning("refresh_token_reuse_detected", extra={"family_id": str(row.family_id)})
        raise Unauthorized("Refresh token reuse detected; please sign in again",
                           code="refresh_reuse")
    if row.expires_at <= now:
        raise Unauthorized("Refresh token expired", code="refresh_expired")

    load_membership(db, row.user_id, row.tenant_id)  # raises if access was revoked meanwhile
    db.execute(update(RefreshToken).where(RefreshToken.id == row.id).values(revoked_at=now))
    return issue_tokens(db, user_id=row.user_id, tenant_id=row.tenant_id,
                        family_id=row.family_id, user_agent=user_agent, ip=ip)


def revoke_refresh(db: Session, raw: str) -> None:
    row = db.execute(
        text("SELECT id, user_id, tenant_id, family_id FROM auth_find_refresh_token(:h)"),
        {"h": hash_token(raw)},
    ).one_or_none()
    if row is None:
        return
    set_context(db, tenant_id=row.tenant_id, user_id=row.user_id)
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
