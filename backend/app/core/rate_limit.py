"""Fixed-window rate limiting. Redis in all real environments; in-memory only when env=test."""

import logging
import threading
import time
from typing import Protocol

import redis

from app.core.config import get_settings
from app.core.errors import RateLimited

log = logging.getLogger("repintel.ratelimit")


class Limiter(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> None: ...


class RedisLimiter:
    def __init__(self, url: str):
        self._r = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        bucket = f"rl:{key}:{int(time.time() // window_seconds)}"
        try:
            pipe = self._r.pipeline()
            pipe.incr(bucket)
            pipe.expire(bucket, window_seconds)
            count, _ = pipe.execute()
        except redis.RedisError:
            # Fail closed for auth endpoints: an attacker must not be able to disable
            # limiting by degrading Redis.
            log.error("rate_limiter_unavailable", extra={"key": key})
            raise RateLimited("Rate limiter unavailable, try again shortly") from None
        if int(count) > limit:
            raise RateLimited("Too many requests")

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except redis.RedisError:
            return False


class MemoryLimiter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        bucket = f"{key}:{int(time.time() // window_seconds)}"
        with self._lock:
            self._counts[bucket] = self._counts.get(bucket, 0) + 1
            if self._counts[bucket] > limit:
                raise RateLimited("Too many requests")

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = MemoryLimiter() if s.env == "test" else RedisLimiter(s.redis_url)
    return _limiter
