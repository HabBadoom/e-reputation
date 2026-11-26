"""Phase 7: the platform administrators' review queue for sent screenshots AynPulse could not
attribute to a brand with confidence (decision 2026-11-25). The administrator sees the image and
the text read from it, then picks the followed brands (delivered to every organisation that
follows them) or discards it.

Called from the admin console (platform administrator context). The attribution rows are
readable by platform administrators (RLS); the screenshot and its entries are read in the
sending agent's Scanner context; mentions are written in each organisation's context.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collection.text import normalize_for_match
from app.core.errors import Conflict, NotFound
from app.db.models import ScannerAttribution, ScannerCollectionItem, ScannerUploadedFile
from app.db.session import set_context, set_scanner_context
from app.scanner import transmit
from app.scanner.audit import record


def _restore(db: Session, ctx: Any) -> None:
    set_context(db, tenant_id=ctx.tenant_id, user_id=ctx.user_id)


def _attribution(db: Session, item_id: uuid.UUID) -> ScannerAttribution:
    att = db.get(ScannerAttribution, item_id)          # RLS: platform administrators
    if att is None:
        raise NotFound("Screenshot not found", code="item_not_found")
    return att


def pending(db: Session, ctx: Any, limit: int = 50) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(ScannerAttribution)
                      .where(ScannerAttribution.status == "to_review")) or 0
    rows = db.scalars(select(ScannerAttribution).where(ScannerAttribution.status == "to_review")
                      .order_by(ScannerAttribution.created_at).limit(limit)).all()
    out = []
    for att in rows:
        info = {"item_id": att.item_id, "collection_id": att.session_id,
                "sent_at": att.created_at, "reason": att.reason,
                "suggested": att.brand_names, "confidence": att.confidence}
        set_scanner_context(db, account_id=att.account_id)
        item = db.get(ScannerCollectionItem, att.item_id)
        if item is not None:
            c = transmit._content(db, item)
            has_image = db.scalar(select(ScannerUploadedFile.id)
                                  .where(ScannerUploadedFile.item_id == item.id)) is not None
            info |= {"platform": item.platform, "source_url": item.source_url,
                     "page_or_group": c.context.get("page_or_group", ""),
                     "dialog_title": c.context.get("dialog_title", ""),
                     "validated": c.validated, "captured_at": c.captured_at,
                     "has_image": has_image,
                     "entries": [{"kind": e.kind, "author": e.author_name, "text": e.text,
                                  "image_text": e.image_text, "time": e.relative_time}
                                 for e in c.entries]}
        out.append(info)
        _restore(db, ctx)
    return {"total": total, "items": out}


def brands(db: Session) -> list[dict[str, Any]]:
    """Brands that can be chosen: one line per brand name, with how many organisations follow
    it (organisation names are not shown here)."""
    return [{"name": g.name, "organisations": len({t.tenant_id for t in g.targets})}
            for g in sorted(transmit.catalogue(db).values(), key=lambda g: g.name.lower())]


def image(db: Session, ctx: Any, item_id: uuid.UUID) -> tuple[bytes, str]:
    att = _attribution(db, item_id)
    set_scanner_context(db, account_id=att.account_id)
    f = db.scalar(select(ScannerUploadedFile).where(ScannerUploadedFile.item_id == item_id))
    if f is None:
        _restore(db, ctx)
        raise NotFound("The image is no longer kept", code="image_purged")
    from app.scanner.storage import get_storage
    data = get_storage().get(f.storage_key)
    _restore(db, ctx)
    return data, f.mime_type


def decide(db: Session, ctx: Any, item_id: uuid.UUID, *, brand_names: list[str],
           discard: bool) -> dict[str, Any]:
    att = _attribution(db, item_id)
    if att.status != "to_review":
        raise Conflict("This screenshot was already handled", code="already_handled")
    account_id, now = att.account_id, datetime.now(UTC)
    created = orgs = 0
    chosen: list[transmit.BrandGroup] = []
    if not discard:
        groups = transmit.catalogue(db)
        wanted = {normalize_for_match(n) for n in brand_names}
        chosen = [g for k, g in groups.items() if k in wanted]
        if not chosen:
            raise Conflict("Choose at least one followed brand, or discard",
                           code="no_brand_chosen")
        set_scanner_context(db, account_id=account_id)
        item = db.get(ScannerCollectionItem, item_id)
        if item is None:
            raise NotFound("Screenshot not found", code="item_not_found")
        content = transmit._content(db, item)
        created, orgs, _ = transmit.deliver(db, content, chosen, account_id=account_id)
    set_scanner_context(db, account_id=account_id)
    att = db.get(ScannerAttribution, item_id)
    assert att is not None
    att.status = "discarded" if discard else "delivered"
    att.method, att.decided_by, att.decided_at, att.updated_at = "admin", ctx.user_id, now, now
    if not discard:
        att.brand_names = [g.name for g in chosen]
        att.mentions_created, att.organisations, att.confidence = created, orgs, 1.0
    record(db, account_id=account_id, action="attribution.decided",
           target_type="collection_item", target_id=item_id,
           meta={"by": str(ctx.user_id), "discarded": discard, "brands": att.brand_names,
                 "mentions": created})
    _restore(db, ctx)
    return {"item_id": item_id, "status": att.status, "mentions_created": created,
            "organisations": orgs}
