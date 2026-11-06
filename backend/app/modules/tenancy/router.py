"""Organization, members, roles and invitations."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, client_ip, get_auth, require
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import hash_token, new_refresh_token
from app.db.models import Invitation, Membership, Role, Tenant, User
from app.db.session import get_db, set_context
from app.modules.audit.service import record
from app.modules.auth import service as auth_service
from app.modules.auth.router import _deliver
from app.modules.auth.schemas import AcceptInvitationIn, TokenOut

router = APIRouter(tags=["organization"])
INVITE_TTL = timedelta(days=7)


# ---------------------------------------------------------------- schemas
class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    slug: str
    status: str
    default_locale: str
    created_at: datetime


class TenantPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    default_locale: Literal["fr", "ar", "en"] | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    name: str
    rank: int


class MemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    status: str
    joined_at: datetime


class MemberPatch(BaseModel):
    role: str = Field(min_length=1, max_length=50)


class InviteIn(BaseModel):
    email: EmailStr
    role: str = Field(default="viewer", max_length=50)


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    created_at: datetime
    token: str | None = Field(
        default=None, description="Returned once at creation. Share it as an invite link."
    )


# ---------------------------------------------------------------- helpers
def _role(db: Session, key: str) -> Role:
    role = db.scalars(select(Role).where(Role.key == key)).one_or_none()
    if role is None:
        raise NotFound(f"Unknown role '{key}'")
    return role


def _assert_can_assign(ctx: AuthContext, role: Role) -> None:
    if role.rank > ctx.role_rank or (role.key == "owner" and ctx.role_key != "owner"):
        raise Forbidden("You cannot grant a role above your own")


def _owner_count(db: Session, tenant_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count()).select_from(Membership).join(Role, Role.id == Membership.role_id)
        .where(Membership.tenant_id == tenant_id, Role.key == "owner",
               Membership.status == "active")
    ) or 0


def _member(db: Session, tenant_id: uuid.UUID, membership_id: uuid.UUID
            ) -> tuple[Membership, Role, User]:
    # Explicit tenant filter: the memberships RLS policy also exposes the caller's own
    # memberships in *other* tenants (for the tenant switcher).
    row = db.execute(
        select(Membership, Role, User).join(Role, Role.id == Membership.role_id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.id == membership_id, Membership.tenant_id == tenant_id)
    ).one_or_none()
    if row is None:
        raise NotFound("Member not found")
    return row[0], row[1], row[2]


# ---------------------------------------------------------------- organization
@router.get("/organization", response_model=TenantOut)
def get_org(ctx: AuthContext = Depends(require("tenant:read")),
            db: Session = Depends(get_db)) -> Tenant:
    t = db.get(Tenant, ctx.tenant_id)
    assert t is not None
    return t


@router.patch("/organization", response_model=TenantOut)
def patch_org(body: TenantPatch, request: Request,
              ctx: AuthContext = Depends(require("tenant:update")),
              db: Session = Depends(get_db)) -> Tenant:
    t = db.get(Tenant, ctx.tenant_id)
    assert t is not None
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(t, k, v.strip() if isinstance(v, str) else v)
    t.updated_at = datetime.now(UTC)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="tenant.updated",
           target_type="tenant", target_id=t.id, meta={"fields": sorted(changes)}, request=request)
    db.commit()
    return t


@router.get("/roles", response_model=list[RoleOut])
def list_roles(_: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> list[Role]:
    return list(db.scalars(select(Role).order_by(Role.rank.desc())))


# ---------------------------------------------------------------- members
@router.get("/members", response_model=list[MemberOut])
def list_members(ctx: AuthContext = Depends(require("members:read")),
                 db: Session = Depends(get_db)) -> list[MemberOut]:
    rows = db.execute(
        select(Membership, Role.key, User).join(Role, Role.id == Membership.role_id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == ctx.tenant_id).order_by(Membership.created_at)
    ).all()
    return [MemberOut(membership_id=m.id, user_id=u.id, email=u.email, full_name=u.full_name,
                      role=r, status=m.status, joined_at=m.created_at) for m, r, u in rows]


@router.patch("/members/{membership_id}", response_model=MemberOut)
def change_role(membership_id: uuid.UUID, body: MemberPatch, request: Request,
                ctx: AuthContext = Depends(require("members:manage")),
                db: Session = Depends(get_db)) -> MemberOut:
    m, current_role, user = _member(db, ctx.tenant_id, membership_id)
    if m.user_id == ctx.user_id:
        raise Forbidden("You cannot change your own role")
    if current_role.rank >= ctx.role_rank and ctx.role_key != "owner":
        raise Forbidden("You cannot modify a member with an equal or higher role")
    new_role = _role(db, body.role)
    _assert_can_assign(ctx, new_role)
    demoting_owner = current_role.key == "owner" and new_role.key != "owner"
    if demoting_owner and _owner_count(db, ctx.tenant_id) <= 1:
        raise Conflict("An organization must keep at least one owner", code="last_owner")
    m.role_id = new_role.id
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="member.role_changed",
           target_type="membership", target_id=m.id,
           meta={"from": current_role.key, "to": new_role.key}, request=request)
    db.commit()
    return MemberOut(membership_id=m.id, user_id=user.id, email=user.email,
                     full_name=user.full_name, role=new_role.key, status=m.status,
                     joined_at=m.created_at)


@router.delete("/members/{membership_id}", status_code=204)
def remove_member(membership_id: uuid.UUID, request: Request,
                  ctx: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> Response:
    m, role, _user = _member(db, ctx.tenant_id, membership_id)
    leaving_self = m.user_id == ctx.user_id
    if not leaving_self:
        if not ctx.has("members:manage"):
            raise Forbidden("Missing permission: members:manage")
        if role.rank >= ctx.role_rank and ctx.role_key != "owner":
            raise Forbidden("You cannot remove a member with an equal or higher role")
    if role.key == "owner" and _owner_count(db, ctx.tenant_id) <= 1:
        raise Conflict("An organization must keep at least one owner", code="last_owner")
    db.delete(m)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="member.left" if leaving_self else "member.removed",
           target_type="user", target_id=m.user_id, request=request)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- invitations
@router.get("/invitations", response_model=list[InviteOut])
def list_invitations(_: AuthContext = Depends(require("members:manage")),
                     db: Session = Depends(get_db)) -> list[InviteOut]:
    rows = db.execute(
        select(Invitation, Role.key).join(Role, Role.id == Invitation.role_id)
        .where(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None),
               Invitation.expires_at > func.now())
        .order_by(Invitation.created_at.desc())
    ).all()
    return [InviteOut(id=i.id, email=i.email, role=r, expires_at=i.expires_at,
                      created_at=i.created_at) for i, r in rows]


@router.post("/invitations", response_model=InviteOut, status_code=201)
def create_invitation(body: InviteIn, request: Request,
                      ctx: AuthContext = Depends(require("members:manage")),
                      db: Session = Depends(get_db)) -> InviteOut:
    role = _role(db, body.role)
    _assert_can_assign(ctx, role)
    email = body.email.lower()
    already = db.scalar(
        select(func.count()).select_from(Membership).join(User, User.id == Membership.user_id)
        .where(User.email == email, Membership.tenant_id == ctx.tenant_id)
    )
    if already:
        raise Conflict("This person is already a member", code="already_member")
    now = datetime.now(UTC)
    db.execute(update(Invitation).where(Invitation.email == email,
                                        Invitation.accepted_at.is_(None),
                                        Invitation.revoked_at.is_(None)).values(revoked_at=now))
    raw, hashed = new_refresh_token()
    inv = Invitation(tenant_id=ctx.tenant_id, email=email, role_id=role.id, token_hash=hashed,
                     invited_by=ctx.user_id, expires_at=now + INVITE_TTL)
    db.add(inv)
    db.flush()
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="invitation.created",
           target_type="invitation", target_id=inv.id, meta={"email": email, "role": role.key},
           request=request)
    db.commit()
    return InviteOut(id=inv.id, email=email, role=role.key, expires_at=inv.expires_at,
                     created_at=inv.created_at, token=raw)


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke_invitation(invitation_id: uuid.UUID, request: Request,
                      ctx: AuthContext = Depends(require("members:manage")),
                      db: Session = Depends(get_db)) -> Response:
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.revoked_at or inv.accepted_at:
        raise NotFound("Invitation not found")
    inv.revoked_at = datetime.now(UTC)
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="invitation.revoked",
           target_type="invitation", target_id=inv.id, request=request)
    db.commit()
    return Response(status_code=204)


@router.post("/invitations/accept", response_model=TokenOut)
def accept_invitation(body: AcceptInvitationIn, request: Request, response: Response,
                      ctx: AuthContext = Depends(get_auth),
                      db: Session = Depends(get_db)) -> TokenOut:
    me = db.get(User, ctx.user_id)
    assert me is not None
    if me.email_verified_at is None:
        # Otherwise anyone could register with someone else's address and take their seat.
        raise Forbidden("Verify your email address before accepting an invitation",
                        code="email_unverified")
    inv = db.execute(
        text("SELECT id, tenant_id, email, role_id, expires_at, accepted_at, revoked_at "
             "FROM auth_find_invitation(:h)"), {"h": hash_token(body.token)}
    ).one_or_none()
    # Same error for every failure mode: don't help an attacker probe tokens.
    if (inv is None or inv.accepted_at or inv.revoked_at
            or inv.expires_at <= datetime.now(UTC) or inv.email != me.email):
        raise NotFound("Invitation not found or no longer valid", code="invalid_invitation")

    set_context(db, tenant_id=inv.tenant_id, user_id=ctx.user_id)
    exists = db.scalar(select(Membership.id).where(Membership.user_id == ctx.user_id,
                                                   Membership.tenant_id == inv.tenant_id))
    if not exists:
        db.add(Membership(tenant_id=inv.tenant_id, user_id=ctx.user_id, role_id=inv.role_id))
    db.execute(update(Invitation).where(Invitation.id == inv.id)
               .values(accepted_at=datetime.now(UTC)))
    record(db, tenant_id=inv.tenant_id, actor_user_id=ctx.user_id, action="invitation.accepted",
           target_type="invitation", target_id=inv.id, request=request)
    tokens = auth_service.issue_tokens(db, user_id=ctx.user_id, tenant_id=inv.tenant_id,
                                       user_agent=request.headers.get("user-agent"),
                                       ip=client_ip(request))
    db.commit()
    return _deliver(request, response, tokens)
