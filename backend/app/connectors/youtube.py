"""YouTube Data API v3 (official), with an API key: public videos and comments.

Quota: the platform's Google Cloud project has a daily quota shared by every client
(`REPINTEL_YOUTUBE_DAILY_QUOTA_UNITS`, reset at midnight Pacific time). Each call's cost is
recorded as usage (`youtube_units`) before it is made; when the quota would be exceeded the
sync stops cleanly. Costs below follow Google's published table (search.list is the expensive
one); they are deliberately conservative.
"""

import re
from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from app.connectors.base import Connector, ConnectorError, Health
from app.connectors.social import SocialItem, counts, pick, text_of, to_datetime
from app.core.config import get_settings

UNITS = {"search": 100, "videos": 1, "channels": 1, "playlistItems": 1, "commentThreads": 1}


@lru_cache
def youtube_client() -> httpx.Client:
    return httpx.Client(timeout=20.0)


def channel_ref(url: str) -> dict[str, str]:
    """channels.list selector from a channel URL (/@handle, /channel/UC…) or a bare handle."""
    u = url.strip()
    if u.startswith("@"):
        return {"forHandle": u}
    path = urlsplit(u).path if u.startswith("http") else u
    if m := re.match(r"^/?channel/(UC[\w-]{20,})", path):
        return {"id": m.group(1)}
    if m := re.match(r"^/?(@[\w.\-]{3,})", path):
        return {"forHandle": m.group(1)}
    raise ConnectorError("input", "use a channel address like https://www.youtube.com/@name "
                                  "or https://www.youtube.com/channel/UC…", fatal=True)


