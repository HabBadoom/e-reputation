"""robots.txt (RFC 9309) with a per-run cache.

4xx on robots.txt → no restrictions; 5xx / network failure → treat everything as disallowed.
"""

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from app.collection.fetcher import FetchError, safe_get
from app.core.config import get_settings


class RobotsCache:
    def __init__(self) -> None:
        self._cache: dict[str, RobotFileParser | bool] = {}

    def _load(self, origin: str) -> RobotFileParser | bool:
        try:
            r = safe_get(f"{origin}/robots.txt", max_bytes=512 * 1024, accept="text/plain")
        except FetchError:
            return False                       # unreachable → disallow
        if 400 <= r.status < 500:
            return True                        # no robots.txt → allow
        if r.status >= 500:
            return False
        rp = RobotFileParser()
        rp.parse(r.text().splitlines())
        return rp

    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._cache:
            self._cache[origin] = self._load(origin)
        entry = self._cache[origin]
        if isinstance(entry, bool):
            return entry
        return entry.can_fetch(get_settings().crawler_robots_token, url)
