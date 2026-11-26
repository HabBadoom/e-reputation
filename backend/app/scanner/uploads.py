"""Screenshot items: add (upload), list, reorder, remove, read back.

Each uploaded image becomes one `screenshot` item of the collection, queued for extraction
(phase 4) when a vision model is configured, else left `pending`. A near-identical image is
only *flagged* (`possible_duplicate_of`), never merged, and an exact duplicate of an image
already in the collection is refused; repeated comments across overlapping screenshots are
linked by extraction.
"""

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.db.models import ScannerCollectionItem, ScannerCollectionSession, ScannerUploadedFile
from app.scanner import images
from app.scanner.sessions import OPEN_STATUSES
from app.scanner.storage import StorageError, get_storage

log = logging.getLogger("repintel.scanner.uploads")


class StorageUnavailable(AppError):
    status_code = 503
    code = "storage_unavailable"


@dataclass
class FileResult:
    filename: str
    status: str                 # added | duplicate | rejected
    code: str | None = None     # reason when duplicate/rejected
    item_id: uuid.UUID | None = None


def clean_filename(name: str | None) -> str:
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(c for c in name if c.isprintable())
    return re.sub(r"\s+", " ", name).strip()[:255]


def require_open(s: ScannerCollectionSession) -> ScannerCollectionSession:
    if s.status not in OPEN_STATUSES:
        raise Conflict("This collection is no longer open", code="session_not_open")
    return s


def _item_count(db: Session, session_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(ScannerCollectionItem)
                     .where(ScannerCollectionItem.session_id == session_id)) or 0


def _next_position(db: Session, session_id: uuid.UUID) -> int:
    top = db.scalar(select(func.max(ScannerCollectionItem.position))
                    .where(ScannerCollectionItem.session_id == session_id))
    return 0 if top is None else top + 1


def add_screenshots(db: Session, s: ScannerCollectionSession,
                    files: list[tuple[str, bytes]], *, source_url: str | None = None,
                    platform: str | None = None,
                    link_item_id: uuid.UUID | None = None) -> list[FileResult]:
    """Validate, sanitise, store and attach each image. One bad file never blocks the others.
    `source_url`/`platform` record where a capture was taken (built-in browser);
    `link_item_id` the pasted link it was taken from (phase 5)."""
    cfg = get_settings()
    require_open(s)
    storage = get_storage()
    existing = db.execute(select(ScannerUploadedFile.item_id, ScannerUploadedFile.sha256,
                                 ScannerUploadedFile.dhash)
                          .where(ScannerUploadedFile.session_id == s.id)).all()
    seen = {r.sha256: r.item_id for r in existing}
    hashes = [(r.dhash, r.item_id) for r in existing]
    room = cfg.scanner_max_items_per_session - _item_count(db, s.id)
    position = _next_position(db, s.id)
    results: list[FileResult] = []
    stored: list[str] = []

    for raw_name, raw in files:
        name = clean_filename(raw_name)
        if room <= 0:
            results.append(FileResult(name, "rejected", "collection_full"))
            continue
        try:
            img = images.sanitize(raw, max_bytes=cfg.scanner_upload_max_bytes,
                                  max_pixels=cfg.scanner_upload_max_pixels,
                                  max_side=cfg.scanner_upload_max_side)
        except images.ImageRejected as e:
            results.append(FileResult(name, "rejected", e.code))
            continue
        if img.sha256 in seen:
            results.append(FileResult(name, "duplicate", "duplicate_image", seen[img.sha256]))
            continue
        near = next((item for h, item in hashes
                     if images.hamming(h, img.dhash) <= images.NEAR_DUPLICATE_MAX_DISTANCE), None)

        item_id, file_id = uuid.uuid4(), uuid.uuid4()
        key = f"scanner/{s.account_id}/{s.id}/{file_id}.{img.extension}"
        try:
            storage.put(key, img.data, img.mime_type)
        except StorageError as e:
            log.error("scanner_storage_put_failed", extra={"session": str(s.id)})
            for k in stored:   # the request fails as a whole: do not leave earlier files behind
                _discard(storage, k)
            raise StorageUnavailable("File storage is unavailable; nothing was lost on your "
                                     "device, try again") from e
        stored.append(key)
        sp = db.begin_nested()
        try:
            db.add(ScannerCollectionItem(
                id=item_id, account_id=s.account_id, session_id=s.id, source_type="screenshot",
                position=position, status="pending", possible_duplicate_of=near,
                source_url=(source_url or None) and source_url[:2000], platform=platform,
                link_item_id=link_item_id))
            db.flush()
            db.add(ScannerUploadedFile(
                id=file_id, account_id=s.account_id, session_id=s.id, item_id=item_id,
                storage_key=key, original_filename=name, mime_type=img.mime_type,
                size_bytes=len(img.data), width=img.width, height=img.height,
                sha256=img.sha256, dhash=img.dhash))
            db.flush()
            sp.commit()
        except IntegrityError:
            # Same image uploaded concurrently (unique per collection): keep the first one.
            sp.rollback()
            _discard(storage, key)
            results.append(FileResult(name, "duplicate", "duplicate_image"))
            continue
        seen[img.sha256] = item_id
        hashes.append((img.dhash, item_id))
        position += 1
        room -= 1
        results.append(FileResult(name, "added", None, item_id))

    if any(r.status == "added" for r in results):
        if s.status == "draft":
            s.status = "collecting"
        s.updated_at = datetime.now(UTC)
        if link_item_id is not None:           # the link now has its capture(s)
            link = db.get(ScannerCollectionItem, link_item_id)
            if link is not None and link.status == "pending":
                link.status, link.updated_at = "ready", s.updated_at
        from app.scanner.extraction.service import queue_items
        queue_items(db, s.account_id, s.id,
                    [r.item_id for r in results if r.status == "added" and r.item_id])
    return results


