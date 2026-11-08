"""Headless Chromium rendering of public pages (browser fleet, Milestone 6)."""

from typing import ClassVar

from app.connectors.base import Connector


class BrowserConnector(Connector):
    key = "browser"
    version = "1.0"
    category = "websites"
    access_method = "public_web_page_rendered"
    capabilities: ClassVar[frozenset[str]] = frozenset({"collect", "verify"})
    limitations = (
        "Used only when a page's static HTML does not contain its text (JavaScript sites), "
        "or for sources set to browser rendering.",
        "Honest user agent, robots.txt honoured, one page at a time per domain.",
        "Never logs in, never solves or bypasses CAPTCHAs or anti-bot challenges: a challenge "
        "stops the visit, the domain is paused and the source may be marked restricted.",
        "Each visit uses a fresh isolated context; cookies and storage are discarded.",
    )
