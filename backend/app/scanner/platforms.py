"""Which social network a URL belongs to (collection metadata from the host name only)."""

from urllib.parse import urlsplit

_HOSTS = {
    "facebook": ("facebook.com", "fb.com", "fb.watch", "messenger.com"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "linkedin": ("linkedin.com", "lnkd.in"),
    "x": ("x.com", "twitter.com", "t.co"),
}


def platform_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host:
        return None
    for platform, domains in _HOSTS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return platform
    return "web"
