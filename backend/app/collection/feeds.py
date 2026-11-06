"""RSS 2.0 / Atom parsing with the standard library.

ElementTree does not resolve external entities, and the bundled expat (>= 2.4) limits
entity-expansion ("billion laughs"); input size is capped by the fetcher.
"""

import xml.etree.ElementTree as ET  # noqa: S405 — see module docstring
from dataclasses import dataclass
from datetime import datetime

from app.collection.dates import parse_absolute
from app.collection.html import Page

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"


@dataclass
class FeedItem:
    title: str
    link: str
    summary: str
    published_at: datetime | None
    author: str | None


class FeedError(ValueError):
    pass


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _plain(html_fragment: str) -> str:
    return Page(html_fragment, "about:blank").text if "<" in html_fragment else html_fragment


def parse_feed(data: bytes) -> tuple[str, list[FeedItem]]:
    """Return (feed title, items)."""
    try:
        root = ET.fromstring(data)  # noqa: S314 — see module docstring
    except ET.ParseError as e:
        raise FeedError(f"not a valid RSS/Atom document: {e}") from e

    items: list[FeedItem] = []
    if root.tag == f"{ATOM}feed":
        title = _text(root.find(f"{ATOM}title"))
        for e in root.findall(f"{ATOM}entry"):
            link = next((lk.get("href", "") for lk in e.findall(f"{ATOM}link")
                         if lk.get("rel", "alternate") == "alternate"), "")
            summary = _text(e.find(f"{ATOM}summary")) or _text(e.find(f"{ATOM}content"))
            when = _text(e.find(f"{ATOM}published")) or _text(e.find(f"{ATOM}updated"))
            items.append(FeedItem(
                _plain(_text(e.find(f"{ATOM}title"))), link.strip(), _plain(summary),
                parse_absolute(when),
                _text(e.find(f"{ATOM}author/{ATOM}name")) or None))
        return title, items

    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedError("unsupported feed format")
    title = _text(channel.find("title"))
    for it in channel.findall("item"):
        summary = _text(it.find("description")) or _text(it.find(f"{CONTENT}encoded"))
        items.append(FeedItem(
            _plain(_text(it.find("title"))), _text(it.find("link")), _plain(summary),
            parse_absolute(_text(it.find("pubDate")) or _text(it.find(f"{DC}date"))),
            _text(it.find("author")) or _text(it.find(f"{DC}creator")) or None))
    return title, items
