"""Common shape of an item returned by a social provider, before it becomes a mention.

Providers differ in what they return; each adapter fills what it has and leaves the rest
empty. Nothing is invented: no date, author or count that the provider did not give.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

PLATFORM_DOMAINS = {"facebook": "facebook.com", "tiktok": "tiktok.com",
                    "linkedin": "linkedin.com", "reddit": "reddit.com",
                    "youtube": "youtube.com", "instagram": "instagram.com", "x": "x.com"}


@dataclass
class SocialItem:
    platform: str
    external_id: str
    url: str                                   # permalink when the provider gives one
    text: str
    content_type: str                          # post | video | comment | reply
    title: str = ""
    author_name: str | None = None
    author_id: str | None = None
    author_url: str | None = None
    published_at: datetime | None = None
    engagement: dict[str, int] = field(default_factory=dict)
    comments_count: int | None = None
    container: str | None = None               # page, group, channel, community name
    parent_external_id: str | None = None      # comment → post; reply → comment
    parent_url: str | None = None              # the post a comment belongs to
    extra: dict[str, Any] = field(default_factory=dict)
    children: list["SocialItem"] = field(default_factory=list)

    def walk(self) -> list["SocialItem"]:
        out = [self]
        for c in self.children:
            out += c.walk()
        return out


def get(d: Any, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def pick(d: Any, *paths: str) -> Any:
    """First value that is present and not empty among the given (dotted) paths."""
    for p in paths:
        v = get(d, p)
        if v not in (None, "", [], {}):
            return v
    return None


def text_of(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


def to_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace(" ", "")
        if s.isdigit():
            return int(s)
    return None


def to_datetime(v: Any) -> datetime | None:
    """ISO 8601 text, epoch seconds or milliseconds; anything else → None (never guessed)."""
    if isinstance(v, dict):
        v = pick(v, "timestamp", "date", "iso")
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int | float):
        ts = float(v)
        if ts > 1e12:
            ts /= 1000.0
        if ts < 946684800:          # before 2000: not a plausible publication time
            return None
        return datetime.fromtimestamp(ts, UTC)
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit():
            return to_datetime(int(s))
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return None


def counts(d: Any, **paths: tuple[str, ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, ps in paths.items():
        n = to_int(pick(d, *ps))
        if n is not None and n >= 0:
            out[name] = n
    return out
