"""Serper.dev: Google web and news results (POST https://google.serper.dev/{search,news}).

Results come from Google Search via a third party; see connector_policies('serper') for the
legal status. Snippets are leads, not evidence: the pipeline verifies hits on the real page.
"""

from functools import lru_cache
from typing import Any, ClassVar

import httpx

from app.collection.dates import parse_provider_date
from app.connectors.base import Connector, ConnectorError, Health, RawItem
from app.core.config import get_settings

# Project languages → Google interface language (`hl`). Kabyle has no Google UI language.
HL = {"fr": "fr", "ar": "ar", "ar-DZ": "ar", "en": "en"}


@lru_cache
def serper_client() -> httpx.Client:
    return httpx.Client(timeout=20.0)


class SerperConnector(Connector):
    key = "serper"
    version = "1.0"
    category = "search_engines"
    access_method = "third_party_search_api"
    capabilities: ClassVar[frozenset[str]] = frozenset({"search", "news", "discover"})
    limitations = (
        "Only what Google indexes and returns in the first results page per query.",
        "Snippets and dates come from Google; relative dates ('3 hours ago') are approximate.",
        "No social-network content behind logins.",
        "Each query costs credits; queries per run are capped.",
    )

    def __init__(self) -> None:
        s = get_settings()
        self.api_key = s.serper_api_key.get_secret_value()
        self.base = s.serper_base_url.rstrip("/")
        self.country = s.search_country
        self.num = s.search_results_per_query

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def initialize(self) -> None:
        if not self.configured:
            raise ConnectorError("not_configured", "REPINTEL_SERPER_API_KEY is not set", fatal=True)

    def health_check(self) -> Health:
        return Health(self.configured, "" if self.configured else "API key not configured")

    def search(self, query: str, *, hl: str | None, endpoint: str = "search",
               tbs: str | None = None) -> list[RawItem]:
        body: dict[str, Any] = {"q": query, "gl": self.country, "num": self.num}
        if hl:
            body["hl"] = hl
        if tbs:
            body["tbs"] = tbs
        try:
            r = serper_client().post(f"{self.base}/{endpoint}", json=body,
                                     headers={"X-API-KEY": self.api_key})
        except httpx.HTTPError as e:
            raise ConnectorError("network_error", type(e).__name__) from e
        if r.status_code in (401, 403):
            raise ConnectorError("auth_failed", "Serper rejected the API key", fatal=True)
        if r.status_code == 429:
            raise ConnectorError("rate_limited", "Serper quota or rate limit reached", fatal=True)
        if r.status_code >= 400:
            raise ConnectorError("http_error", f"Serper returned {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ConnectorError("bad_response", "invalid JSON") from e

        key, method = ("news", "news_api") if endpoint == "news" else ("organic", "search_api")
        items = []
        for it in data.get(key) or []:
            link = it.get("link")
            if not isinstance(link, str) or not link:
                continue
            published, how = parse_provider_date(it.get("date"))
            items.append(RawItem(
                url=link, title=str(it.get("title") or ""), snippet=str(it.get("snippet") or ""),
                method=method, published_raw=it.get("date"), published_at=published,
                published_how=how,
                publisher=it.get("source"),
                raw={k: it.get(k) for k in ("position", "date", "source") if it.get(k) is not None}
                | {"query": query, "hl": hl, "endpoint": endpoint},
            ))
        return items