class YouTubeConnector(Connector):
    key = "youtube"
    version = "1.0"
    category = "social_networks"
    access_method = "official_api"
    capabilities: ClassVar[frozenset[str]] = frozenset({"search", "collect", "comments"})
    limitations = (
        "Public videos and comments only; comments disabled by the channel are not available.",
        "Keyword search returns what YouTube ranks for the query and region, not every video.",
        "The daily quota of the platform's Google Cloud project is shared by all clients.",
        "Comments: up to the configured number of most recent threads per video, with the "
        "replies YouTube includes in them.",
    )

    def __init__(self, spend: Callable[[int], None] | None = None) -> None:
        s = get_settings()
        self.key_ = s.youtube_api_key.get_secret_value()
        self.base = s.youtube_base_url.rstrip("/")
        self.region = s.youtube_region
        self.spend = spend or (lambda units: None)

    @property
    def configured(self) -> bool:
        return bool(self.key_)

    def health_check(self) -> Health:
        return Health(self.configured, "" if self.configured else "YouTube API key not configured")

    def _get(self, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise ConnectorError("not_configured", "REPINTEL_YOUTUBE_API_KEY is not set",
                                 fatal=True)
        self.spend(UNITS[resource])          # raises when the daily quota would be exceeded
        try:
            r = youtube_client().get(f"{self.base}/{resource}",
                                     params={**params, "key": self.key_})
        except httpx.HTTPError as e:
            raise ConnectorError("network", type(e).__name__) from e
        if r.status_code == 200:
            return r.json()
        reason = ""
        try:
            errs = r.json().get("error", {}).get("errors") or [{}]
            reason = str(errs[0].get("reason", ""))
        except (ValueError, AttributeError):
            pass
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            raise ConnectorError("quota", reason, fatal=True)
        if reason == "commentsDisabled":
            raise ConnectorError("comments_disabled", reason)
        if r.status_code in (400, 403) and reason in ("keyInvalid", "accessNotConfigured",
                                                      "forbidden", "ipRefererBlocked"):
            raise ConnectorError("auth", reason, fatal=True)
        if r.status_code == 404:
            raise ConnectorError("not_found", reason or "not found")
        raise ConnectorError("provider", f"HTTP {r.status_code} {reason}".strip())

    # -- discovery of video ids -------------------------------------------------------------
    def search_ids(self, query: str, *, since: datetime | None, limit: int) -> list[str]:
        params: dict[str, Any] = {"part": "snippet", "q": query, "type": "video",
                                  "order": "date", "maxResults": min(50, limit),
                                  "regionCode": self.region}
        if since:
            params["publishedAfter"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        data = self._get("search", params)
        return [i["id"]["videoId"] for i in data.get("items", [])
                if isinstance(i.get("id"), dict) and i["id"].get("videoId")][:limit]

    def uploads_playlist(self, url: str) -> tuple[str, str]:
        data = self._get("channels", {"part": "contentDetails,snippet", **channel_ref(url)})
        items = data.get("items") or []
        if not items:
            raise ConnectorError("not_found", "channel not found", fatal=True)
        ch = items[0]
        pl = pick(ch, "contentDetails.relatedPlaylists.uploads")
        if not pl:
            raise ConnectorError("not_found", "channel has no public uploads", fatal=True)
        return str(pl), text_of(pick(ch, "snippet.title"))

    def playlist_ids(self, playlist: str, *, since: datetime | None, limit: int) -> list[str]:
        data = self._get("playlistItems", {"part": "contentDetails", "playlistId": playlist,
                                           "maxResults": min(50, limit)})
        out = []
        for i in data.get("items", []):
            vid = pick(i, "contentDetails.videoId")
            when = to_datetime(pick(i, "contentDetails.videoPublishedAt"))
            if vid and not (since and when and when <= since):
                out.append(str(vid))
        return out[:limit]

    # -- content ----------------------------------------------------------------------------
    def videos(self, ids: list[str]) -> list[SocialItem]:
        out: list[SocialItem] = []
        for i in range(0, len(ids), 50):
            data = self._get("videos", {"part": "snippet,statistics",
                                        "id": ",".join(ids[i:i + 50])})
            for v in data.get("items", []):
                vid = text_of(v.get("id"))
                if not vid:
                    continue
                title = text_of(pick(v, "snippet.title"))
                desc = text_of(pick(v, "snippet.description"))
                ch = text_of(pick(v, "snippet.channelId"))
                item = SocialItem(
                    platform="youtube", external_id=vid,
                    url=f"https://www.youtube.com/watch?v={vid}", title=title,
                    text="\n".join(p for p in (title, desc) if p), content_type="video",
                    author_name=text_of(pick(v, "snippet.channelTitle")) or None,
                    author_id=ch or None,
                    author_url=f"https://www.youtube.com/channel/{ch}" if ch else None,
                    published_at=to_datetime(pick(v, "snippet.publishedAt")),
                    engagement=counts(v, views=("statistics.viewCount",),
                                      likes=("statistics.likeCount",),
                                      comments=("statistics.commentCount",)),
                    container=text_of(pick(v, "snippet.channelTitle")) or None,
                    extra={"language_hint": pick(v, "snippet.defaultAudioLanguage",
                                                 "snippet.defaultLanguage")}
                    if pick(v, "snippet.defaultAudioLanguage", "snippet.defaultLanguage") else {})
                item.comments_count = item.engagement.get("comments")
                out.append(item)
        return out

    def comments(self, video: SocialItem, *, limit: int) -> list[SocialItem]:
        try:
            data = self._get("commentThreads", {
                "part": "snippet,replies", "videoId": video.external_id,
                "maxResults": max(1, min(100, limit)), "order": "time",
                "textFormat": "plainText"})
        except ConnectorError as e:
            if e.reason in ("comments_disabled", "not_found"):
                return []
            raise
        out = []
        for t in data.get("items", []):
            top = pick(t, "snippet.topLevelComment")
            c = self._comment(top, video, parent=None)
            if c is None:
                continue
            for r in pick(t, "replies.comments") or []:
                if (rc := self._comment(r, video, parent=c.external_id)) is not None:
                    c.children.append(rc)
            out.append(c)
        return out

    @staticmethod
    def _comment(d: Any, video: SocialItem, *, parent: str | None) -> SocialItem | None:
        if not isinstance(d, dict):
            return None
        cid = text_of(d.get("id"))
        sn = d.get("snippet") or {}
        body = text_of(pick(sn, "textOriginal", "textDisplay"))
        if not cid or not body:
            return None
        ch = text_of(pick(sn, "authorChannelId.value"))
        return SocialItem(
            platform="youtube", external_id=cid,
            url=f"https://www.youtube.com/watch?v={video.external_id}&lc={cid}", text=body,
            content_type="reply" if parent else "comment",
            author_name=text_of(sn.get("authorDisplayName")) or None, author_id=ch or None,
            author_url=text_of(sn.get("authorChannelUrl")) or None,
            published_at=to_datetime(sn.get("publishedAt")),
            engagement=counts(sn, likes=("likeCount",)), parent_external_id=parent,
            parent_url=video.url)
