"""Extraction runs: queue a screenshot, read it with the vision model, store the entries.

Item status: pending (no extraction configured) → processing (queued or running) → ready,
needs_review (low confidence, unreadable or no text) or failed (error_code says why).
Runs as the `scanner.extract` background job, in the agent's Scanner context (RLS).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    ScannerCollectionItem,
    ScannerExtractedEntry,
    ScannerExtraction,
    ScannerUploadedFile,
)
from app.jobs import queue
from app.nlp.llm import LLMError
from app.scanner.extraction import dates, schema, text, vision
from app.scanner.storage import StorageError, get_storage

log = logging.getLogger("repintel.scanner.extraction")
JOB_KIND = "scanner.extract"
MAX_ATTEMPTS = 4
# "unreadable_parts" is shown to the agent but does not by itself send to review: on the first
# benchmark (104 real captures) models raised it on a third of fully readable screenshots.
REVIEW_ISSUES = {"not_social_media", "no_text"}


def queue_items(db: Session, account_id: uuid.UUID, session_id: uuid.UUID,
                item_ids: list[uuid.UUID]) -> int:
    """Queue extraction for these items (no-op when extraction is not configured)."""
    if not item_ids or not vision.configured():
        return 0
    now = datetime.now(UTC)
    for item_id in item_ids:
        ex = db.get(ScannerExtraction, item_id)
        if ex is None:
            db.add(ScannerExtraction(item_id=item_id, account_id=account_id,
                                     session_id=session_id, status="queued"))
        else:
            ex.status, ex.error_code, ex.updated_at = "queued", None, now
        db.execute(update(ScannerCollectionItem).where(ScannerCollectionItem.id == item_id)
                   .values(status="processing", error_code=None, updated_at=now))
        queue.enqueue(db, JOB_KIND, {"account_id": str(account_id), "item_id": str(item_id)},
                      max_attempts=MAX_ATTEMPTS)
    db.flush()
    return len(item_ids)


def _finish(db: Session, item: ScannerCollectionItem, ex: ScannerExtraction, *, status: str,
            item_status: str, error: str | None = None) -> None:
    now = datetime.now(UTC)
    ex.status, ex.error_code, ex.finished_at, ex.updated_at = status, error, now, now
    item.status, item.error_code, item.updated_at = item_status, error, now


def run(db: Session, item_id: uuid.UUID, *, attempt: int = 1,
        last_attempt: bool = True) -> str:
    """Extract one item. Returns the item's new status. Raises LLMError only for a temporary
    provider problem when another attempt will follow."""
    item = db.get(ScannerCollectionItem, item_id)
    ex = db.get(ScannerExtraction, item_id)
    if item is None or ex is None:
        return "gone"                         # removed meanwhile
    f = db.scalar(select(ScannerUploadedFile).where(ScannerUploadedFile.item_id == item_id))
    if f is None:
        _finish(db, item, ex, status="failed", item_status="failed", error="no_file")
        return "failed"
    cfg = get_settings()
    ex.status, ex.attempts = "running", attempt
    ex.started_at = datetime.now(UTC)
    captured = dates.captured_at(f.original_filename, f.created_at,
                                 cfg.scanner_extraction_timezone)
    ex.captured_at = captured

    try:
        png = _as_png(get_storage().get(f.storage_key))
        parts = vision.prepare(png)
        res = vision.get_provider().read(parts, captured=captured.isoformat(timespec="minutes"))
    except StorageError:
        if not last_attempt:
            raise
        _finish(db, item, ex, status="failed", item_status="failed", error="storage_unavailable")
        return "failed"
    except LLMError as e:
        if not e.fatal and not last_attempt:
            raise
        log.warning("scanner_extraction_failed", extra={"item": str(item_id), "reason": e.reason})
        _finish(db, item, ex, status="failed", item_status="failed", error=e.reason[:50])
        return "failed"

    ex.model, ex.input_tokens, ex.output_tokens = res.model[:100], res.input_tokens, \
        res.output_tokens
    ex.raw = {"prompt": schema.PROMPT_VERSION, "parts": res.parts, "output": res.data}
    try:
        parsed = schema.parse(res.data)
    except schema.BadOutput:
        _finish(db, item, ex, status="failed", item_status="failed", error="bad_output")
        return "failed"

    ex.context, ex.issues, ex.confidence = parsed.context, parsed.issues, parsed.confidence
    if item.platform is None and parsed.platform not in ("unknown",):
        item.platform = parsed.platform
    item.content_type = _content_type(parsed)
    store(db, item, parsed, captured, cfg.scanner_extraction_timezone)

    review = (parsed.confidence < cfg.scanner_extraction_min_confidence
              or bool(REVIEW_ISSUES & set(parsed.issues)))
    status = "needs_review" if review else "ready"
    _finish(db, item, ex, status="done", item_status=status)
    return status


def _as_png(data: bytes) -> bytes:
    """Stored images are PNG, JPEG or WebP; the model gets PNG (lossless re-encode)."""
    import io

    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        if im.format == "PNG":
            return data
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG")
        return buf.getvalue()


def _content_type(parsed: schema.Result) -> str:
    kinds = {e.kind for e in parsed.entries}
    if "post" in kinds and kinds - {"post"}:
        return "post_and_comments"
    if "post" in kinds:
        return "post"
    return "comments" if kinds else "unknown"


def store(db: Session, item: ScannerCollectionItem, parsed: schema.Result, captured: datetime,
          tz: str) -> list[ScannerExtractedEntry]:
    """Replace the item's entries with the parsed ones, linked to their parent and checked for
    duplicates against the other screenshots of the collection."""
    db.execute(delete(ScannerExtractedEntry).where(ScannerExtractedEntry.item_id == item.id))
    db.flush()
    earlier = db.scalars(select(ScannerExtractedEntry).where(
        ScannerExtractedEntry.session_id == item.session_id,
        ScannerExtractedEntry.item_id != item.id,
        ScannerExtractedEntry.duplicate_of.is_(None))).all()
    by_key = {e.dedup_key: e for e in earlier}

    rows: list[ScannerExtractedEntry] = []
    for pos, e in enumerate(parsed.entries):
        body = e.text or e.image_text
        key = text.dedup_key(e.author, body)
        when = dates.published(e.relative_time, captured, tz)
        row = ScannerExtractedEntry(
            id=uuid.uuid4(), account_id=item.account_id, session_id=item.session_id,
            item_id=item.id, position=pos, kind=e.kind,
            parent_id=rows[e.parent].id if e.parent is not None else None,
            author_name=e.author, author_is_page=e.author_is_page, text=e.text,
            image_text=e.image_text, script=text.script_of(f"{e.text} {e.image_text}"),
            text_truncated=e.text_truncated, partially_visible=e.partially_visible,
            relative_time=e.relative_time,
            published_at=when[0] if when else None, published_precision=when[1] if when else None,
            reactions_count=e.reactions_count, comments_count=e.comments_count,
            shares_count=e.shares_count, dedup_key=key)
        same = by_key.get(key)
        if same is None and (e.partially_visible or e.text_truncated):
            same = next((o for o in earlier if text.normalise(o.author_name)
                         == text.normalise(e.author)
                         and text.continues(body, o.text or o.image_text)), None)
        if same is not None:
            row.duplicate_of = same.id
        rows.append(row)
    db.add_all(rows)
    db.flush()
    # A cut copy seen earlier that this screenshot shows in full now points to the full one.
    for row in rows:
        if row.duplicate_of is not None:
            continue
        for o in earlier:
            if (o.duplicate_of is None and (o.partially_visible or o.text_truncated)
                    and text.normalise(o.author_name) == text.normalise(row.author_name)
                    and text.continues(o.text or o.image_text, row.text or row.image_text)):
                o.duplicate_of = row.id
    db.flush()
    return rows


# ---- review (agent) ----------------------------------------------------------------------
def entries_of(db: Session, item_id: uuid.UUID) -> list[ScannerExtractedEntry]:
    return list(db.scalars(select(ScannerExtractedEntry)
                           .where(ScannerExtractedEntry.item_id == item_id)
                           .order_by(ScannerExtractedEntry.position)))


def get_entry(db: Session, entry_id: uuid.UUID) -> ScannerExtractedEntry:
    from app.core.errors import NotFound
    e = db.get(ScannerExtractedEntry, entry_id)          # RLS: own entries only
    if e is None:
        raise NotFound("Entry not found")
    return e


def correct(e: ScannerExtractedEntry, *, author_name: str | None, text_value: str | None) -> bool:
    changed = False
    if author_name is not None and author_name.strip() != e.author_name:
        e.author_name, changed = author_name.strip()[:200], True
    if text_value is not None and text_value.strip() != e.text:
        e.text, changed = text_value.strip()[:20000], True
    if changed:
        e.edited_by_agent, e.updated_at = True, datetime.now(UTC)
    return changed


def summary(db: Session, item_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Per item: extraction status, confidence, issues and the number of entries kept."""
    from sqlalchemy import func
    if not item_ids:
        return {}
    counts = dict(db.execute(
        select(ScannerExtractedEntry.item_id, func.count())
        .where(ScannerExtractedEntry.item_id.in_(item_ids),
               ScannerExtractedEntry.duplicate_of.is_(None))
        .group_by(ScannerExtractedEntry.item_id)).all())
    out = {}
    for ex in db.scalars(select(ScannerExtraction).where(ScannerExtraction.item_id.in_(item_ids))):
        out[ex.item_id] = {"status": ex.status, "confidence": ex.confidence,
                           "issues": ex.issues or [], "context": ex.context or {},
                           "entries": int(counts.get(ex.item_id, 0)), "model": ex.model}
    return out
