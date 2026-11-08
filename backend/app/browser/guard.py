"""Browser Permission Guard: a mission runs only with a valid authorization record.

Checked when the mission is created (VALIDATING) and again just before navigation (the access
must still be authorized when it actually happens): connector compliance status, URL safety
(SSRF), domain policy, robots.txt, domain cool-down after challenges, plan quota.
"""

import ipaddress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.collection.fetcher import FetchError, check_url
from app.collection.robots import RobotsCache
from app.collection.urls import domain_of
from app.connectors.browser import BrowserConnector


@dataclass
class Authorization:
    ok: bool
    reason: str | None
    record: dict[str, Any] = field(default_factory=dict)


@dataclass
class DomainPolicy:
    allowed: bool
    max_concurrency: int
    min_interval_seconds: int
    timeout_seconds: int
    block_resources: list[str]


def domain_policy(db: Session, domain: str) -> DomainPolicy:
    row = db.execute(text("""
        SELECT allowed, max_concurrency, min_interval_seconds, timeout_seconds, block_resources
        FROM browser_domain_policies WHERE domain IN (:d, '*') ORDER BY (domain = '*') LIMIT 1
    """), {"d": domain}).one()
    return DomainPolicy(row.allowed, row.max_concurrency, row.min_interval_seconds,
                        row.timeout_seconds, list(row.block_resources))


def url_allowed(url: str) -> bool:
    """Every request the browser makes (page, redirects, scripts, XHR) goes through this.
    Same rules as the HTTP fetcher: http(s) on 80/443 to public addresses only.
    Tests replace it to reach a local server."""
    try:
        check_url(url)
    except FetchError:
        return False
    return True


def is_public_literal(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def cooldown_until(db: Session, domain: str) -> datetime | None:
    return db.execute(text("SELECT browser_domain_cooldown(:d)"), {"d": domain}).scalar_one()


def authorize(db: Session, tenant_id: Any, url: str, *, robots: RobotsCache | None = None,
              count_quota: bool = True) -> Authorization:
    from app.modules.billing.entitlements import plan_info

    rec: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(), "url": url}

    def deny(reason: str) -> Authorization:
        rec["decision"] = "denied"
        rec["reason"] = reason
        return Authorization(False, reason, rec)

    perm = BrowserConnector().check_permissions(db)
    rec["connector"] = {"key": "browser", "review_status": perm.review_status,
                        "allowed": perm.allowed}
    if not perm.allowed:
        return deny("connector_not_authorized")
    if urlsplit(url).scheme not in ("http", "https"):
        return deny("bad_url")
    if not url_allowed(url):
        return deny("blocked_address")
    rec["address"] = "public"
    domain = domain_of(url)
    pol = domain_policy(db, domain)
    rec["domain_policy"] = {"allowed": pol.allowed, "max_concurrency": pol.max_concurrency,
                            "min_interval_seconds": pol.min_interval_seconds}
    if not pol.allowed:
        return deny("domain_not_allowed")
    if not (robots or RobotsCache()).allowed(url):
        return deny("blocked_by_robots")
    rec["robots"] = "allowed"
    if (until := cooldown_until(db, domain)) is not None:
        rec["cooldown_until"] = until.isoformat()
        return deny("domain_cooling_down")
    if count_quota:
        limit = plan_info(db, tenant_id).limits.get("max_browser_missions_per_day")
        used = db.execute(text("SELECT browser_missions_today()")).scalar_one()
        rec["quota"] = {"used_24h": used, "limit": limit}
        if limit is not None and used > limit:     # the new mission is already counted
            return deny("quota_exhausted")
    rec["decision"] = "authorized"
    return Authorization(True, None, rec)