def _discard(storage, key: str) -> None:  # noqa: ANN001
    try:
        storage.delete(key)
    except StorageError:
        log.warning("scanner_storage_delete_failed")   # orphan; swept by retention (phase 6)


def list_items(db: Session, session_id: uuid.UUID
               ) -> list[tuple[ScannerCollectionItem, ScannerUploadedFile | None]]:
    rows = db.execute(
        select(ScannerCollectionItem, ScannerUploadedFile)
        .outerjoin(ScannerUploadedFile, ScannerUploadedFile.item_id == ScannerCollectionItem.id)
        .where(ScannerCollectionItem.session_id == session_id)
        .order_by(ScannerCollectionItem.position, ScannerCollectionItem.created_at)).all()
    return [(r[0], r[1]) for r in rows]


def get_item(db: Session, item_id: uuid.UUID) -> ScannerCollectionItem:
    item = db.get(ScannerCollectionItem, item_id)   # RLS: own items only
    if item is None:
        raise NotFound("Item not found")
    return item


def remove_item(db: Session, item: ScannerCollectionItem, s: ScannerCollectionSession
                ) -> list[str]:
    """Delete the item (and its file rows). Returns the storage keys to delete after commit."""
    require_open(s)
    keys = list(db.scalars(select(ScannerUploadedFile.storage_key)
                           .where(ScannerUploadedFile.item_id == item.id)))
    db.delete(item)
    db.flush()
    if _item_count(db, s.id) == 0 and s.status == "collecting":
        s.status = "draft"
    s.updated_at = datetime.now(UTC)
    return keys


def delete_objects(keys: list[str]) -> None:
    storage = get_storage()
    for k in keys:
        _discard(storage, k)


def reorder(db: Session, s: ScannerCollectionSession, item_ids: list[uuid.UUID]) -> None:
    require_open(s)
    items = {i.id: i for i in db.scalars(select(ScannerCollectionItem)
                                          .where(ScannerCollectionItem.session_id == s.id))}
    if len(item_ids) != len(set(item_ids)) or set(item_ids) != set(items):
        raise Conflict("The new order must list every item of the collection exactly once",
                       code="invalid_order")
    for pos, iid in enumerate(item_ids):
        items[iid].position = pos
    s.updated_at = datetime.now(UTC)


def read_file(db: Session, file_id: uuid.UUID) -> tuple[ScannerUploadedFile, bytes]:
    f = db.get(ScannerUploadedFile, file_id)   # RLS: own files only
    if f is None:
        raise NotFound("File not found")
    try:
        return f, get_storage().get(f.storage_key)
    except StorageError as e:
        raise StorageUnavailable("File storage is unavailable; try again") from e
