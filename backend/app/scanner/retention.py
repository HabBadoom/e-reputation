"""Phase 6: how long Scanner keeps screenshots (decision D9, 2026-11-24).

- A sent collection's images are kept `REPINTEL_SCANNER_RETENTION_SENT_DAYS` (30) after sending,
  then deleted; the extracted text has gone to AnyPulse and stays in Scanner's history.
- A collection never sent (open or cancelled) without activity for
  `REPINTEL_SCANNER_RETENTION_IDLE_DAYS` (30) is deleted with its images; the agent sees the date
  from `REPINTEL_SCANNER_RETENTION_WARNING_DAYS` (7) before.

Runs inside the daily maintenance job, one transaction per agent, in that agent's context.
Stored objects are deleted before their rows: if storage fails, the rows stay and the next run
tries again (never a row pointing to a deleted image, at worst an image kept one more day).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ScannerCollectionSession, ScannerUploadedFile
from app.db.session import session_scope, set_scanner_context
from app.scanner.audit import record
from app.scanner.storage import StorageError, get_storage

log = logging.getLogger("repintel.scanner.retention")
ABANDONABLE = ("draft", "collecting", "ready", "cancelled")


def expires_at(s: ScannerCollectionSession) -> datetime | None:
    """When an unsent collection will be deleted for inactivity (None once it is sent)."""
    if s.status not in ABANDONABLE:
        return None
    return s.updated_at + timedelta(days=get_settings().scanner_retention_idle_days)


def set_after_sending(db: Session, session_id: uuid.UUID, sent_at: datetime) -> int:
    """Phase 7: the images of a sent collection are deleted N days after sending."""
    until = sent_at + timedelta(days=get_settings().scanner_retention_sent_days)
    from sqlalchemy import update
    return db.execute(update(ScannerUploadedFile)
                      .where(ScannerUploadedFile.session_id == session_id)
                      .values(retention_until=until)).rowcount


def _delete_objects(keys: list[str]) -> bool:
    storage = get_storage()
    try:
        for k in keys:
            storage.delete(k)
    except StorageError:
        log.warning("scanner_retention_storage_failed", extra={"objects": len(keys)})
        return False
    return True


def purge_account(db: Session, account_id: uuid.UUID, now: datetime) -> dict[str, int]:
    cfg = get_settings()
    set_scanner_context(db, account_id=account_id)
    out = {"files": 0, "collections": 0}

    expired = db.scalars(select(ScannerUploadedFile).where(
        ScannerUploadedFile.retention_until.is_not(None),
        ScannerUploadedFile.retention_until < now)).all()
    if expired and _delete_objects([f.storage_key for f in expired]):
        db.execute(delete(ScannerUploadedFile).where(
            ScannerUploadedFile.id.in_([f.id for f in expired])))
        out["files"] = len(expired)
        record(db, account_id=account_id, action="files.retention_deleted",
               meta={"files": len(expired), "after_days": cfg.scanner_retention_sent_days})

    limit = now - timedelta(days=cfg.scanner_retention_idle_days)
    stale = db.scalars(select(ScannerCollectionSession).where(
        ScannerCollectionSession.status.in_(ABANDONABLE),
        ScannerCollectionSession.updated_at < limit)).all()
    for s in stale:
        keys = list(db.scalars(select(ScannerUploadedFile.storage_key)
                               .where(ScannerUploadedFile.session_id == s.id)))
        if not _delete_objects(keys):
            continue
        db.execute(delete(ScannerCollectionSession)
                   .where(ScannerCollectionSession.id == s.id))   # items, files, text: cascade
        out["collections"] += 1
        record(db, account_id=account_id, action="collection.expired",
               meta={"status": s.status, "idle_days": cfg.scanner_retention_idle_days,
                     "files": len(keys)})
    return out


def run(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    cfg = get_settings()
    if not cfg.scanner_enabled:
        return {}
    with session_scope() as db:
        accounts = [r[0] for r in db.execute(
            text("SELECT * FROM scanner_retention_accounts(:d)"),
            {"d": cfg.scanner_retention_idle_days})]
    stats = {"accounts": 0, "files": 0, "collections": 0}
    for account_id in accounts:
        try:
            with session_scope() as db:
                res = purge_account(db, account_id, now)
            stats["accounts"] += 1
            stats["files"] += res["files"]
            stats["collections"] += res["collections"]
        except Exception:
            log.exception("scanner_retention_failed", extra={"account_id": str(account_id)})
    if accounts:
        log.info("scanner_retention", extra=stats)
    return stats
