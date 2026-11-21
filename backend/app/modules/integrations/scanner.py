"""AnyPulse side of the Scanner integration: /api/v1/integrations/scanner/*.

Two kinds of callers:
  * AnyPulse members (normal AnyPulse auth): inspect, approve or decline a Scanner link
    request for their current organization, list and revoke the organization's links.
  * The Scanner integration client (HMAC-signed, no AnyPulse session): list the brand names
    of a linked organization. From phase 7 it will also submit collections here.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_auth, require
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized
from app.core.rate_limit import get_limiter
from app.core.security import hash_token
from app.db.models import Brand, ScannerLink
from app.db.session import get_db, set_context
from app.integrations.anypulse import signing
from app.modules.audit.service import record

router = APIRouter(prefix="/integrations/scanner", tags=["integrations"])


# ---- schemas ---------------------------------------------------------------------------------
class LinkRequestTokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class LinkRequestOut(BaseModel):
    account_email: str
    account_name: str
    status: str            # pending | approved | declined | expired
    expires_at: datetime
    organization_name: str
    can_decide: bool


class ScannerLinkOut(BaseModel):
    id: uuid.UUID
    account_email: str
    account_name: str
    status: str
    created_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None


class BrandRef(BaseModel):
    id: uuid.UUID
    name: str


class BrandList(BaseModel):
    items: list[BrandRef]


def _sqlstate(e: DBAPIError) -> str | None:
    return getattr(e.orig, "sqlstate", None)


def _link_out(link: ScannerLink) -> ScannerLinkOut:
    return ScannerLinkOut(id=link.id, account_email=link.account_email,
                          account_name=link.account_name, status=link.status,
                          created_at=link.created_at, revoked_at=link.revoked_at,
                          revoked_by=link.revoked_by)


# ---- AnyPulse members: consent ---------------------------------------------------------------
@router.post("/link-requests/inspect", response_model=LinkRequestOut)
def inspect_request(body: LinkRequestTokenIn, ctx: AuthContext = Depends(get_auth),
                    db: Session = Depends(get_db)) -> LinkRequestOut:
    row = db.execute(text("SELECT id, status, expires_at, account_email, account_name "
                          "FROM scanner_link_request_peek(:h)"),
                     {"h": hash_token(body.token)}).one_or_none()
    if row is None:
        raise NotFound("Link request not found", code="invalid_link_request")
    status = row.status
    if status == "pending" and row.expires_at <= datetime.now(UTC):
        status = "expired"
    org = db.scalar(text("SELECT name FROM tenants WHERE id = :t"), {"t": ctx.tenant_id})
    return LinkRequestOut(account_email=row.account_email, account_name=row.account_name,
                          status=status, expires_at=row.expires_at, organization_name=org or "",
                          can_decide=ctx.has("scanner:link"))


@router.post("/link-requests/approve", response_model=ScannerLinkOut)
def approve_request(body: LinkRequestTokenIn, request: Request,
                    ctx: AuthContext = Depends(require("scanner:link")),
                    db: Session = Depends(get_db)) -> ScannerLinkOut:
    try:
        link_id = db.scalar(text("SELECT scanner_link_approve(:h)"),
                            {"h": hash_token(body.token)})
    except DBAPIError as e:
        state = _sqlstate(e)
        db.rollback()
        if state == "P0002":       # no_data_found
            raise NotFound("Link request not found or no longer valid",
                           code="invalid_link_request") from e
        if state == "42501":       # insufficient_privilege
            raise Forbidden("Missing permission: scanner:link") from e
        raise
    link = db.get(ScannerLink, link_id)
    assert link is not None
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="scanner.link_approved",
           target_type="scanner_link", target_id=link.id,
           meta={"scanner_account": link.account_email}, request=request)
    db.commit()
    return _link_out(link)


@router.post("/link-requests/decline", status_code=204)
def decline_request(body: LinkRequestTokenIn, request: Request,
                    ctx: AuthContext = Depends(require("scanner:link")),
                    db: Session = Depends(get_db)) -> Response:
    declined = db.scalar(text("SELECT scanner_link_decline(:h)"), {"h": hash_token(body.token)})
    if not declined:
        raise NotFound("Link request not found or no longer valid", code="invalid_link_request")
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
           action="scanner.link_declined", request=request)
    db.commit()
    return Response(status_code=204)


# ---- AnyPulse members: the organization's links ----------------------------------------------
@router.get("/links", response_model=list[ScannerLinkOut])
def list_links(ctx: AuthContext = Depends(require("tenant:read")),
               db: Session = Depends(get_db)) -> list[ScannerLinkOut]:
    rows = db.scalars(select(ScannerLink).where(ScannerLink.tenant_id == ctx.tenant_id)
                      .order_by(ScannerLink.created_at.desc())).all()
    return [_link_out(r) for r in rows]


@router.post("/links/{link_id}/revoke", response_model=ScannerLinkOut)
def revoke_link(link_id: uuid.UUID, request: Request,
                ctx: AuthContext = Depends(require("scanner:link")),
                db: Session = Depends(get_db)) -> ScannerLinkOut:
    link = db.scalars(select(ScannerLink).where(ScannerLink.id == link_id,
                                                ScannerLink.tenant_id == ctx.tenant_id)
                      ).one_or_none()
    if link is None:
        raise NotFound("Scanner link not found")
    if link.status != "active":
        raise Conflict("This Scanner link is already revoked", code="link_revoked")
    link.status, link.revoked_by = "revoked", "anypulse"
    record(db, tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id, action="scanner.link_revoked",
           target_type="scanner_link", target_id=link.id,
           meta={"scanner_account": link.account_email}, request=request)
    db.commit()
    db.refresh(link)
    return _link_out(link)


# ---- Scanner integration client (signed) -----------------------------------------------------
class SignedLink:
    def __init__(self, link_id: uuid.UUID, account_id: uuid.UUID, tenant_id: uuid.UUID):
        self.link_id, self.account_id, self.tenant_id = link_id, account_id, tenant_id


async def _signed_body(request: Request) -> bytes:
    return await request.body()


def signed_link(request: Request, body: bytes = Depends(_signed_body),
                db: Session = Depends(get_db)) -> SignedLink:
    """Authenticate a Scanner integration call and switch the DB to the link's organization."""
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
        link_id, account_id = uuid.UUID(link), uuid.UUID(account)
    except ValueError as e:
        raise Unauthorized("Invalid integration signature", code="invalid_signature") from e
    get_limiter().hit(f"integration:scanner:{link_id}", 120, 60)
    row = db.execute(text("SELECT tenant_id, account_id, status, tenant_active "
                          "FROM integration_scanner_link(:l)"), {"l": link_id}).one_or_none()
    if row is None or row.account_id != account_id:
        raise NotFound("Scanner link not found", code="link_not_found")
    if row.status != "active" or not row.tenant_active:
        raise AppError("This Scanner link is no longer active", code="link_revoked",
                       status_code=409)
    set_context(db, tenant_id=row.tenant_id, user_id=None)
    return SignedLink(link_id, account_id, row.tenant_id)


@router.get("/links/{link_id}/brands", response_model=BrandList)
def link_brands(link_id: uuid.UUID, auth: SignedLink = Depends(signed_link),
                db: Session = Depends(get_db)) -> BrandList:
    if link_id != auth.link_id:
        raise NotFound("Scanner link not found", code="link_not_found")
    rows = db.scalars(select(Brand).where(Brand.status == "active")
                      .order_by(Brand.name)).all()   # RLS: the linked organization only
    return BrandList(items=[BrandRef(id=b.id, name=b.name) for b in rows])
