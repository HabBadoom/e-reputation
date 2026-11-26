"""Synchronising social sources: jobs `social.sync` (start) and `social.poll` (Apify runs).

Order of checks for every sync: source enabled → project active → subscription active →
connector allowed (compliance record) → provider configured → monthly budget for the
platform. A refused sync is recorded as `skipped` with the reason, never silently dropped.

Apify runs are asynchronous: the start job launches the actor with `maxItems` and a cost
cap, then `social.poll` checks the run until it ends and reads its dataset. Facebook posts are
followed by a comments run on posts old enough to have comments (`APIFY_COMMENTS_DELAY_HOURS`).
YouTube is synchronous (official API).

Provider errors never raise out of the job (the sync row would be rolled back): they are
recorded on the sync and on the source.
"""

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.connectors import apify as apify_mod
from app.connectors.apify import ApifyConnector
from app.connectors.base import ConnectorError
from app.connectors.social import SocialItem, to_datetime
from app.connectors.youtube import YouTubeConnector
from app.core.config import get_settings
from app.db.models import MonitoringProject, Source, SourceSync
from app.jobs import queue
from app.modules.monitoring.query_plan import load_query_plan
from app.social.ingest import Ingestor, Provenance

log = logging.getLogger("repintel.social")

YOUTUBE_KINDS = ("youtube_channel", "youtube_search")
SOCIAL_KINDS = tuple(apify_mod.KINDS) + YOUTUBE_KINDS
COMMENT_KINDS = ("facebook_page", "facebook_group")
PLAN_KEY = {"facebook_posts": "max_facebook_posts_per_month",
            "facebook_comments": "max_facebook_comments_per_month",
            "tiktok_results": "max_tiktok_results_per_month",
            "reddit_results": "max_reddit_results_per_month",
            "linkedin_results": "max_linkedin_results_per_month",
            "youtube_results": "max_youtube_results_per_month"}
COMMENTS_DELAY_HOURS = 12
PENDING_MAX = 200
PENDING_MAX_AGE_DAYS = 7
DONE_MAX = 1000


def metric_of(kind: str) -> str:
    return "youtube_results" if kind in YOUTUBE_KINDS else apify_mod.KINDS[kind][1]


def platform_of(kind: str) -> str:
    return "youtube" if kind in YOUTUBE_KINDS else apify_mod.KINDS[kind][2]


# ------------------------------------------------------------------------------ budget
def _month_minutes_left(now: datetime) -> float:
    nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0,
                                                              microsecond=0)
    return max(60.0, (nxt - now).total_seconds() / 60)


def run_budget(db: Session, src: Source, metric: str, per_run_max: int, *,
               manual: bool, kinds: tuple[str, ...] | None = None) -> int:
    """How many results this run may request. Scheduled runs share what is left of the
    monthly cap between the sources of the platform and the runs left in the month, so the
    cap is not spent on the first day; a manual run may use up to `per_run_max`."""
    from app.modules.billing.entitlements import remaining_this_month
    left = remaining_this_month(db, src.tenant_id, PLAN_KEY[metric])
    if left is None:
        return per_run_max
    if left <= 0:
        return 0
    if manual:
        return min(per_run_max, left)
    every = src.frequency_minutes or get_settings().social_default_frequency_minutes
    runs_left = max(1.0, _month_minutes_left(datetime.now(UTC)) / every)
    same = kinds or tuple(k for k in SOCIAL_KINDS if metric_of(k) == metric)
    n = db.scalar(select(func.count()).select_from(Source).where(
        Source.tenant_id == src.tenant_id, Source.enabled.is_(True),
        Source.kind.in_(same))) or 1
    share = math.ceil(left / runs_left / max(1, n))
    return max(1, min(per_run_max, left, share))


