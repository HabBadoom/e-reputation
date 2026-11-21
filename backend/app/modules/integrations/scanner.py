"""AnyPulse side of the Scanner integration: /api/v1/integrations/scanner/*.

Only the Scanner integration client calls these endpoints, with HMAC-signed requests (no
AnyPulse session). Every Scanner account gets its own AnyPulse organization, created and
linked automatically (decision of 2026-11-21); only platform administrators revoke that access
(admin router). From phase 7, collections will be submitted here too.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound, Unauthorized
from app.core.rate_limit import get_limiter
from app.core.security import new_refresh_token
from app.db.models import Brand, Invitation, Membership, Role, ScannerLink, Tenant, User
from app.db.session import get_db, set_context, set_provisioning_context
from app.integrations.anypulse import signing
from app.modules.audit.service import record
from app.modules.auth.service import _slugify
from app.modules.billing.entitlements import create_subscription, ensure_active, ensure_capacity
from app.modules.notifications.email import queue_email
from app.modules.notifications.templates import invitation_email
from app.modules.tenancy.router import INVITE_TTL

log = logging.getLogger("repintel.integrations.scanner")
router = APIRouter(prefix="/integrations/scanner", tags=["integrations"])
INVITER_NAME = "Aynpulse Scanner"


# ---- schemas ---------------------------------------------------------------------------------
class ProvisionOut(BaseModel):
    link_id: uuid.UUID
    organization_name: str
    status: str
    created: bool


class BrandRef(BaseModel):
    id: uuid.UUID
    name: str


class BrandList(BaseModel):
    items: list[BrandRef]


class BrandCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class InvitationOut(BaseModel):
    email: str
    expires_at: datetime


# ---- signed calls ----------------------------------------------------------------------------
@dataclass(frozen=True)
class SignedCall:
    account_id: uuid.UUID
    link_id: uuid.UUID | None


async def _raw_body(request: Request) -> bytes:
    return await request.body()


def signed_call(request: Request, body: bytes = Depends(_raw_body)) -> SignedCall:
    """Verify the HMAC signature. The link header may be empty (before provisioning)."""
    h = request.headers
    account, link, ts = (h.get(signing.HEADER_ACCOUNT, ""), h.get(signing.HEADER_LINK, ""),
                         h.get(signing.HEADER_TIMESTAMP, ""))
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    ok = signing.verify(get_settings().scanner_integration_key,
                        h.get(signing.HEADER_SIGNATURE, ""), method=request.method, path=path,
                        timestamp=ts, account_id=account, link_id=link, body=body)
    if not ok:
        raise Unauthorized("Invalid integration signature", code="invalid_signature")
    try:
        return SignedCall(uuid.UUID(account), uuid.UUID(link) if link else None)
    except ValueError as e:
        raise Unauthorized("Invalid integration signature", code="invalid_signature") from e


@dataclass(frozen=True)
class LinkedCall:
    account_id: uuid.UUID
    link_id: uuid.UUID
    tenant_id: uuid.UUID


def linked_call(call: SignedCall = Depends(signed_call),
                db: Session = Depends(get_db)) -> LinkedCall:
    """A signed call through an *active* link: switches the DB to the link's organization."""
    if call.link_id is None:
        raise NotFound("No AnyPulse link for this Scanner account", code="link_not_found")
    get_limiter().hit(f"integration:scanner:{call.link_id}", 120, 60)
    row = db.execute(text("SELECT tenant_id, account_id, status, tenant_active, account_active "
                          "FROM integration_scanner_link(:l)"), {"l": call.link_id}).one_or_none()
    if row is None or row.account_id != call.account_id:
        raise NotFound("No AnyPulse link for this Scanner account", code="link_not_found")
    if row.status != "active" or not row.tenant_active or not row.account_active:
        raise AppError("Access to AnyPulse has been revoked for this Scanner account",
                       code="access_revoked", status_code=403)
    set_context(db, tenant_id=row.tenant_id, user_id=None)
    return LinkedCall(call.account_id, call.link_id, row.tenant_id)


def _invite_owner(db: Session, *, tenant_id: uuid.UUID, email: str, org_name: str,
                  locale: str) -> Invitation:
    """Invite the Scanner account's e-mail as owner of its organization (AnyPulse login is
    separate). Replaces a pending invitation for the same address. Requires the tenant
    context."""
    now = datetime.now(UTC)
    db.execute(update(Invitation).where(Invitation.email == email,
                                        Invitation.accepted_at.is_(None),
                                        Invitation.revoked_at.is_(None)).values(revoked_at=now))
    owner = db.scalars(select(Role).where(Role.key == "owner")).one()
    raw, hashed = new_refresh_token()
    inv = Invitation(tenant_id=tenant_id, email=email, role_id=owner.id, token_hash=hashed,
                     invited_by=None, expires_at=now + INVITE_TTL)
    db.add(inv)
    db.flush()
    queue_email(db, invitation_email(email, INVITER_NAME, org_name, locale, raw),
                tenant_id=tenant_id)
    return inv


