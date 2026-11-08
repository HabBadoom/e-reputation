"""Public web pages: verification of search hits, homepage checks and feed discovery.
Honours robots.txt; never logs in, never bypasses paywalls or anti-bot measures."""

from typing import ClassVar

from app.browser.detect import Challenge, Observation, detect
from app.collection.fetcher import FetchError, check_url, safe_get
from app.collection.html import Page
from app.collection.robots import RobotsCache
from app.connectors.base import Connector, ConnectorError, RawItem


class BlockedByRobots(ConnectorError):
    def __init__(self, url: str) -> None:
        super().__init__("blocked_by_robots", url)


class ChallengeDetected(ConnectorError):
    """The site answered with an anti-bot challenge. We stop there: no retry with another
    identity, no solving service, no evasion."""

    def __init__(self, url: str, challenge: Challenge) -> None:
        super().__init__("challenge", f"{challenge.kind} on {url}")
        self.url, self.challenge = url, challenge


class WebPageConnector(Connector):
    key = "web"
    version = "1.0"
    category = "websites"
    access_method = "public_web_page"
    capabilities: ClassVar[frozenset[str]] = frozenset({"collect", "discover"})
    limitations = (
        "Static HTML; pages that need JavaScript are handed to the browser fleet.",
        "Pages behind login, paywall or anti-bot challenges are not accessed.",
        "Only an excerpt around the brand mention is stored.",
    )

    def __init__(self, robots: RobotsCache | None = None) -> None:
        self.robots = robots or RobotsCache()

    def fetch_page(self, url: str) -> Page:
        try:
            check_url(url)        # refuse internal addresses before anything else (SSRF)
        except FetchError as e:
            raise ConnectorError(e.reason, str(e)) from e
        if not self.robots.allowed(url):
            raise BlockedByRobots(url)
        try:
            r = safe_get(url)
        except FetchError as e:
            raise ConnectorError(e.reason, str(e)) from e
        is_html = "html" in r.content_type.lower() or not r.content_type
        html = r.text() if is_html else ""
        page = Page(html, r.url) if is_html else None
        challenge = detect(Observation(
            status=r.status, url=r.url, html=html, title=page.title if page else "",
            text_length=len(page.text) if page else 0, headers=r.headers, rendered=False))
        if challenge is not None:
            raise ChallengeDetected(r.url, challenge)
        if r.status >= 400:
            raise ConnectorError("http_error", f"page returned {r.status}")
        if page is None:
            raise ConnectorError("not_html", r.content_type)
        return page

    def discover(self, homepage: str) -> list[str]:  # type: ignore[override]
        """RSS/Atom feeds advertised by a site's homepage."""
        return self.fetch_page(homepage).feed_links

    def collect(self, url: str) -> list[RawItem]:  # type: ignore[override]
        p = self.fetch_page(url)
        return [RawItem(url=url, title=p.title, snippet=p.text[:500], method="page_fetch",
                        published_at=p.published_at())]
