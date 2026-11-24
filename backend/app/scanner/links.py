"""Phase 5: links pasted by the agent (a post received on WhatsApp, a comment link…).

A link becomes a `url` item of the collection and opens in the built-in browser; the captures
taken from it point to it (`link_item_id`). Scanner never fetches or scrapes the page itself:
the agent captures what the page shows. The same link (after removing tracking parameters and
mobile host variants) is kept once per collection.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.models import ScannerCollectionItem, ScannerCollectionSession
from app.scanner.platforms import platform_of
from app.scanner.uploads import _item_count, _next_position, require_open

# Query parameters that only track who shared or clicked; they change nothing on the page.
_TRACKING = {"fbclid", "mibextid", "rdid", "share_url", "sfnsn", "ref", "refsrc", "_rdr",
             "_rdc", "__cft__[0]", "__tn__", "igsh", "igshid", "si", "feature", "s",
             "app", "locale", "notif_id", "notif_t", "comment_tracking", "eav", "paipv",
             "acontext", "hoisted_section_header_type"}
_MOBILE = ("m.", "mobile.", "mbasic.", "web.", "www.", "touch.", "lm.", "l.")


class BadLink(AppError):
    def __init__(self, message: str = "This is not a web address (http or https)"):
        super().__init__(message, code="bad_url", status_code=422)


def clean(raw: str) -> str:
    """The address to open: trimmed, scheme added, fragment and tracking parameters removed."""
    raw = (raw or "").strip().strip("<>\"'")
    if not raw:
        raise BadLink()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        p = urlsplit(raw)
        port = p.port
    except ValueError as e:
        raise BadLink() from e
    if p.scheme.lower() not in ("http", "https") or not p.hostname or "." not in p.hostname:
        raise BadLink()
    if p.username or p.password:
        raise BadLink("Addresses with a user name or password are not accepted")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in _TRACKING and not k.lower().startswith(("utm_", "__xts__"))]
    host = p.hostname.lower() + (f":{port}" if port else "")
    url = urlunsplit((p.scheme.lower(), host, p.path or "/", urlencode(query), ""))
    if len(url) > 2000:
        raise BadLink("This address is too long")
    return url


def key_of(url: str) -> str:
    """Same page, same key: mobile/www host variants and trailing slashes do not matter."""
    p = urlsplit(url)
    host = p.hostname or ""
    for prefix in _MOBILE:
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix):]
            break
    query = urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))
    return hashlib.sha256(f"{host}{p.path.rstrip('/') or '/'}?{query}".encode()).hexdigest()


def add(db: Session, s: ScannerCollectionSession, raw: str
        ) -> tuple[ScannerCollectionItem, bool]:
    """Record the link in the collection (or return the one already there)."""
    require_open(s)
    url = clean(raw)
    key = key_of(url)
    existing = db.scalar(select(ScannerCollectionItem).where(
        ScannerCollectionItem.session_id == s.id, ScannerCollectionItem.source_type == "url",
        ScannerCollectionItem.url_key == key))
    if existing is not None:
        return existing, False
    if _item_count(db, s.id) >= get_settings().scanner_max_items_per_session:
        raise AppError("The collection is full", code="collection_full", status_code=409)
    platform = platform_of(url)
    item = ScannerCollectionItem(
        id=uuid.uuid4(), account_id=s.account_id, session_id=s.id, source_type="url",
        position=_next_position(db, s.id), status="pending", source_url=url, url_key=key,
        platform=platform)
    sp = db.begin_nested()
    try:
        db.add(item)
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()              # pasted twice at the same time: keep the first
        existing = db.scalar(select(ScannerCollectionItem).where(
            ScannerCollectionItem.session_id == s.id, ScannerCollectionItem.url_key == key))
        if existing is None:
            raise
        return existing, False
    if s.status == "draft":
        s.status = "collecting"
    s.updated_at = datetime.now(UTC)
    return item, True


def get_link(db: Session, item_id: uuid.UUID, session_id: uuid.UUID) -> ScannerCollectionItem:
    item = db.get(ScannerCollectionItem, item_id)            # RLS: own items only
    if item is None or item.source_type != "url" or item.session_id != session_id:
        raise NotFound("Link not found in this collection", code="link_not_found")
    return item


def capture_counts(db: Session, link_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not link_ids:
        return {}
    i = ScannerCollectionItem
    return dict(db.execute(select(i.link_item_id, func.count()).where(
        i.link_item_id.in_(link_ids)).group_by(i.link_item_id)).all())
