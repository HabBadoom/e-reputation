"""Minimal, dependency-free HTML extraction (stdlib html.parser)."""

import json
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from app.collection.dates import parse_absolute

log = logging.getLogger("repintel.html")

_SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "head"}
_BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "article", "section",
          "tr", "blockquote"}
_DATE_META = ("article:published_time", "og:published_time", "datepublished",
              "pubdate", "date", "dc.date", "dc.date.issued", "sailthru.date")
_FEED_TYPES = {"application/rss+xml", "application/atom+xml", "application/feed+json"}


class _Extractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.skip = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.feeds: list[str] = []
        self.jsonld: list[str] = []
        self._in_jsonld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"])
        elif tag == "link" and "alternate" in a.get("rel", "").lower().split() \
                and a.get("type", "").lower() in _FEED_TYPES and a.get("href"):
            self.feeds.append(urljoin(self.base, a["href"]))
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
        elif tag == "time" and a.get("datetime") and "itemprop_time" not in self.meta:
            self.meta["itemprop_time"] = a["datetime"]
        if tag in _SKIP:
            self.skip += 1
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "script":
            self._in_jsonld = False
        if tag in _SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._in_jsonld:
            self.jsonld.append(data)
        elif not self.skip:
            self.parts.append(data)


_APP_ROOT = re.compile(r'<div[^>]+id=["\'](root|app|__next|__nuxt|svelte)["\'][^>]*>\s*</div>',
                       re.I)
_NOSCRIPT_JS = re.compile(r"<noscript[^>]*>[^<]{0,300}(javascript|js)", re.I)


def needs_javascript(html: str, text: str) -> bool:
    """True when the static HTML is an application shell whose text appears only after
    JavaScript runs (the browser fleet can then render it)."""
    if len(text) < 400:
        return True
    return len(text) < 1500 and bool(_APP_ROOT.search(html) or _NOSCRIPT_JS.search(html))


class Page:
    def __init__(self, html: str, url: str) -> None:
        p = _Extractor(url)
        try:
            p.feed(html)
            p.close()
        except Exception as e:  # noqa: BLE001 — malformed HTML: keep what was parsed
            log.debug("html_parse_partial", extra={"url": url, "error": type(e).__name__})
        text = "".join(p.parts)
        self.text = re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
        self.title = " ".join(p.title.split()) or p.meta.get("og:title", "")
        self.meta = p.meta
        self.feed_links = list(dict.fromkeys(p.feeds))
        self._jsonld = p.jsonld
        self.needs_javascript = needs_javascript(html, self.text)

    def published_at(self):  # noqa: ANN201
        for key in _DATE_META:
            if d := parse_absolute(self.meta.get(key)):
                return d
        for block in self._jsonld:
            try:
                data = json.loads(block)
            except ValueError:
                continue
            for item in data if isinstance(data, list) else [data]:
                if not isinstance(item, dict):
                    continue
                if d := parse_absolute(str(item.get("datePublished", ""))):
                    return d
        return parse_absolute(self.meta.get("itemprop_time"))
