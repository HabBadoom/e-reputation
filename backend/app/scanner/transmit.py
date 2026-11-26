"""Phase 7: sending a collection to AynPulse (central space, option A).

The agent presses "Envoyer à AynPulse". The `scanner.transmit` job then, for each screenshot:
1. **attributes** it to brands — first by the brands' names, aliases and keywords (all
   organisations' active brands), then, if nothing matches and a model is configured, by asking
   the language model which of the followed brands the content is about;
2. **delivers** it to every organisation that follows a matched brand (decision 2026-11-25):
   one AynPulse mention per post, comment or reply read from it, marked `scanner_validated` or
   `scanner_unvalidated` (decision 2026-11-24), then queued for AynPulse's analysis;
3. when unsure (no match, or a model answer below the confidence threshold), leaves it **to be
   reviewed** by a platform administrator, who picks the brands or discards it.

Scanner itself still analyses nothing: matching brand names to route content is AynPulse's
work, run on the AynPulse side of the platform. Delivery is idempotent (one mention per entry,
organisation and brand), so a retried job never duplicates.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collection.text import content_hash, find_terms, normalize_for_match
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden
from app.db.models import (
    Mention,
    ScannerAccount,
    ScannerAttribution,
    ScannerCollectionItem,
    ScannerCollectionSession,
    ScannerExtractedEntry,
    ScannerExtraction,
)
from app.db.session import set_context, set_scanner_context
from app.jobs import queue
from app.social.ingest import social_duplicate_of

log = logging.getLogger("repintel.scanner.transmit")
JOB_KIND = "scanner.transmit"
CONNECTOR, CONNECTOR_VERSION = "aynpulse_scanner", "scanner-v1"


# ---- sending (agent) ---------------------------------------------------------------------
def send(db: Session, s: ScannerCollectionSession, account: ScannerAccount) -> None:
    """Close the collection and queue its transmission."""
    from app.scanner.uploads import require_open
    require_open(s)
    if account.anypulse_access != "active":
        raise Forbidden("AynPulse access was revoked", code="anypulse_access_revoked")
    items = db.scalars(select(ScannerCollectionItem)
                      .where(ScannerCollectionItem.session_id == s.id)).all()
    if not any(i.source_type == "screenshot" for i in items):
        raise Conflict("Add at least one screenshot before sending", code="nothing_to_send")
    if any(i.status == "processing" for i in items):
        raise Conflict("Some screenshots are still being read", code="still_reading")
    s.status, s.updated_at = "queued", datetime.now(UTC)
    queue.enqueue(db, JOB_KIND, {"account_id": str(s.account_id), "session_id": str(s.id)},
                  max_attempts=5)


# ---- brand catalogue ---------------------------------------------------------------------
@dataclass
class Target:
    tenant_id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    exclude: list[str]


@dataclass
class BrandGroup:
    """One brand name as followed by one or more organisations."""
    name: str
    terms: list[str]
    targets: list[Target] = field(default_factory=list)


def catalogue(db: Session) -> dict[str, BrandGroup]:
    groups: dict[str, BrandGroup] = {}
    for row in db.execute(text("SELECT * FROM scanner_brand_catalogue()")):
        key = normalize_for_match(row.name)
        if not key:
            continue
        g = groups.setdefault(key, BrandGroup(row.name, []))
        for t in [row.name, *(row.aliases or []), *(row.include_terms or [])]:
            if t and t.strip() and t not in g.terms:
                g.terms.append(t)
        g.targets.append(Target(row.tenant_id, row.brand_id, row.name,
                                list(row.exclude_terms or [])))
    return groups


# ---- attribution -------------------------------------------------------------------------
@dataclass
class Content:
    item: ScannerCollectionItem
    entries: list[ScannerExtractedEntry]
    context: dict
    validated: bool
    captured_at: datetime | None

    @property
    def text(self) -> str:
        parts = [self.context.get("page_or_group", ""), self.context.get("dialog_title", "")]
        for e in self.entries:
            parts += [e.author_name, e.text, e.image_text]
        return "\n".join(p for p in parts if p)


def _content(db: Session, item: ScannerCollectionItem) -> Content:
    ex = db.get(ScannerExtraction, item.id)
    entries = list(db.scalars(select(ScannerExtractedEntry).where(
        ScannerExtractedEntry.item_id == item.id, ScannerExtractedEntry.duplicate_of.is_(None))
        .order_by(ScannerExtractedEntry.position)))
    entries = [e for e in entries if (e.text or e.image_text).strip()]
    return Content(item, entries, (ex.context if ex else {}) or {},
                   bool(ex and ex.validated_at), ex.captured_at if ex else None)


ATTRIBUTION_SYSTEM = """You route social-media content collected in Algeria to the brands it is
about. You get the text read from one screenshot (page or group name, post, comments) and the
list of brands followed on the platform. Answer with the brands the content is clearly about,
using names exactly as listed; an empty list if none. Do not judge tone or sentiment.
confidence: from 0 to 1, how sure you are."""

ATTRIBUTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["brands", "confidence", "reason"],
    "properties": {"brands": {"type": "array", "items": {"type": "string"}},
                   "confidence": {"type": "number"},
                   "reason": {"type": "string"}},
}


@dataclass
class Decision:
    status: str                      # delivered | to_review | empty
    method: str | None
    groups: list[BrandGroup]
    confidence: float | None = None
    reason: str = ""


def attribute(c: Content, groups: dict[str, BrandGroup]) -> Decision:
    if not c.entries:
        return Decision("empty", None, [], reason="no text read from this screenshot")
    body = c.text
    matched = [g for g in groups.values() if find_terms(body, g.terms)]
    if matched:
        return Decision("delivered", "keywords", matched, 1.0,
                        "names found: " + ", ".join(g.name for g in matched))
    cfg = get_settings()
    if not (cfg.scanner_attribution_model and groups):
        return Decision("to_review", None, [], reason="no followed brand named in the content")
    from app.nlp.llm import LLMError, get_provider
    names = sorted({g.name for g in groups.values()})[:400]
    user = ("Brands followed:\n" + "\n".join(f"- {n}" for n in names)
            + "\n\nContent:\n" + body[:12000])
    try:
        res = get_provider().complete_json(model=cfg.scanner_attribution_model,
                                           system=ATTRIBUTION_SYSTEM, user=user,
                                           schema=ATTRIBUTION_SCHEMA,
                                           schema_name="brand_routing")
    except LLMError as e:
        if not e.fatal:
            raise
        return Decision("to_review", None, [], reason=f"model unavailable: {e.reason}")
    by_name = {normalize_for_match(g.name): g for g in groups.values()}
    chosen = [by_name[k] for k in dict.fromkeys(
        normalize_for_match(n) for n in res.data.get("brands", []) if isinstance(n, str))
        if k in by_name]
    conf = res.data.get("confidence")
    conf = round(min(1.0, max(0.0, float(conf))), 2) if isinstance(conf, int | float) else 0.0
    reason = str(res.data.get("reason", ""))[:500]
    if chosen and conf >= cfg.scanner_attribution_min_confidence:
        return Decision("delivered", "ai", chosen, conf, reason)
    return Decision("to_review", "ai" if chosen else None, chosen, conf,
                    reason or "the model was not sure")


# ---- delivery ----------------------------------------------------------------------------
def _domain(item: ScannerCollectionItem) -> str:
    host = urlsplit(item.source_url or "").hostname or ""
    if host:
        return host.removeprefix("www.").removeprefix("m.").removeprefix("web.")[:255]
    return {"facebook": "facebook.com", "instagram": "instagram.com", "tiktok": "tiktok.com",
            "youtube": "youtube.com", "linkedin": "linkedin.com", "x": "x.com"}.get(
        item.platform or "", "scanner.aynpulse")


def deliver(db: Session, c: Content, groups: list[BrandGroup], *,
            account_id: uuid.UUID) -> tuple[int, int, dict[uuid.UUID, list[uuid.UUID]]]:
    """Create the mentions in each organisation following the brands (in its own tenant
    context). Returns (mentions created, organisations, {tenant: [mention ids]})."""
    item = c.item
    verification = "scanner_validated" if c.validated else "scanner_unvalidated"
    url = item.source_url or f"https://scanner.aynpulse/collection/{item.session_id}"
    created: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    tenants: set[uuid.UUID] = set()
    now = datetime.now(UTC)
    for g in groups:
        for t in g.targets:
            if t.exclude and find_terms(c.text, t.exclude):
                continue
            tenants.add(t.tenant_id)
            set_context(db, tenant_id=t.tenant_id, user_id=None)
            for e in c.entries:
                body = e.text or e.image_text
                title = (f"{e.author_name} — {item.platform or 'web'}" if e.kind == "post"
                         else f"{'Réponse' if e.kind == 'reply' else 'Commentaire'} de "
                              f"{e.author_name or '?'}")[:2000]
                h = content_hash(title, body)
                dup = social_duplicate_of(db, t.tenant_id, t.brand_id,
                                          url=item.source_url or "", kind=e.kind, body=body)
                m = Mention(
                    tenant_id=t.tenant_id, brand_id=t.brand_id, entity_type="brand",
                    entity_name=t.name[:200], url=url[:2000],
                    canonical_url=f"scanner:entry:{e.id}", domain=_domain(item), title=title,
                    snippet=body[:4000], original_text=body[:4000],
                    matched_terms=find_terms(body, g.terms) or [],
                    publisher=(e.author_name or None) and e.author_name[:200],
                    published_at=e.published_at,
                    published_at_source="scanner_time_label" if e.published_at else None,
                    collected_at=c.captured_at or now, last_seen_at=now,
                    collection_method="scanner", connector=CONNECTOR,
                    connector_version=CONNECTOR_VERSION, verification_status=verification,
                    content_hash=h, duplicate_of=dup,
                    raw={"scanner": {
                        "collection_id": str(item.session_id), "item_id": str(item.id),
                        "entry_id": str(e.id), "kind": e.kind,
                        "parent_entry_id": str(e.parent_id) if e.parent_id else None,
                        "author": e.author_name, "author_is_page": e.author_is_page,
                        "image_text": e.image_text, "platform": item.platform,
                        "page_or_group": c.context.get("page_or_group", ""),
                        "relative_time": e.relative_time,
                        "published_precision": e.published_precision,
                        "reactions": e.reactions_count, "comments": e.comments_count,
                        "shares": e.shares_count, "validated": c.validated,
                        "edited_by_agent": e.edited_by_agent,
                        "link_item_id": str(item.link_item_id) if item.link_item_id else None,
                        "agent_account_id": str(account_id)}},
                )
                try:
                    with db.begin_nested():
                        db.add(m)
                except IntegrityError:          # already delivered (retried job)
                    continue
                created[t.tenant_id].append(m.id)
    for tid, ids in created.items():
        set_context(db, tenant_id=tid, user_id=None)
        for i in range(0, len(ids), 50):
            queue.enqueue(db, "nlp.analyze", {"mention_ids": [str(x) for x in ids[i:i + 50]]},
                          tenant_id=tid)
    return sum(len(v) for v in created.values()), len(tenants), created


# ---- the job -----------------------------------------------------------------------------
def run(db: Session, account_id: uuid.UUID, session_id: uuid.UUID) -> str:
    set_scanner_context(db, account_id=account_id)
    s = db.get(ScannerCollectionSession, session_id)
    if s is None or s.status not in ("queued", "transmitting", "retrying"):
        return "skipped"
    account = db.get(ScannerAccount, account_id)
    if account is None or account.anypulse_access != "active":
        s.status, s.updated_at = "failed", datetime.now(UTC)
        return "access_revoked"
    s.status = "transmitting"
    item_ids = list(db.scalars(select(ScannerCollectionItem.id).where(
        ScannerCollectionItem.session_id == session_id,
        ScannerCollectionItem.source_type == "screenshot")
        .order_by(ScannerCollectionItem.position)))
    set_context(db, tenant_id=None, user_id=None)
    groups = catalogue(db)
    for item_id in item_ids:
        set_scanner_context(db, account_id=account_id)
        att = db.get(ScannerAttribution, item_id)
        if att is not None and att.status != "pending":
            continue                         # done in an earlier attempt
        item = db.get(ScannerCollectionItem, item_id)
        assert item is not None
        c = _content(db, item)
        decision = attribute(c, groups)
        created = orgs = 0
        if decision.status == "delivered":
            created, orgs, _ = deliver(db, c, decision.groups, account_id=account_id)
            set_scanner_context(db, account_id=account_id)
        att = db.get(ScannerAttribution, item_id) or ScannerAttribution(
            item_id=item_id, account_id=account_id, session_id=session_id)
        att.status, att.method = decision.status, decision.method
        att.brand_names = [g.name for g in decision.groups]
        att.confidence, att.reason = decision.confidence, decision.reason[:500]
        att.mentions_created, att.organisations = created, orgs
        att.updated_at = datetime.now(UTC)
        db.add(att)
        db.flush()
    set_scanner_context(db, account_id=account_id)
    s = db.get(ScannerCollectionSession, session_id)
    assert s is not None
    now = datetime.now(UTC)
    s.status, s.transmitted_at, s.updated_at = "transmitted", now, now
    from app.scanner import retention
    retention.set_after_sending(db, session_id, now)
    from app.scanner.audit import record
    record(db, account_id=account_id, action="collection.transmitted",
           target_type="collection_session", target_id=session_id,
           meta={"screenshots": len(item_ids)})
    return "transmitted"


def fail(db: Session, account_id: uuid.UUID, session_id: uuid.UUID) -> None:
    """Last attempt failed: the collection shows "failed" and can be sent again."""
    set_scanner_context(db, account_id=account_id)
    s = db.get(ScannerCollectionSession, session_id)
    if s is not None and s.status in ("queued", "transmitting", "retrying"):
        s.status, s.updated_at = "failed", datetime.now(UTC)


def resend(db: Session, s: ScannerCollectionSession) -> None:
    if s.status != "failed":
        raise Conflict("Only a failed sending can be retried", code="not_failed")
    s.status, s.updated_at = "retrying", datetime.now(UTC)
    queue.enqueue(db, JOB_KIND, {"account_id": str(s.account_id), "session_id": str(s.id)},
                  max_attempts=5)

