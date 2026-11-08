"""Database side of a mission run: start (re-authorize, open session) and finish (apply the
result, handle challenges, retries). Each call is one short transaction in the tenant's
context, so row-level security applies to everything the worker writes."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.browser.challenges import handle_challenge, note_success
from app.browser.guard import authorize, domain_policy
from app.browser.missions import event, transition
from app.browser.runtime import ISOLATION, VisitResult
from app.collection.html import Page
from app.collection.text import content_hash, excerpt_around, find_terms
from app.collection.urls import domain_of, is_http_url
from app.db.models import BrowserMission, BrowserSession, Mention, Source
from app.db.session import session_scope, set_context

log = logging.getLogger("repintel.browser")
NO_RETRY = {"blocked_address"}


@dataclass
class MissionSpec:
    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: str
    url: str
    timeout_seconds: int
    block_resources: list[str]
    session_id: uuid.UUID


def start_mission(mission_id: uuid.UUID, tenant_id: uuid.UUID, worker_id: uuid.UUID
                  ) -> MissionSpec | None:
    """ASSIGNED → RUNNING if the access is still authorized, else FAILED/CANCELLED."""
    with session_scope() as db:
        set_context(db, tenant_id=tenant_id, user_id=None)
        m = db.get(BrowserMission, mission_id)
        if m is None or m.status != "ASSIGNED" or m.worker_id != worker_id:
            return None
        auth = authorize(db, tenant_id, m.url, count_quota=False)
        event(db, m, "validation", {**auth.record, "stage": "before_navigation"}, worker_id)
        if not auth.ok:
            # Lost authorization between queueing and running (robots changed, domain now
            # cooling down after another tenant's visit hit a challenge, connector revoked).
            transition(m, "CANCELLED" if auth.reason == "domain_cooling_down" else "FAILED",
                       error=f"not_authorized:{auth.reason}")
            _revert_mention(db, m, "snippet_only" if auth.reason == "domain_cooling_down"
                            else "page_unavailable")
            return None
        transition(m, "RUNNING")
        m.started_at = m.heartbeat_at = datetime.now(UTC)
        session = BrowserSession(id=uuid.uuid4(), tenant_id=tenant_id, mission_id=m.id,
                                 worker_id=worker_id, isolation=ISOLATION)
        db.add(session)
        pol = domain_policy(db, m.domain)
        return MissionSpec(m.id, tenant_id, m.kind, m.url, m.timeout_seconds,
                           pol.block_resources, session.id)


def _revert_mention(db: Any, m: BrowserMission, status: str) -> None:
    if m.mention_id:
        mention = db.get(Mention, m.mention_id)
        if mention is not None and mention.verification_status == "pending_render":
            mention.verification_status = status


def finish_mission(spec: MissionSpec, worker_id: uuid.UUID, res: VisitResult) -> str:
    with session_scope() as db:
        set_context(db, tenant_id=spec.tenant_id, user_id=None)
        m = db.get(BrowserMission, spec.id)
        session = db.get(BrowserSession, spec.session_id)
        if session is not None:
            session.closed_at = datetime.now(UTC)
            session.cleanup_verified = res.cleanup_verified
            session.requests_blocked = sum(res.blocked.values())
        if m is None:
            return "gone"
        event(db, m, "navigation", res.summary(), worker_id)
        if res.blocked.get("address"):
            event(db, m, "blocked_request", {"hosts": res.blocked_hosts,
                                             "count": res.blocked["address"]}, worker_id)
        event(db, m, "cleanup", {"context_closed": res.cleanup_verified}, worker_id)
        if m.status != "RUNNING":          # cancelled meanwhile: discard the result
            return m.status
        m.result = res.summary()
        source = db.get(Source, m.source_id) if m.source_id else None
        mention = db.get(Mention, m.mention_id) if m.mention_id else None
        if source is None and mention is not None and mention.source_id:
            source = db.get(Source, mention.source_id)

        if res.challenge is not None:
            handle_challenge(db, spec.tenant_id, url=res.final_url or m.url, domain=m.domain,
                             via="browser", challenge=res.challenge, mission=m, source=source,
                             mention=mention, worker_id=worker_id)
            return m.status

        if res.error is not None:
            if m.attempts < m.max_attempts and res.error not in NO_RETRY:
                transition(m, "QUEUED", error=res.error)
                m.worker_id = None
                m.run_after = datetime.now(UTC) + timedelta(seconds=60 * 2 ** m.attempts)
            else:
                transition(m, "FAILED", error=res.error)
                _revert_mention(db, m, "page_unavailable")
            return m.status

        note_success(db, m.domain, source)
        if m.kind == "render_page":
            m.result = {**m.result, **_apply_render(db, m, mention, source, res)}
        else:
            m.result = {**m.result, **_apply_source_check(db, m, source, res)}
        transition(m, "COMPLETED")
        return m.status


def _apply_render(db: Any, m: BrowserMission, mention: Mention | None, source: Source | None,
                  res: VisitResult) -> dict[str, Any]:
    from app.jobs.queue import enqueue

    if mention is None:
        return {"mention": "deleted"}
    page = Page(res.html, res.final_url or m.url)
    text = res.text or page.text
    body = f"{res.title}\n{text}"
    terms, exclude = m.params.get("terms", []), m.params.get("exclude", [])
    if exclude and find_terms(body, exclude):
        # Same rule as collection: an excluded context is not a mention of the brand.
        db.delete(mention)
        return {"mention": "excluded"}
    on_page = find_terms(body, terms)
    before = mention.original_text
    mention.verified_with = "browser"
    if on_page:
        mention.verification_status = "verified_on_page"
        mention.matched_terms = list(dict.fromkeys(list(mention.matched_terms) + on_page))
        mention.original_text = (excerpt_around(text, on_page) or mention.original_text)[:4000]
    else:
        mention.verification_status = "not_found_on_page"
    if mention.published_at is None and (d := page.published_at()) is not None:
        mention.published_at, mention.published_at_source = d, "page_metadata"
    mention.content_hash = content_hash(mention.title, mention.original_text)
    if source is not None and source.kind == "website":
        source.coverage_status, source.restrictions = "AVAILABLE", ""
    if mention.original_text != before:     # new text: analyse it again
        enqueue(db, "nlp.analyze", {"mention_ids": [str(mention.id)]},
                tenant_id=m.tenant_id, max_attempts=3)
    return {"mention": mention.verification_status, "terms_found": on_page}


def _apply_source_check(db: Any, m: BrowserMission, source: Source | None,
                        res: VisitResult) -> dict[str, Any]:
    page = Page(res.html, res.final_url or m.url)
    out: dict[str, Any] = {"feeds": page.feed_links[:5]}
    if source is None:
        return out
    source.last_checked_at = datetime.now(UTC)
    source.coverage_status, source.restrictions = "AVAILABLE", ""
    source.last_error, source.consecutive_errors = None, 0
    if not source.name and res.title:
        source.name = res.title[:200]
    created = 0
    for feed in page.feed_links[:5]:
        if not is_http_url(feed) or db.scalar(select(Source.id).where(
                Source.tenant_id == m.tenant_id, Source.kind == "rss",
                func.lower(Source.url) == feed.lower())):
            continue
        db.add(Source(tenant_id=m.tenant_id, kind="rss", url=feed, domain=domain_of(feed),
                      name=f"{source.name or source.domain} (RSS)"[:200],
                      category=source.category, language=source.language, connector="rss",
                      enabled=False, discovered_via="feed_discovery",
                      coverage_status="PARTIALLY_AVAILABLE"))
        created += 1
    out["feeds_added"] = created
    return out