@router.post("/provision", response_model=ProvisionOut)
def provision(request: Request, call: SignedCall = Depends(signed_call),
              db: Session = Depends(get_db)) -> ProvisionOut:
    """Create the account's AnyPulse organization and link (idempotent)."""
    get_limiter().hit(f"integration:scanner:provision:{call.account_id}", 30, 3600)
    row = db.execute(text("SELECT email, full_name, locale, status, link_id, tenant_id, "
                          "link_status FROM integration_scanner_account(:a)"),
                     {"a": call.account_id}).one_or_none()
    if row is None or row.status != "active":
        raise NotFound("Scanner account not found", code="account_not_found")
    if row.link_id is not None:
        set_context(db, tenant_id=row.tenant_id, user_id=None)
        org = db.get(Tenant, row.tenant_id)
        return ProvisionOut(link_id=row.link_id, organization_name=org.name if org else "",
                            status=row.link_status, created=False)

    tenant_id = uuid.uuid4()
    name = row.full_name[:200]
    set_provisioning_context(db, tenant_id=tenant_id, scanner_account_id=call.account_id)
    try:
        db.add(Tenant(id=tenant_id, name=name, slug=_slugify(name), default_locale=row.locale))
        db.flush()
        create_subscription(db, tenant_id)
        link = ScannerLink(account_id=call.account_id, tenant_id=tenant_id, tenant_name=name)
        db.add(link)
        db.flush()
    except IntegrityError:
        # A concurrent call provisioned it first: return that one.
        db.rollback()
        existing = db.execute(text("SELECT link_id, tenant_id, link_status "
                                   "FROM integration_scanner_account(:a)"),
                              {"a": call.account_id}).one()
        if existing.link_id is None:
            raise
        log.info("scanner_provision_race", extra={"account": str(call.account_id)})
        set_context(db, tenant_id=existing.tenant_id, user_id=None)
        org = db.get(Tenant, existing.tenant_id)
        return ProvisionOut(link_id=existing.link_id, organization_name=org.name if org else "",
                            status=existing.link_status, created=False)
    _invite_owner(db, tenant_id=tenant_id, email=row.email, org_name=name, locale=row.locale)
    record(db, tenant_id=tenant_id, actor_user_id=None, action="scanner.organization_created",
           target_type="scanner_link", target_id=link.id, meta={"scanner_account": row.email},
           request=request)
    db.commit()
    return ProvisionOut(link_id=link.id, organization_name=name, status="active", created=True)


@router.get("/brands", response_model=BrandList)
def list_brands(call: LinkedCall = Depends(linked_call),
                db: Session = Depends(get_db)) -> BrandList:
    rows = db.scalars(select(Brand).where(Brand.tenant_id == call.tenant_id,
                                          Brand.status == "active").order_by(Brand.name)).all()
    return BrandList(items=[BrandRef(id=b.id, name=b.name) for b in rows])


@router.post("/brands", response_model=BrandRef, status_code=201)
def create_brand(body: BrandCreateIn, request: Request, call: LinkedCall = Depends(linked_call),
                 db: Session = Depends(get_db)) -> BrandRef:
    ensure_active(db, call.tenant_id)
    ensure_capacity(db, call.tenant_id, "max_brands")
    b = Brand(tenant_id=call.tenant_id, name=body.name)
    db.add(b)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise Conflict("A brand with this name already exists", code="duplicate") from e
    record(db, tenant_id=call.tenant_id, actor_user_id=None, action="brand.created",
           target_type="brand", target_id=b.id, meta={"name": b.name, "via": "scanner"},
           request=request)
    db.commit()
    return BrandRef(id=b.id, name=b.name)


@router.post("/invitation", response_model=InvitationOut)
def resend_invitation(request: Request, call: LinkedCall = Depends(linked_call),
                      db: Session = Depends(get_db)) -> InvitationOut:
    """Send again the invitation to become owner of the account's organization in AnyPulse."""
    get_limiter().hit(f"integration:scanner:invite:{call.link_id}", 5, 3600)
    row = db.execute(text("SELECT email, locale FROM integration_scanner_account(:a)"),
                     {"a": call.account_id}).one()
    member = db.scalar(select(func.count()).select_from(Membership)
                       .join(User, User.id == Membership.user_id)
                       .where(Membership.tenant_id == call.tenant_id, User.email == row.email))
    if member:
        raise Conflict("You already have access to this organization in AnyPulse",
                       code="already_member")
    org = db.get(Tenant, call.tenant_id)
    assert org is not None
    inv = _invite_owner(db, tenant_id=call.tenant_id, email=row.email, org_name=org.name,
                        locale=row.locale)
    record(db, tenant_id=call.tenant_id, actor_user_id=None, action="invitation.created",
           target_type="invitation", target_id=inv.id,
           meta={"email": row.email, "role": "owner", "via": "scanner"}, request=request)
    db.commit()
    return InvitationOut(email=row.email, expires_at=inv.expires_at)