# ------------------------------------------------------------------------------ helpers
def _finish(src: Source, sync: SourceSync, status: str, *, category: str | None = None,
            error: str | None = None) -> None:
    now = datetime.now(UTC)
    sync.status, sync.finished_at = status, now
    sync.error_category, sync.error = category, (error or None) and error[:500]
    if status in ("succeeded", "partial"):
        src.last_synced_at = now
        src.consecutive_errors = 0
        src.last_error = None if status == "succeeded" else sync.error
        src.coverage_status = "AVAILABLE" if status == "succeeded" else "PARTIALLY_AVAILABLE"
    elif status == "failed":
        src.consecutive_errors += 1
        src.last_error = f"{category}: {error}"[:500] if error else category
        if src.consecutive_errors >= 3 or category in ("auth", "not_found", "input"):
            src.coverage_status = "CONNECTOR_ERROR"
    elif status == "skipped" and category in ("not_authorized", "not_configured"):
        src.coverage_status = "PENDING_AUTHORIZATION"
        src.last_error = f"{category}: {error}"[:500] if error else category


def _skip(db: Session, src: Source, provider: str, trigger: str, category: str, why: str,
          *, stage: str = "items", parent: uuid.UUID | None = None) -> SourceSync:
    sync = SourceSync(id=uuid.uuid4(), tenant_id=src.tenant_id, source_id=src.id,
                      provider=provider, stage=stage, trigger=trigger, parent_sync_id=parent,
                      started_at=datetime.now(UTC))
    db.add(sync)
    _finish(src, sync, "skipped", category=category, error=why)
    db.flush()
    return sync


def _since(src: Source) -> datetime | None:
    return to_datetime((src.sync_state or {}).get("newest_published"))


def _remember_newest(src: Source, items: list[SocialItem]) -> None:
    dates = [i.published_at for i in items if i.published_at]
    if not dates:
        return
    newest = max(dates)
    old = _since(src)
    if old is None or newest > old:
        src.sync_state = {**(src.sync_state or {}), "newest_published": newest.isoformat()}


def _queue_pending(src: Source, posts: list[Any]) -> None:
    """Remember kept posts whose comments will be fetched once they are old enough."""
    state = dict(src.sync_state or {})
    pending = list(state.get("pending_comments") or [])
    known = {p["external_id"] for p in pending} | set(state.get("comments_done") or [])
    for m in posts:
        n = (m.engagement or {}).get("comments")
        if m.external_id in known or n == 0 or not m.url.startswith("http"):
            continue
        pending.append({"external_id": m.external_id, "url": m.url,
                        "published": m.published_at.isoformat() if m.published_at else None,
                        "queued": datetime.now(UTC).isoformat()})
    state["pending_comments"] = pending[-PENDING_MAX:]
    src.sync_state = state


def _take_mature(src: Source, limit: int) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    state = dict(src.sync_state or {})
    keep, ready = [], []
    for p in state.get("pending_comments") or []:
        queued = to_datetime(p.get("queued")) or now
        if now - queued > timedelta(days=PENDING_MAX_AGE_DAYS):
            continue                                   # too old: give up on its comments
        ref = to_datetime(p.get("published")) or queued
        if now - ref >= timedelta(hours=COMMENTS_DELAY_HOURS) and len(ready) < limit:
            ready.append(p)
        else:
            keep.append(p)
    state["pending_comments"] = keep
    done = list(state.get("comments_done") or []) + [p["external_id"] for p in ready]
    state["comments_done"] = done[-DONE_MAX:]      # comments are fetched once per post
    src.sync_state = state
    return ready


def _context(db: Session, src: Source) -> tuple[MonitoringProject | None, str | None]:
    if not src.enabled:
        return None, "source disabled"
    project = db.get(MonitoringProject, src.project_id) if src.project_id else None
    if project is None:
        return None, "no monitoring project"
    if project.status != "active":
        return None, "monitoring project paused"
    return project, None


