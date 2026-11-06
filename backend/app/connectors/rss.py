"""RSS / Atom feeds registered in the tenant's source registry."""

from typing import ClassVar

from app.collection.feeds import FeedError, parse_feed
from app.collection.fetcher import FetchError, safe_get
from app.connectors.base import Connector, ConnectorError, Health, RawItem
from app.core.config import get_settings

FEED_ACCEPT = "application/rss+xml,application/atom+xml,application/xml;q=0.9,text/xml;q=0.8"


class RssConnector(Connector):
    key = "rss"
    version = "1.0"
    category = "rss"
    access_method = "public_feed"
    capabilities: ClassVar[frozenset[str]] = frozenset({"collect"})
    limitations = (
        "Only the items currently in the feed (usually the latest 10-50).",
        "Item summaries may be truncated by the publisher.",
    )

    def fetch(self, feed_url: str) -> tuple[str, list[RawItem]]:
        try:
            r = safe_get(feed_url, accept=FEED_ACCEPT)
        except FetchError as e:
            raise ConnectorError(e.reason, str(e)) from e
        if r.status >= 400:
            raise ConnectorError("http_error", f"feed returned {r.status}")
        try:
            title, entries = parse_feed(r.body)
        except FeedError as e:
            raise ConnectorError("bad_feed", str(e)) from e
        cap = get_settings().max_feed_items_per_source
        return title, [RawItem(url=e.link, title=e.title, snippet=e.summary, method="rss",
                               published_at=e.published_at,
                               published_how="feed" if e.published_at else None,
                               publisher=e.author,
                               raw={"feed": feed_url})
                       for e in entries[:cap] if e.link]

    def collect(self, feed_url: str) -> list[RawItem]:  # type: ignore[override]
        return self.fetch(feed_url)[1]

    def health_check(self, feed_url: str | None = None) -> Health:  # type: ignore[override]
        if not feed_url:
            return Health(True)
        try:
            title, items = self.fetch(feed_url)
            return Health(True, f"{len(items)} items")
        except ConnectorError as e:
            return Health(False, str(e))
