"""URL canonicalisation for de-duplication. Conservative: only removes parts that never
identify content (tracking parameters, fragments, default ports, trailing slash)."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "mc_", "pk_", "hsa_")
_TRACKING_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "ref_src",
                  "_hsenc", "_hsmi", "mkt_tok", "spm", "at_medium", "at_campaign", "amp"}


def is_http_url(url: str) -> bool:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.hostname)


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def canonicalize(url: str) -> str:
    p = urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    port = p.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = urlencode(sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES) and k.lower() not in _TRACKING_KEYS
    ))
    # http and https variants of the same page are the same content
    return urlunsplit(("https" if scheme in ("http", "https") else scheme, netloc, path, query, ""))
