"""Social items → mentions: attribution to the project's tracked names, threads, dedup.

- Attribution reuses the monitoring project's query plan (brand, products, competitors,
  exclusions). `match_mode = 'brand'` (the brand's own page or channel) keeps every post for
  the brand; `terms` keeps a post only when it names a tracked name.
- Comments inherit the post they belong to. When the post itself is not kept, a comment is
  kept only if it names a tracked name itself.
- Idempotent: an item already stored for the brand (platform + external id) only gets its
  counts refreshed. The same post or comment collected by Scanner is marked as a duplicate of
  the first one received, so it is counted once.
- The monthly mention quota of the plan applies.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collection.text import content_hash, find_terms, normalize_for_match
from app.collection.urls import canonicalize
from app.connectors.social import PLATFORM_DOMAINS, SocialItem
from app.db.models import Mention, MonitoringProject, Source
from app.jobs import queue
from app.modules.monitoring.query_plan import QueryGroup

PRIORITY = {"brand": 0, "product": 1, "competitor": 2}
MIN_REPOST_CHARS = 40          # shorter texts ("merci", "top") are not treated as reposts


@dataclass
class Provenance:
    method: str                # third_party_provider | platform_api
    connector: str             # apify | youtube
    version: str
    verification: str          # provider_record | platform_api_record
    raw: dict[str, Any]        # provider, actor, run and sync ids


@dataclass
class IngestStats:
    new: int = 0
    known: int = 0
    not_matched: int = 0
    excluded: int = 0
    quota_reached: bool = False
    new_ids: list[uuid.UUID] = field(default_factory=list)
    kept_posts: list[Mention] = field(default_factory=list)


def attribute(text: str, groups: list[QueryGroup], match_mode: str
              ) -> tuple[QueryGroup | None, list[str], bool]:
    """(group, matched terms, excluded)."""
    ordered = sorted(groups, key=lambda g: PRIORITY.get(g.entity_type, 9))
    if match_mode == "brand":
        brand = next((g for g in ordered if g.entity_type == "brand"), None)
        if brand is None:
            return None, [], False
        if brand.exclude and find_terms(text, brand.exclude):
            return None, [], True
        return brand, find_terms(text, brand.terms), False
    excluded = False
    for g in ordered:
        hit = find_terms(text, g.terms)
        if not hit:
            continue
        if g.exclude and find_terms(text, g.exclude):
            excluded = True
            continue
        return g, hit, False
    return None, [], excluded


def canonical_for(item: SocialItem) -> str:
    url = item.url if item.url.startswith("http") else ""
    if item.content_type in ("comment", "reply") and (not url or url == item.parent_url):
        return f"{item.platform}:{item.content_type}:{item.external_id}"
    return canonicalize(url) if url else f"{item.platform}:{item.content_type}:{item.external_id}"


def _title(item: SocialItem) -> str:
    if item.title:
        return item.title[:2000]
    first = " ".join(item.text.split())[:140]
    return first or (item.container or item.platform)


class Ingestor:
    def __init__(self, db: Session, source: Source, project: MonitoringProject,
                 groups: list[QueryGroup], prov: Provenance, *, mention_quota: int | None,
                 sync_id: uuid.UUID) -> None:
        self.db, self.source, self.project, self.groups = db, source, project, groups
        self.prov, self.left, self.sync_id = prov, mention_quota, sync_id
        self.stats = IngestStats()
        self._by_external: dict[tuple[str, str], Mention] = {}

    # -- lookups ---------------------------------------------------------------------------
    def _existing(self, item: SocialItem) -> Mention | None:
        key = (item.platform, item.external_id)
        if key in self._by_external:
            return self._by_external[key]
        m = self.db.scalars(select(Mention).where(
            Mention.tenant_id == self.project.tenant_id, Mention.brand_id == self.project.brand_id,
            Mention.platform == item.platform, Mention.external_id == item.external_id[:200])
        ).one_or_none()
        if m is not None:
            self._by_external[key] = m
        return m

    def parent_post(self, comment: SocialItem) -> Mention | None:
        """The stored post (or video) a comment belongs to."""
        post_id = comment.extra.get("post_external_id")
        if post_id:
            m = self._existing(SocialItem(comment.platform, str(post_id), "", "", "post"))
            if m is not None:
                return m
        for u in (comment.parent_url, comment.extra.get("input_url")):
            if isinstance(u, str) and u.startswith("http"):
                m = self.db.scalars(select(Mention).where(
                    Mention.tenant_id == self.project.tenant_id,
                    Mention.brand_id == self.project.brand_id,
                    Mention.canonical_url == canonicalize(u),
                    Mention.content_type.in_(("post", "video")))).first()
                if m is not None:
                    return m
        return None

    def _cross_duplicate(self, item: SocialItem, parent: Mention | None, h: str
                         ) -> uuid.UUID | None:
        """First copy of the same content already received for this brand: Scanner capture
        of the same post/comment, or an identical long text (repost)."""
        t, b = self.project.tenant_id, self.project.brand_id
        if item.content_type in ("post", "video") and item.url.startswith("http"):
            hit = self.db.scalar(select(Mention.id).where(
                Mention.tenant_id == t, Mention.brand_id == b,
                Mention.collection_method == "scanner", Mention.duplicate_of.is_(None),
                Mention.url.in_({item.url, canonicalize(item.url)}),
                Mention.raw["scanner"]["kind"].astext == "post").limit(1))
            if hit:
                return hit
        if item.content_type in ("comment", "reply") and parent is not None:
            body = normalize_for_match(item.text)
            for sid, stext in self.db.execute(select(Mention.id, Mention.original_text).where(
                    Mention.tenant_id == t, Mention.brand_id == b,
                    Mention.collection_method == "scanner", Mention.duplicate_of.is_(None),
                    Mention.url.in_({parent.url, parent.canonical_url}),
                    Mention.raw["scanner"]["kind"].astext.in_(("comment", "reply")))):
                if body and normalize_for_match(stext) == body:
                    return sid
        if item.content_type in ("post", "video") and len(item.text) >= MIN_REPOST_CHARS:
            return self.db.scalar(select(Mention.id).where(
                Mention.tenant_id == t, Mention.brand_id == b, Mention.content_hash == h,
                Mention.duplicate_of.is_(None)).limit(1))
        return None

    # -- storing ---------------------------------------------------------------------------
    def _store(self, item: SocialItem, group: QueryGroup, matched: list[str],
               parent: Mention | None) -> Mention | None:
        existing = self._existing(item)
        now = datetime.now(UTC)
        if existing is not None:
            if item.engagement:
                existing.engagement = item.engagement
            existing.last_seen_at = now
            existing.seen_count += 1
            self.stats.known += 1
            return existing
        if self.left is not None and self.left <= 0:
            self.stats.quota_reached = True
            return None
        title = _title(item)
        h = content_hash(title, item.text)
        raw = dict(self.prov.raw)
        raw.update({k: v for k, v in {
            "container": item.container, "parent_url": item.parent_url,
            "parent_external_id": item.parent_external_id,
            "comments_count": item.comments_count, **item.extra}.items() if v not in (None, "")})
        m = Mention(
            id=uuid.uuid4(), tenant_id=self.project.tenant_id, brand_id=self.project.brand_id,
            project_id=self.project.id, source_id=self.source.id,
            entity_type=group.entity_type, entity_name=group.entity_name[:200],
            url=(item.url if item.url.startswith("http") else item.parent_url or "")[:2000],
            canonical_url=canonical_for(item)[:2000],
            domain=PLATFORM_DOMAINS.get(item.platform, item.platform), title=title,
            snippet=item.text[:4000], original_text=item.text[:20000], matched_terms=matched,
            publisher=(item.author_name or None) and item.author_name[:200],
            published_at=item.published_at,
            published_at_source="provider" if item.published_at else None,
            collected_at=now, last_seen_at=now, collection_method=self.prov.method,
            connector=self.prov.connector, connector_version=self.prov.version,
            verification_status=self.prov.verification, content_hash=h,
            duplicate_of=self._cross_duplicate(item, parent, h), raw=raw,
            platform=item.platform, external_id=item.external_id[:200],
            content_type=item.content_type,
            parent_mention_id=parent.id if parent is not None else None,
            engagement=item.engagement or None,
            author_external_id=(item.author_id or None) and item.author_id[:200],
            author_url=(item.author_url or None) and item.author_url[:500])
        try:
            with self.db.begin_nested():
                self.db.add(m)
        except IntegrityError:          # same URL already stored for the brand
            self.stats.known += 1
            return None
        self._by_external[(item.platform, item.external_id)] = m
        self.stats.new += 1
        self.stats.new_ids.append(m.id)
        if self.left is not None:
            self.left -= 1
        return m

    def _thread(self, children: list[SocialItem], post: Mention | None,
                group: QueryGroup | None) -> None:
        by_id: dict[str, Mention] = {}
        for c in children:
            self._comment(c, post, group, by_id)

    def _comment(self, c: SocialItem, post: Mention | None, group: QueryGroup | None,
                 by_id: dict[str, Mention]) -> None:
        if self.stats.quota_reached:
            return
        parent = by_id.get(c.parent_external_id or "") or post
        if c.parent_external_id and c.parent_external_id not in by_id:
            p = self._existing(SocialItem(c.platform, c.parent_external_id, "", "", "comment"))
            parent = p or post
        g, matched, excluded = (group, find_terms(c.text, group.terms), False) if group else \
            attribute(c.text, self.groups, "terms")
        if group is not None and group.exclude and find_terms(c.text, group.exclude):
            excluded, g = True, None
        if g is None:
            self.stats.excluded += int(excluded)
            self.stats.not_matched += int(not excluded)
            stored = None
        else:
            stored = self._store(c, g, matched, parent)
        if stored is not None:
            by_id[c.external_id] = stored
        for r in c.children:
            self._comment(r, post, group, by_id)

    def posts(self, items: list[SocialItem]) -> IngestStats:
        for it in items:
            if self.stats.quota_reached:
                break
            text = "\n".join(p for p in (it.container or "", it.text) if p) \
                if self.source.match_mode == "brand" else it.text
            g, matched, excluded = attribute(text, self.groups, self.source.match_mode)
            if g is None:
                self.stats.excluded += int(excluded)
                self.stats.not_matched += int(not excluded)
                self._thread(it.children, None, None)     # comments may still name a brand
                continue
            m = self._store(it, g, find_terms(it.text, g.terms) or matched, None)
            if m is not None:
                self.stats.kept_posts.append(m)
                self._thread(it.children, m, g)
        return self.stats

    def comments(self, items: list[SocialItem]) -> IngestStats:
        """Comments fetched separately (Facebook comments actor, YouTube comment threads)."""
        for c in items:
            if self.stats.quota_reached:
                break
            post = self.parent_post(c)
            group = None
            if post is not None:
                group = next((g for g in self.groups if g.entity_type == post.entity_type
                              and g.entity_name == post.entity_name), None)
            self._comment(c, post, group, {})
        return self.stats

    def finish(self) -> None:
        from app.billing import usage
        t = self.project.tenant_id
        if self.stats.new:
            usage.record(self.db, t, "mentions_collected", self.stats.new, sync=self.sync_id)
            self.source.mentions_count += self.stats.new
            self.source.last_collected_at = datetime.now(UTC)
        ids = self.stats.new_ids
        for i in range(0, len(ids), 50):
            queue.enqueue(self.db, "nlp.analyze",
                          {"mention_ids": [str(x) for x in ids[i:i + 50]]}, tenant_id=t)
