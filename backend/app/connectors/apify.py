"""Apify: third-party collection provider (product-owner decision 2026-11-25).

Actors run **logged out**: no account, no cookies, public content only (no private group).
Each source kind maps to an actor (configurable: `REPINTEL_APIFY_ACTORS`) and to an adapter
that builds the actor input and reads its output. Actors are written by Apify or by
independent developers and their output changes over time: adapters accept the documented
field names and alternatives, and items they cannot read are counted, never guessed.

Every run carries `maxItems` (results billed) and `maxTotalChargeUsd` (cost cap).
"""

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, ClassVar

import httpx

from app.connectors.base import Connector, ConnectorError, Health
from app.connectors.social import SocialItem, counts, pick, text_of, to_datetime, to_int
from app.core.config import get_settings

# Source kind → (actor setting key, usage metric / plan category, platform)
KINDS: dict[str, tuple[str, str, str]] = {
    "facebook_page": ("facebook_page", "facebook_posts", "facebook"),
    "facebook_group": ("facebook_group", "facebook_posts", "facebook"),
    "tiktok_profile": ("tiktok", "tiktok_results", "tiktok"),
    "tiktok_hashtag": ("tiktok", "tiktok_results", "tiktok"),
    "tiktok_search": ("tiktok", "tiktok_results", "tiktok"),
    "reddit_community": ("reddit", "reddit_results", "reddit"),
    "reddit_search": ("reddit", "reddit_results", "reddit"),
    "linkedin_company": ("linkedin_company", "linkedin_results", "linkedin"),
}
COMMENTS = ("facebook_comments", "facebook_comments", "facebook")
RUNNING = ("READY", "RUNNING", "TIMING-OUT", "ABORTING")
FINISHED_WITH_DATA = ("SUCCEEDED", "TIMED-OUT", "ABORTED")


@lru_cache
def apify_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


@dataclass
class Run:
    id: str
    status: str
    dataset_id: str | None
    cost_usd: float | None


