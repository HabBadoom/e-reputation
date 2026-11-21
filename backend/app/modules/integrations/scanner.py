"""AnyPulse side of the Scanner integration: /api/v1/integrations/scanner/*.

Only the Scanner integration client calls these endpoints, with HMAC-signed requests (no
AnyPulse session). Every Scanner account gets its own AnyPulse organization, created and
linked automatically (decision of 2026-11-21); only platform administrators revoke that access
(admin router). Scanner users are collection agents: they never read AnyPulse data and never
choose a brand. From phase 7, collections will be submitted here.
"""

import logging
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFound, Unauthorized
from app.core.rate_limit import get_limiter
from app.db.models import ScannerLink, Tenant
from app.db.session import get_db, set_context, set_provisioning_context
from app.integrations.anypulse import signing
from app.modules.audit.service import record
from app.modules.auth.service import _slugify
from app.modules.billing.entitlements import create_subscription

log = logging.getLogger("repintel.integrations.scanner")
router = APIRouter(prefix="/integrations/scanner", tags=["integrations"])


# ---- schemas ---------------------------------------------------------------------------------
class ProvisionOut(BaseModel):
    link_id: uuid.UUID
    organization_name: str
    status: str
    created: bool


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
    record(db, tenant_id=tenant_id, actor_user_id=None, action="scanner.organization_created",
           target_type="scanner_link", target_id=link.id, meta={"scanner_account": row.email},
           request=request)
    db.commit()
    return ProvisionOut(link_id=link.id, organization_name=name, status="active", created=True)