# ------------------------------------------------------------------------------ entry
def start(db: Session, source_id: uuid.UUID, *, trigger: str = "schedule") -> SourceSync | None:
    from app.modules.billing.entitlements import plan_info
    src = db.get(Source, source_id)
    if src is None or src.kind not in SOCIAL_KINDS:
        return None
    provider = "youtube" if src.kind in YOUTUBE_KINDS else "apify"
    project, why = _context(db, src)
    if project is None:
        return _skip(db, src, provider, trigger, "input", why or "")
    if not plan_info(db, src.tenant_id).active:
        return _skip(db, src, provider, trigger, "subscription", "subscription not active")
    conn = YouTubeConnector() if provider == "youtube" else ApifyConnector()
    perm = conn.check_permissions(db)
    if not perm.allowed:
        return _skip(db, src, provider, trigger, "not_authorized", perm.reason)
    if not conn.configured:
        return _skip(db, src, provider, trigger, "not_configured",
                     f"{provider} is not configured on this server")
    if provider == "youtube":
        return _youtube(db, src, project, trigger)
    return _apify_start(db, src, conn, trigger)  # type: ignore[arg-type]


def _apify_start(db: Session, src: Source, conn: ApifyConnector, trigger: str, *,
                 stage: str = "items", parent: SourceSync | None = None) -> SourceSync:
    s = get_settings()
    if stage == "items":
        setting_key, metric, _ = apify_mod.KINDS[src.kind]
        budget = run_budget(db, src, metric, s.apify_items_per_run, manual=trigger == "manual")
    else:
        setting_key, metric = apify_mod.COMMENTS[0], apify_mod.COMMENTS[1]
        budget = run_budget(db, src, metric,
                            s.apify_comments_per_post * s.apify_comment_posts_per_sync,
                            manual=trigger == "manual", kinds=COMMENT_KINDS)
    if budget <= 0:
        return _skip(db, src, "apify", trigger, "quota",
                     f"monthly {metric.replace('_', ' ')} limit reached", stage=stage,
                     parent=parent.id if parent else None)
    if stage == "items":
        run_input = apify_mod.build_input(src.kind, url=src.url, query=src.query, limit=budget,
                                          since=_since(src))
        posts: list[dict[str, Any]] = []
    else:
        per_post = s.apify_comments_per_post
        posts = _take_mature(src, max(1, budget // max(1, per_post)))
        if not posts:                      # nothing ready yet: no run, no row
            assert parent is not None
            return parent
        budget = min(budget, per_post * len(posts))
        run_input = apify_mod.comments_input([p["url"] for p in posts], per_post=per_post)
    sync = SourceSync(id=uuid.uuid4(), tenant_id=src.tenant_id, source_id=src.id,
                      provider="apify", stage=stage, trigger=trigger, max_items=budget,
                      parent_sync_id=parent.id if parent else None,
                      started_at=datetime.now(UTC), status="running")
    db.add(sync)
    db.flush()
    try:
        actor = conn.actor_for(setting_key)
        sync.actor = actor
        run = conn.start(actor, run_input, max_items=budget)
    except ConnectorError as e:
        if stage == "comments":            # put the posts back for the next sync
            state = dict(src.sync_state or {})
            state["pending_comments"] = posts + list(state.get("pending_comments") or [])
            ids = {p["external_id"] for p in posts}
            state["comments_done"] = [x for x in state.get("comments_done") or []
                                      if x not in ids]
            src.sync_state = state
        _finish(src, sync, "failed", category=_category(e), error=str(e))
        return sync
    sync.external_run_id, sync.dataset_id = run.id, run.dataset_id
    queue.enqueue(db, "social.poll", {"sync_id": str(sync.id)}, tenant_id=src.tenant_id,
                  run_after=datetime.now(UTC) + timedelta(seconds=s.apify_poll_seconds),
                  max_attempts=3)
    return sync


def _category(e: ConnectorError) -> str:
    allowed = {"not_configured", "auth", "rate_limit", "provider", "network", "timeout",
               "parse", "not_found", "input", "quota"}
    return e.reason if e.reason in allowed else "provider"


def poll(db: Session, sync_id: uuid.UUID) -> None:
    s = get_settings()
    sync = db.get(SourceSync, sync_id)
    if sync is None or sync.status != "running" or sync.provider != "apify":
        return
    src = db.get(Source, sync.source_id)
    assert src is not None
    conn = ApifyConnector()
    sync.polls += 1
    waited = (datetime.now(UTC) - (sync.started_at or sync.created_at)).total_seconds() / 60
    try:
        run = conn.get_run(sync.external_run_id or "")
    except ConnectorError as e:
        if e.fatal or waited > s.apify_max_wait_minutes:
            _finish(src, sync, "failed", category=_category(e), error=str(e))
            return
        _poll_again(db, sync)
        return
    if run.status in apify_mod.RUNNING:
        if waited <= s.apify_max_wait_minutes:
            _poll_again(db, sync)
            return
        conn.abort(run.id)        # waited too long: stop the run, keep what it produced
        run.status = "ABORTED"
    sync.provider_cost_usd = run.cost_usd
    if run.status not in apify_mod.FINISHED_WITH_DATA or not run.dataset_id:
        _finish(src, sync, "failed", category="provider", error=f"Apify run {run.status}")
        return
    try:
        rows = conn.items(run.dataset_id, limit=sync.max_items or 1)
    except ConnectorError as e:
        _finish(src, sync, "failed", category=_category(e), error=str(e))
        return
    _ingest_apify(db, src, sync, rows, timed_out=run.status != "SUCCEEDED")


def _poll_again(db: Session, sync: SourceSync) -> None:
    wait = timedelta(seconds=get_settings().apify_poll_seconds)
    queue.enqueue(db, "social.poll", {"sync_id": str(sync.id)}, tenant_id=sync.tenant_id,
                  run_after=datetime.now(UTC) + wait, max_attempts=3)


def _ingestor(db: Session, src: Source, project: MonitoringProject, sync: SourceSync,
              prov: Provenance) -> Ingestor:
    from app.modules.billing.entitlements import remaining_this_month
    return Ingestor(db, src, project, load_query_plan(db, project), prov,
                    mention_quota=remaining_this_month(db, src.tenant_id,
                                                       "max_mentions_per_month"),
                    sync_id=sync.id)


def _ingest_apify(db: Session, src: Source, sync: SourceSync, rows: list[dict[str, Any]], *,
                  timed_out: bool) -> None:
    from app.billing import usage
    metric = apify_mod.COMMENTS[1] if sync.stage == "comments" else apify_mod.KINDS[src.kind][1]
    sync.items_fetched = len(rows)
    sync.results_billed = len(rows)
    usage.record(db, src.tenant_id, metric, len(rows), sync=sync.id)
    project, why = _context(db, src)
    if project is None:
        _finish(src, sync, "partial", category="input", error=f"not stored: {why}")
        return
    kind = "facebook_comments" if sync.stage == "comments" else src.kind
    items, bad = apify_mod.parse(kind, rows)
    sync.items_unreadable = bad
    prov = Provenance("third_party_provider", "apify", ApifyConnector.version, "provider_record",
                      {"provider": "apify", "actor": sync.actor, "run_id": sync.external_run_id,
                       "sync_id": str(sync.id)})
    ing = _ingestor(db, src, project, sync, prov)
    stats = ing.comments(items) if sync.stage == "comments" else ing.posts(items)
    ing.finish()
    sync.mentions_new, sync.mentions_known = stats.new, stats.known
    if sync.stage == "items":
        _remember_newest(src, items)
        if src.kind in COMMENT_KINDS:
            _queue_pending(src, stats.kept_posts)
    problems = []
    if timed_out:
        problems.append(("timeout", "the run was stopped before the end; partial results"))
    if bad and bad * 2 > len(rows):
        problems.append(("parse", f"{bad} of {len(rows)} items could not be read "
                                  "(the actor's output format may have changed)"))
    if stats.quota_reached:
        problems.append(("quota", "monthly mention limit reached"))
    if problems:
        _finish(src, sync, "partial", category=problems[0][0],
                error="; ".join(p[1] for p in problems))
    else:
        _finish(src, sync, "succeeded")
    if sync.stage == "items" and src.kind in COMMENT_KINDS:
        _apify_start(db, src, ApifyConnector(), sync.trigger, stage="comments", parent=sync)


# ------------------------------------------------------------------------------ YouTube
def _youtube(db: Session, src: Source, project: MonitoringProject, trigger: str) -> SourceSync:
    from app.billing import usage
    s = get_settings()
    sync = SourceSync(id=uuid.uuid4(), tenant_id=src.tenant_id, source_id=src.id,
                      provider="youtube", trigger=trigger, started_at=datetime.now(UTC),
                      status="running", actor="youtube-data-api-v3")
    db.add(sync)
    db.flush()

    def spend(units: int) -> None:
        used = int(db.scalar(text("SELECT youtube_units_today()")) or 0)
        if used + units > s.youtube_daily_quota_units:
            raise ConnectorError("quota", "daily YouTube quota of the platform reached",
                                 fatal=True)
        usage.record(db, src.tenant_id, "youtube_units", units, sync=sync.id)
        db.flush()
        sync.api_units += units

    yt = YouTubeConnector(spend)
    budget = run_budget(db, src, "youtube_results", s.youtube_videos_per_sync,
                        manual=trigger == "manual")
    sync.max_items = budget
    if budget <= 0:
        _finish(src, sync, "skipped", category="quota", error="monthly YouTube limit reached")
        return sync
    prov = Provenance("platform_api", "youtube", YouTubeConnector.version,
                      "platform_api_record", {"provider": "youtube", "sync_id": str(sync.id)})
    ing = _ingestor(db, src, project, sync, prov)
    problems: list[tuple[str, str]] = []
    try:
        since = _since(src)
        if src.kind == "youtube_search":
            query = src.query.strip() or project.name
            ids = yt.search_ids(query, since=since, limit=budget)
        else:
            state = dict(src.sync_state or {})
            playlist = state.get("uploads_playlist")
            if not playlist:
                playlist, title = yt.uploads_playlist(src.url)
                state["uploads_playlist"] = playlist
                src.sync_state = state
                if title and not src.name:
                    src.name = title[:200]
            ids = yt.playlist_ids(str(playlist), since=since, limit=budget)
        videos = yt.videos(ids)
        sync.items_fetched = len(videos)
        ing.posts(videos)
        _remember_newest(src, videos)
        _queue_pending(src, ing.stats.kept_posts)
        fetched_comments = 0
        for p in _take_mature(src, s.youtube_videos_per_sync):
            video = SocialItem("youtube", p["external_id"], p["url"], "", "video")
            comments = yt.comments(video, limit=s.youtube_comments_per_video)
            for c in comments:
                for x in c.walk():
                    x.extra["post_external_id"] = video.external_id
            fetched_comments += sum(len(c.walk()) for c in comments)
            ing.comments(comments)
        sync.items_fetched += fetched_comments
    except ConnectorError as e:
        problems.append((_category(e), str(e)))
    ing.finish()
    sync.mentions_new, sync.mentions_known = ing.stats.new, ing.stats.known
    sync.results_billed = sync.items_fetched
    usage.record(db, src.tenant_id, "youtube_results", sync.items_fetched, sync=sync.id)
    if ing.stats.quota_reached:
        problems.append(("quota", "monthly mention limit reached"))
    if problems and not sync.items_fetched:
        _finish(src, sync, "failed", category=problems[0][0], error=problems[0][1])
    elif problems:
        _finish(src, sync, "partial", category=problems[0][0],
                error="; ".join(p[1] for p in problems))
    else:
        _finish(src, sync, "succeeded")
    return sync


def queue_manual(db: Session, src: Source) -> None:
    queue.enqueue(db, "social.sync", {"source_id": str(src.id), "trigger": "manual"},
                  tenant_id=src.tenant_id, max_attempts=1)


def schedule(db: Session) -> int:
    return int(db.scalar(text("SELECT schedule_social_syncs(:m)"),
                         {"m": get_settings().social_default_frequency_minutes}) or 0)
