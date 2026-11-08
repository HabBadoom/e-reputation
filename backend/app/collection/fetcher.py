"""HTTP fetching for untrusted URLs (search results, feeds, user-added sources).

SSRF protection: only http/https on ports 80/443; every hop of a redirect chain is re-checked;
the hostname must resolve exclusively to public (globally routable) addresses, so cloud
metadata endpoints, localhost, RFC 1918, link-local and similar are refused. Responses are
size-capped and time-limited.

Residual risk (documented in SECURITY.md): DNS can change between the check and the
connection (rebinding). Production workers must additionally run behind an egress firewall
that blocks private ranges (Milestone 6 network policy).
"""

import ipaddress
import socket
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import get_settings

MAX_REDIRECTS = 5


class FetchError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)   # lower-case names

    def text(self) -> str:
        charset = "utf-8"
        for part in self.content_type.split(";"):
            if part.strip().lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip('"') or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def resolve(host: str) -> list[str]:
    """All addresses the host resolves to (patched in tests)."""
    try:
        return list({ai[4][0] for ai in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except (socket.gaierror, UnicodeError) as e:
        raise FetchError("dns_failure", host) from e


def check_url(url: str) -> None:
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError as e:
        raise FetchError("bad_url", url) from e
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("bad_url", url)
    if port not in (None, 80, 443):
        raise FetchError("blocked_port", str(port))
    if p.username or p.password:
        raise FetchError("bad_url", "credentials in URL")
    host = p.hostname
    try:
        literal = ipaddress.ip_address(host)
        addrs = [str(literal)]
    except ValueError:
        addrs = resolve(host)
    if not addrs:
        raise FetchError("dns_failure", host)
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise FetchError("blocked_address", f"{host} -> {ip}")


@lru_cache
def http_client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(timeout=s.fetch_timeout_seconds, follow_redirects=False,
                        headers={"user-agent": s.crawler_user_agent,
                                 "accept-language": "fr,ar;q=0.9,en;q=0.5"})


def safe_get(url: str, *, max_bytes: int | None = None,
             accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5"
             ) -> FetchResult:
    limit = max_bytes or get_settings().fetch_max_bytes
    client = http_client()
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        check_url(current)
        try:
            with client.stream("GET", current, headers={"accept": accept}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > limit:
                    raise FetchError("too_large", declared)
                chunks, size = [], 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise FetchError("too_large", f">{limit}")
                    chunks.append(chunk)
                return FetchResult(str(r.url), r.status_code,
                                   r.headers.get("content-type", ""), b"".join(chunks),
                                   {k.lower(): v for k, v in r.headers.items()})
        except httpx.TimeoutException as e:
            raise FetchError("timeout", current) from e
        except httpx.HTTPError as e:
            raise FetchError("network_error", type(e).__name__) from e
    raise FetchError("too_many_redirects", url)


def safe_post(url: str, body: bytes, headers: dict[str, str], *,
              max_response_bytes: int = 65536) -> FetchResult:
    """POST to an untrusted, user-configured URL (webhooks). HTTPS only, same address checks
    as safe_get, redirects are NOT followed (a 3xx is returned as-is and treated as a failure
    by callers), the response body is capped."""
    if urlsplit(url).scheme != "https":
        raise FetchError("bad_url", "https required")
    check_url(url)
    try:
        with http_client().stream("POST", url, content=body, headers=headers) as r:
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > max_response_bytes:
                    break
                chunks.append(chunk)
            return FetchResult(str(r.url), r.status_code, r.headers.get("content-type", ""),
                               b"".join(chunks)[:max_response_bytes])
    except httpx.TimeoutException as e:
        raise FetchError("timeout", url) from e
    except httpx.HTTPError as e:
        raise FetchError("network_error", type(e).__name__) from e
