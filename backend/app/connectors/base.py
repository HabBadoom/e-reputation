"""Unified connector interface (master prompt §7).

Every connector declares what it can do, how it accesses data, and its limitations. Whether
it may run is decided by `check_permissions` from the connector's compliance record
(`connector_policies`), never by the connector itself or by an AI agent.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings

COVERAGE_STATES = ("AVAILABLE", "PARTIALLY_AVAILABLE", "PENDING_AUTHORIZATION", "RESTRICTED",
                   "UNAVAILABLE", "CONNECTOR_ERROR")


class ConnectorError(Exception):
    def __init__(self, reason: str, detail: str = "", *, fatal: bool = False) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.fatal = fatal   # stop using this connector for the rest of the run


@dataclass
class RawItem:
    """One piece of content as returned by a connector, before normalisation."""
    url: str
    title: str
    snippet: str
    method: str                       # search_api | news_api | rss
    published_raw: str | None = None
    published_at: datetime | None = None
    published_how: str | None = None  # provider | provider_relative (approximate) | feed
    publisher: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Health:
    ok: bool
    detail: str = ""


@dataclass
class PermissionDecision:
    allowed: bool
    review_status: str
    reason: str = ""


class Connector:
    key: ClassVar[str]
    version: ClassVar[str]
    category: ClassVar[str]            # §7 category
    access_method: ClassVar[str]
    capabilities: ClassVar[frozenset[str]]
    limitations: ClassVar[tuple[str, ...]]

    def initialize(self) -> None:  # noqa: B027 — optional hook
        """Validate configuration. Raise ConnectorError if unusable."""

    def health_check(self) -> Health:
        return Health(True)

    def check_permissions(self, db: Session) -> PermissionDecision:
        row = db.execute(text("SELECT review_status FROM connector_policies WHERE key = :k"),
                         {"k": self.key}).one_or_none()
        if row is None:
            return PermissionDecision(False, "missing", "no compliance record for connector")
        if row.review_status == "approved":
            return PermissionDecision(True, row.review_status)
        if row.review_status == "rejected":
            return PermissionDecision(False, row.review_status, "connector rejected by review")
        if get_settings().is_production:
            return PermissionDecision(False, row.review_status,
                                      "legal/terms review pending: blocked in production")
        return PermissionDecision(True, row.review_status,
                                  "legal/terms review pending: allowed outside production")

    def get_capabilities(self) -> frozenset[str]:
        return self.capabilities

    def get_coverage_status(self) -> str:
        return "AVAILABLE"

    def discover(self, *args: Any, **kwargs: Any) -> list[str]:
        return []

    def search(self, *args: Any, **kwargs: Any) -> list[RawItem]:
        raise NotImplementedError(f"{self.key} does not support search")

    def collect(self, *args: Any, **kwargs: Any) -> list[RawItem]:
        raise NotImplementedError(f"{self.key} does not support collect")

    def describe(self) -> dict[str, Any]:
        return {"key": self.key, "version": self.version, "category": self.category,
                "access_method": self.access_method, "capabilities": sorted(self.capabilities),
                "limitations": list(self.limitations)}