class ApifyConnector(Connector):
    key = "apify"
    version = "1.0"
    category = "third_party_providers"
    access_method = "third_party_provider"
    capabilities: ClassVar[frozenset[str]] = frozenset({"collect", "comments"})
    limitations = (
        "Public content only, collected logged out: no private groups, no personal profiles' "
        "restricted posts, no private messages.",
        "No keyword search on Facebook: pages and groups must be listed.",
        "Facebook comments: up to the configured number per post, fetched once per post.",
        "TikTok and LinkedIn: posts/videos only in this version (no comments).",
        "Actors are maintained by Apify or independent developers; they can break or change "
        "price. Unreadable items are counted on each sync.",
        "Every result is billed by Apify; monthly caps per client and per platform apply.",
    )

    def __init__(self) -> None:
        s = get_settings()
        self.token = s.apify_token.get_secret_value()
        self.base = s.apify_base_url.rstrip("/")
        self.actors = s.apify_actors

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def health_check(self) -> Health:
        return Health(self.configured, "" if self.configured else "Apify token not configured")

    def actor_for(self, setting_key: str) -> str:
        actor = (self.actors.get(setting_key) or "").strip()
        if not actor:
            raise ConnectorError("not_configured", f"no Apify actor configured for {setting_key}",
                                 fatal=True)
        return actor.replace("/", "~")

    # -- HTTP -------------------------------------------------------------------------
    def _call(self, method: str, path: str, **kw: Any) -> Any:
        if not self.configured:
            raise ConnectorError("not_configured", "REPINTEL_APIFY_TOKEN is not set", fatal=True)
        try:
            r = apify_client().request(method, f"{self.base}{path}",
                                       headers={"Authorization": f"Bearer {self.token}"}, **kw)
        except httpx.HTTPError as e:
            raise ConnectorError("network", type(e).__name__) from e
        if r.status_code in (401, 403):
            raise ConnectorError("auth", f"Apify refused the token ({r.status_code})", fatal=True)
        if r.status_code == 402:
            raise ConnectorError("quota", "Apify account credit or limit exhausted", fatal=True)
        if r.status_code == 404:
            raise ConnectorError("not_found", "actor, run or dataset not found", fatal=True)
        if r.status_code == 429:
            raise ConnectorError("rate_limit", r.headers.get("retry-after", ""))
        if r.status_code >= 400:
            raise ConnectorError("provider", f"HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise ConnectorError("provider", "invalid JSON from Apify") from e

    @staticmethod
    def _run(data: Any) -> Run:
        d = (data or {}).get("data") or {}
        cost = d.get("usageTotalUsd")
        return Run(str(d.get("id") or ""), str(d.get("status") or ""),
                   d.get("defaultDatasetId"),
                   float(cost) if isinstance(cost, int | float) else None)

    def start(self, actor: str, run_input: dict[str, Any], *, max_items: int) -> Run:
        s = get_settings()
        run = self._run(self._call(
            "POST", f"/v2/acts/{actor}/runs", json=run_input,
            params={"maxItems": max_items, "maxTotalChargeUsd": s.apify_max_run_usd,
                    "timeout": s.apify_run_timeout_seconds}))
        if not run.id:
            raise ConnectorError("provider", "Apify returned no run id")
        return run

    def get_run(self, run_id: str) -> Run:
        return self._run(self._call("GET", f"/v2/actor-runs/{run_id}"))

    def abort(self, run_id: str) -> None:
        with contextlib.suppress(ConnectorError):
            self._call("POST", f"/v2/actor-runs/{run_id}/abort")

    def items(self, dataset_id: str, *, limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while len(out) < limit:
            page = self._call("GET", f"/v2/datasets/{dataset_id}/items",
                              params={"format": "json", "clean": "true", "offset": len(out),
                                      "limit": min(1000, limit - len(out))})
            if not isinstance(page, list) or not page:
                break
            out += [p for p in page if isinstance(p, dict)]
            if len(page) < min(1000, limit):
                break
        return out[:limit]


# ---------------------------------------------------------------------------- inputs
def _since(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m-%d") if d else None


def build_input(kind: str, *, url: str, query: str, limit: int,
                since: datetime | None) -> dict[str, Any]:
    """Actor input for one source. `since`: newest item already collected (None = first run)."""
    if kind == "facebook_page":
        inp: dict[str, Any] = {"startUrls": [{"url": url}], "resultsLimit": limit}
        if since:
            inp["onlyPostsNewerThan"] = _since(since)
        return inp
    if kind == "facebook_group":
        inp = {"startUrls": [{"url": url}], "resultsLimit": limit, "viewOption": "CHRONOLOGICAL"}
        if since:
            inp["onlyPostsNewerThan"] = _since(since)
        return inp
    if kind.startswith("tiktok_"):
        base: dict[str, Any] = {"resultsPerPage": limit, "excludePinnedPosts": True,
                                "shouldDownloadVideos": False, "shouldDownloadCovers": False}
        value = query.strip()
        if kind == "tiktok_profile":
            return base | {"profiles": [value.lstrip("@")], "profileSorting": "latest"}
        if kind == "tiktok_hashtag":
            return base | {"hashtags": [value.lstrip("#")]}
        return base | {"searchQueries": [value]}
    if kind == "reddit_community":
        return {"scrapeMode": "posts", "communityNames": [query.strip().removeprefix("r/")],
                "maxPostsPerCommunity": limit, "includeNsfw": False}
    if kind == "reddit_search":
        return {"scrapeMode": "posts", "searchQueries": [query.strip()],
                "maxSearchResultsPerQuery": limit, "includeNsfw": False}
    if kind == "linkedin_company":
        inp = {"targetUrls": [url], "maxPosts": limit, "scrapeComments": False,
               "scrapeReactions": False}
        if since:
            inp["postedLimitDate"] = since.isoformat()
        return inp
    raise ValueError(f"no Apify input for {kind}")


def comments_input(post_urls: list[str], *, per_post: int) -> dict[str, Any]:
    return {"startUrls": [{"url": u} for u in post_urls], "resultsLimit": per_post,
            "includeNestedComments": True, "viewOption": "RANKED_UNFILTERED"}


# ---------------------------------------------------------------------------- outputs
def _facebook_comment(d: dict[str, Any], *, post_url: str | None = None,
                      reply_to: str | None = None) -> SocialItem | None:
    cid = text_of(pick(d, "id", "commentId", "legacyId"))
    body = text_of(pick(d, "text", "body"))
    if not cid or not body:
        return None
    post = text_of(pick(d, "facebookUrl", "postUrl", "inputUrl")) or post_url or ""
    parent_comment = text_of(pick(d, "replyToCommentId", "parentComment.id")) or reply_to
    depth = to_int(d.get("threadingDepth")) or 0
    is_reply = bool(parent_comment) or depth > 0
    item = SocialItem(
        platform="facebook", external_id=cid, url=text_of(d.get("commentUrl")) or post,
        text=body, content_type="reply" if is_reply else "comment",
        author_name=text_of(pick(d, "profileName", "name", "author.name")) or None,
        author_id=text_of(pick(d, "profileId", "author.id")) or None,
        author_url=text_of(pick(d, "profileUrl", "author.url")) or None,
        published_at=to_datetime(pick(d, "date", "time", "timestamp")),
        engagement=counts(d, likes=("likesCount", "likes"), replies=("commentsCount",)),
        parent_external_id=parent_comment if is_reply else None,
        parent_url=post or None)
    for r in d.get("comments") or []:
        if isinstance(r, dict) and (c := _facebook_comment(r, post_url=post, reply_to=cid)):
            item.children.append(c)
    return item


def _facebook_post(d: dict[str, Any], *, group: bool) -> SocialItem | None:
    pid = text_of(pick(d, "postId", "legacyId", "id") if not group
                  else pick(d, "legacyId", "postId", "id"))
    url = text_of(pick(d, "url", "topLevelUrl", "postUrl"))
    body = text_of(pick(d, "text", "message", "postText"))
    if not pid or not url:
        return None
    item = SocialItem(
        platform="facebook", external_id=pid, url=url, text=body, content_type="post",
        author_name=text_of(pick(d, "user.name", "pageName", "authorName")) or None,
        author_id=text_of(pick(d, "user.id", "authorId")) or None,
        author_url=text_of(pick(d, "user.profileUrl", "authorUrl")) or None,
        published_at=to_datetime(pick(d, "time", "timestamp", "date")),
        engagement=counts(d, reactions=("likes", "likesCount", "reactionsCount"),
                          comments=("comments", "commentsCount"),
                          shares=("shares", "sharesCount"), views=("viewsCount",)),
        container=text_of(pick(d, "groupTitle", "pageName")) or None,
        extra={k: d[k] for k in ("link",) if isinstance(d.get(k), str)})
    item.comments_count = item.engagement.get("comments")
    for c in d.get("topComments") or []:
        if isinstance(c, dict) and (ci := _facebook_comment(c, post_url=url)):
            item.children.append(ci)
    return item


def _tiktok(d: dict[str, Any]) -> SocialItem | None:
    if d.get("error") or d.get("errorCode"):
        return None
    vid, url = text_of(d.get("id")), text_of(pick(d, "webVideoUrl", "url"))
    if not vid or not url:
        return None
    handle = text_of(pick(d, "authorMeta.name", "author.uniqueId"))
    item = SocialItem(
        platform="tiktok", external_id=vid, url=url, text=text_of(d.get("text")),
        content_type="video",
        author_name=text_of(pick(d, "authorMeta.nickName", "authorMeta.name")) or None,
        author_id=text_of(pick(d, "authorMeta.id")) or None,
        author_url=text_of(pick(d, "authorMeta.profileUrl")) or None,
        published_at=to_datetime(pick(d, "createTimeISO", "createTime")),
        engagement=counts(d, likes=("diggCount",), comments=("commentCount",),
                          shares=("shareCount",), views=("playCount",), saves=("collectCount",)),
        container=handle or None,
        extra={"language_hint": d["textLanguage"]} if isinstance(d.get("textLanguage"), str)
        else {})
    item.comments_count = item.engagement.get("comments")
    return item


def _reddit(d: dict[str, Any]) -> SocialItem | None:
    rid = text_of(pick(d, "id", "parsedId"))
    community = text_of(pick(d, "communityName", "subreddit", "parsedCommunityName"))
    community = community.removeprefix("r/")
    if not rid:
        return None
    url = text_of(pick(d, "url", "postUrl"))
    permalink = text_of(d.get("permalink"))
    if not url.startswith("http") and permalink.startswith("/"):
        url = "https://www.reddit.com" + permalink
    is_comment = bool(pick(d, "postId", "parentCommentId")) or d.get("dataType") == "comment"
    if not url.startswith("http") and community and not is_comment:
        # Reddit's own permalink format for a post id (deterministic, not guessed content)
        url = f"https://www.reddit.com/r/{community}/comments/{rid.removeprefix('t3_')}/"
    if not url.startswith("http"):
        return None
    title = text_of(d.get("title"))
    body = text_of(pick(d, "body", "text", "selftext"))
    item = SocialItem(
        platform="reddit", external_id=rid, url=url, title=title,
        text="\n".join(p for p in (title, body) if p),
        content_type="comment" if is_comment else "post",
        author_name=text_of(pick(d, "authorName", "username", "author")) or None,
        published_at=to_datetime(pick(d, "createdAt", "created", "created_utc")),
        engagement=counts(d, score=("score", "upVotes"),
                          comments=("commentCount", "numberOfComments")),
        container=f"r/{community}" if community else None,
        parent_external_id=text_of(pick(d, "parentCommentId", "postId")) or None)
    item.comments_count = item.engagement.get("comments")
    return item if item.text else None


def _linkedin(d: dict[str, Any]) -> SocialItem | None:
    pid = text_of(pick(d, "id", "urn", "entityId"))
    url = text_of(pick(d, "linkedinUrl", "url", "postUrl"))
    body = text_of(pick(d, "content", "text", "commentary"))
    if not pid or not url or not body:
        return None
    item = SocialItem(
        platform="linkedin", external_id=pid, url=url, text=body, content_type="post",
        author_name=text_of(pick(d, "author.name", "authorName")) or None,
        author_url=text_of(pick(d, "author.linkedinUrl", "author.url")) or None,
        published_at=to_datetime(pick(d, "postedAt.timestamp", "postedAt.date", "postedAt",
                                      "date")),
        engagement=counts(d, likes=("engagement.likes", "numLikes"),
                          comments=("engagement.comments", "numComments"),
                          shares=("engagement.shares", "numShares")),
        container=text_of(pick(d, "author.name")) or None)
    item.comments_count = item.engagement.get("comments")
    return item


PARSERS: dict[str, Callable[[dict[str, Any]], SocialItem | None]] = {
    "facebook_page": lambda d: _facebook_post(d, group=False),
    "facebook_group": lambda d: _facebook_post(d, group=True),
    "facebook_comments": lambda d: _facebook_comment(d),
    "tiktok_profile": _tiktok, "tiktok_hashtag": _tiktok, "tiktok_search": _tiktok,
    "reddit_community": _reddit, "reddit_search": _reddit,
    "linkedin_company": _linkedin,
}


def parse(kind: str, rows: list[dict[str, Any]]) -> tuple[list[SocialItem], int]:
    """(items read, rows that could not be read)."""
    fn = PARSERS[kind]
    items, bad = [], 0
    for r in rows:
        try:
            it = fn(r)
        except (TypeError, ValueError, AttributeError):
            it = None
        if it is None:
            bad += 1
        else:
            items.append(it)
    return items, bad
