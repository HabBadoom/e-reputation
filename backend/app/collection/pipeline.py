"""Collection pipeline for one monitoring run (master prompt §11).

discovery (search) → permission check → collection → validation → exclusion → verification
on the real page → dedup → provenance → source registry update.

Language/sentiment analysis (steps 7, 11, 12) arrives in Milestones 4–5; nothing here infers
meaning. Nothing is invented: unknown dates stay NULL, unverified hits are labelled as such.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collection.robots import RobotsCache
from app.collection.text import content_hash, excerpt_around, find_terms
from app.collection.urls import canonicalize, domain_of, is_http_url
from app.connectors.base import ConnectorError, RawItem
from app.connectors.rss import RssConnector
from app.connectors.serper import HL, SerperConnector
from app.connectors.web import BlockedByRobots, ChallengeDetected, WebPageConnector
from app.core.config import get_settings
from app.db.models import Mention, MonitoringProject, MonitoringRun, Source
from app.modules.monitoring.query_plan import QueryGroup, load_query_plan

log = logging.getLogger("repintel.collection")


@dataclass
class Candidate:
    group: QueryGroup
    item: RawItem
    connector: str
    connector_version: str
    source_id: uuid.UUID | None = None


@dataclass
class Stats:
    connectors: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidates: int = 0
    new_mentions: int = 0
    already_known: int = 0
    reposts: int = 0
    excluded: int = 0
    unconfirmed: int = 0
    invalid: int = 0
    verified: int = 0
    page_fetches: int = 0
    challenges: int = 0
    render_queued: int = 0
    render_refused: int = 0
    cooldown_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        if len(self.errors) < 20:
            self.errors.append(msg[:300])


def _tbs(project: MonitoringProject) -> str:
    if project.last_run_at is None:
        return "qdr:m"            # first run: last month
    return "qdr:d" if project.frequency_minutes <= 1440 else "qdr:w"


# ---------------------------------------------------------------- discovery / collection
def _search_candidates(db: Session, project: MonitoringProject, plan: list[QueryGroup],
                       stats: Stats) -> list[Candidate]:
    s = get_settings()
    conn = SerperConnector()
    info: dict[str, Any] = {"queries": 0, "results": 0}
    stats.connectors["serper"] = info
    if not conn.configured:
        info["status"] = "not_configured"
        return []
    perm = conn.check_permissions(db)
    info["review_status"] = perm.review_status
    if not perm.allowed:
        info["status"] = "blocked_by_policy"
        info["reason"] = perm.reason
        return []
    if perm.reason:
        info["note"] = perm.reason

    hls = list(dict.fromkeys(HL[lang] for lang in project.languages if lang in HL)) or [None]
    out: list[Candidate] = []
    budget = s.max_search_queries_per_run
    tbs = _tbs(project)
    for group in plan:
        query = group.as_search_string()
        if not query:
            continue
        for hl in hls:
            for endpoint in s.search_endpoints:
                if info["queries"] >= budget:
                    info["status"] = "ok"
                    info["truncated"] = True
                    return out
                info["queries"] += 1
                try:
                    items = conn.search(query, hl=hl, endpoint=endpoint, tbs=tbs)
                except ConnectorError as e:
                    stats.error(f"serper: {e}")
                    if e.fatal:
                        info["status"] = "error"
                        info["reason"] = e.reason
                        return out
                    continue
                info["results"] += len(items)
                out += [Candidate(group, it, conn.key, conn.version) for it in items]
    info["status"] = "ok"
    return out


def _feed_candidates(db: Session, project: MonitoringProject, plan: list[QueryGroup],
                     stats: Stats) -> list[Candidate]:
    conn = RssConnector()
    feeds = list(db.scalars(select(Source).where(Source.tenant_id == project.tenant_id,
                                                 Source.kind == "rss", Source.enabled.is_(True))))
    info: dict[str, Any] = {"sources": len(feeds), "items": 0, "failed_sources": 0}
    stats.connectors["rss"] = info
    if not feeds:
        info["status"] = "no_sources"
        return []
    perm = conn.check_permissions(db)
    info["review_status"] = perm.review_status
    if not perm.allowed:
        info["status"] = "blocked_by_policy"
        info["reason"] = perm.reason
        return []

    out: list[Candidate] = []
    now = datetime.now(UTC)
    for src in feeds:
        try:
            items = conn.collect(src.url)
        except ConnectorError as e:
            info["failed_sources"] += 1
            stats.error(f"rss {src.domain}: {e}")
            src.last_error = str(e)[:500]
            src.consecutive_errors += 1
            src.last_checked_at = now
            if src.consecutive_errors >= 3:
                src.coverage_status = "CONNECTOR_ERROR"
            continue
        src.last_error, src.consecutive_errors = None, 0
        src.last_checked_at = now
        src.coverage_status = "AVAILABLE"
        info["items"] += len(items)
        for it in items:
            text = f"{it.title}\n{it.snippet}"
            for group in plan:
                if find_terms(text, group.terms):
                    out.append(Candidate(group, it, conn.key, conn.version, source_id=src.id))
                    break     # first matching entity wins (brand before products/competitors)
    info["status"] = "ok"
    return out


# ---------------------------------------------------------------- registry
class SourceRegistry:
    """Website sources discovered through search results, one per domain per tenant."""

    def __init__(self, db: Session, tenant_id: uuid.UUID) -> None:
        self.db, self.tenant_id = db, tenant_id
        self._by_domain: dict[str, Source] = {}

    def website(self, domain: str) -> Source:
        if domain in self._by_domain:
            return self._by_domain[domain]
        src = self.db.scalars(select(Source).where(
            Source.tenant_id == self.tenant_id, Source.kind == "website",
            Source.domain == domain)).first()
        if src is None:
            src = Source(tenant_id=self.tenant_id, kind="website", url=f"https://{domain}/",
                         domain=domain, name=domain, connector="web", discovered_via="search",
                         coverage_status="PARTIALLY_AVAILABLE")
            self.db.add(src)
            self.db.flush()
        self._by_domain[domain] = src
        return src


# ---------------------------------------------------------------- browser hand-off
@dataclass
class BrowserGate:
    """Whether pages may be handed to the browser fleet during this run, and how many."""
    allowed: bool
    budget: int
    cooling: dict[str, bool] = field(default_factory=dict)

    def cooling_down(self, db: Session, domain: str) -> bool:
        if domain not in self.cooling:
            from app.browser.guard import cooldown_until
            self.cooling[domain] = cooldown_until(db, domain) is not None
        return self.cooling[domain]

    def take(self) -> bool:
        if self.allowed and self.budget > 0:
            self.budget -= 1
            return True
        return False


# ---------------------------------------------------------------- processing
def _process(db: Session, project: MonitoringProject, run: MonitoringRun, c: Candidate,
             web: WebPageConnector, registry: SourceRegistry, stats: Stats,
             seen: set[str], page_budget: list[int], gate: BrowserGate) -> None:
    s = get_settings()
    it = c.item
    if not is_http_url(it.url):
        stats.invalid += 1
        return
    canonical = canonicalize(it.url)
    if canonical in seen:                     # same URL from several queries in this run
        return
    seen.add(canonical)
    domain = domain_of(it.url)
    terms, exclude = c.group.terms, c.group.exclude
    listing_text = f"{it.title}\n{it.snippet}"

    if exclude and find_terms(listing_text, exclude):
        stats.excluded += 1
        return

    now = datetime.now(UTC)
    existing = db.scalars(select(Mention).where(
        Mention.tenant_id == project.tenant_id, Mention.brand_id == project.brand_id,
        Mention.canonical_url == canonical)).one_or_none()
    if existing is not None:              # already collected: record the sighting only
        existing.last_seen_at = now
        existing.seen_count += 1
        stats.already_known += 1
        return

    source = db.get(Source, c.source_id) if c.source_id else registry.website(domain)
    matched = find_terms(listing_text, terms)
    original, published, published_how = it.snippet, it.published_at, it.published_how

    challenge = None
    render, fallback = False, "snippet_only"
    if it.method == "rss":
        verification = "feed_item"
        original = excerpt_around(it.snippet, matched) if it.snippet else it.title
    elif s.verify_pages and gate.cooling_down(db, domain):
        # The site challenged our crawler recently: don't knock again, keep the snippet.
        verification = "snippet_only"
        stats.cooldown_skipped += 1
    elif s.verify_pages and source.render_mode == "browser" and gate.take():
        verification, render = "pending_render", True
    elif s.verify_pages and page_budget[0] > 0:
        page_budget[0] -= 1
        stats.page_fetches += 1
        try:
            page = web.fetch_page(it.url)
        except ChallengeDetected as e:
            verification, challenge = "blocked_by_challenge", e.challenge
            gate.cooling[domain] = True
            stats.challenges += 1
        except BlockedByRobots:
            verification = "blocked_by_robots"
            if source.kind == "website":
                source.coverage_status = "RESTRICTED"
                source.restrictions = "robots.txt disallows our crawler"
        except ConnectorError as e:
            verification = "page_unavailable"
            stats.error(f"page {domain}: {e.reason}")
        else:
            body = f"{page.title}\n{page.text}"
            if exclude and find_terms(body, exclude):
                stats.excluded += 1
                return
            on_page = find_terms(body, terms)
            if on_page:
                verification = "verified_on_page"
                matched = list(dict.fromkeys(matched + on_page))
                original = excerpt_around(page.text, on_page) or original
                stats.verified += 1
            else:
                verification = "not_found_on_page"
                # Text produced by JavaScript is invisible to a plain fetch: render it.
                if page.needs_javascript and source.render_mode != "static" and gate.take():
                    verification, render, fallback = "pending_render", True, "not_found_on_page"
            if (d := page.published_at()) is not None:
                published, published_how = d, "page_metadata"
            if source.kind == "website":
                source.coverage_status = "AVAILABLE"
                source.restrictions = ""
    else:
        verification = "snippet_only"

    if not matched and verification in ("snippet_only", "not_found_on_page", "page_unavailable",
                                        "blocked_by_robots", "blocked_by_challenge",
                                        "pending_render"):
        # Neither the listing nor the page shows the brand: keep it, but labelled, because
        # the provider matched it for a reason we could not see (e.g. JavaScript content).
        stats.unconfirmed += 1

    h = content_hash(it.title, original)
    repost_of = db.scalar(select(Mention.id).where(
        Mention.tenant_id == project.tenant_id, Mention.brand_id == project.brand_id,
        Mention.content_hash == h, Mention.duplicate_of.is_(None)).limit(1))
    m = Mention(
        tenant_id=project.tenant_id, brand_id=project.brand_id, project_id=project.id,
        source_id=source.id, first_run_id=run.id, entity_type=c.group.entity_type,
        entity_name=c.group.entity_name, url=it.url, canonical_url=canonical, domain=domain,
        title=it.title[:2000], snippet=it.snippet[:4000], original_text=original[:4000],
        matched_terms=matched, publisher=(it.publisher or None) and it.publisher[:200],
        published_at=published, published_at_source=published_how if published else None,
        collected_at=now, last_seen_at=now, collection_method=it.method,
        connector=c.connector, connector_version=c.connector_version,
        verification_status=verification, content_hash=h, duplicate_of=repost_of,
        raw=it.raw | ({"published_raw": it.published_raw} if it.published_raw else {}),
    )
    try:
        with db.begin_nested():
            db.add(m)
    except IntegrityError:          # concurrent run inserted the same URL
        stats.already_known += 1
        return
    stats.new_mentions += 1
    if repost_of:
        stats.reposts += 1
    source.mentions_count += 1
    source.last_collected_at = now

    if challenge is not None:
        from app.browser.challenges import handle_challenge
        handle_challenge(db, project.tenant_id, url=it.url, domain=domain, via="http",
                         challenge=challenge, source=source, mention=m)
    if render:
        from app.browser.missions import create_mission
        mission = create_mission(db, project.tenant_id, "render_page", it.url, mention_id=m.id,
                                 source_id=None, priority=6, robots=web.robots,
                                 params={"terms": terms, "exclude": exclude})
        if mission.status == "QUEUED":
            stats.render_queued += 1
        else:
            m.verification_status = fallback
            stats.render_refused += 1


def collect_for_project(db: Session, project: MonitoringProject, run: MonitoringRun
                        ) -> tuple[str, dict[str, Any]]:
    """Run collection; return (run status, summary)."""
    s = get_settings()
    plan = load_query_plan(db, project)
    stats = Stats()
    candidates = _search_candidates(db, project, plan, stats)
    candidates += _feed_candidates(db, project, plan, stats)
    stats.candidates = len(candidates)

    web = WebPageConnector(RobotsCache())
    web_perm = web.check_permissions(db)
    if not s.verify_pages:
        verification_mode = "disabled"
    elif not web_perm.allowed:
        verification_mode = "blocked_by_policy"
    else:
        verification_mode = "enabled"
    registry = SourceRegistry(db, project.tenant_id)
    from app.connectors.browser import BrowserConnector
    gate = BrowserGate(allowed=(verification_mode == "enabled" and s.browser_enabled
                                and BrowserConnector().check_permissions(db).allowed),
                       budget=s.browser_missions_per_run)
    seen: set[str] = set()
    budget = [s.max_page_fetches_per_run if verification_mode == "enabled" else 0]
    for c in candidates:
        try:
            _process(db, project, run, c, web, registry, stats, seen, budget, gate)
        except Exception as e:  # noqa: BLE001 — one bad item must not sink the run
            log.exception("candidate_failed", extra={"url": c.item.url})
            stats.error(f"{type(e).__name__} on {c.item.url[:120]}")

    ran = [k for k, v in stats.connectors.items() if v.get("status") == "ok"]
    summary: dict[str, Any] = {
        "query_groups": len(plan),
        "search_terms": sum(len(g.terms) for g in plan),
        "connectors": stats.connectors,
        "candidates": stats.candidates,
        "mentions_collected": stats.new_mentions,
        "already_known": stats.already_known,
        "reposts": stats.reposts,
        "excluded": stats.excluded,
        "unconfirmed": stats.unconfirmed,
        "verified_on_page": stats.verified,
        "page_fetches": stats.page_fetches,
        "page_verification": verification_mode,
        "browser": {"enabled": gate.allowed, "render_queued": stats.render_queued,
                    "render_refused": stats.render_refused},
        "challenges": stats.challenges,
        "cooldown_skipped": stats.cooldown_skipped,
        "invalid": stats.invalid,
        "errors": stats.errors,
    }
    if not ran:
        status_of = [v.get("status") for v in stats.connectors.values()]
        if "error" in status_of:
            summary["reason"] = "connectors_failed"
            return "failed", summary
        summary["reason"] = ("connectors_blocked_by_policy" if "blocked_by_policy" in status_of
                             else "no_active_connectors")
        return "skipped", summary
    return "completed", summary

