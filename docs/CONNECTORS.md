# Connectors — design (Milestone 3, not implemented)

## Interface

```python
class Connector(Protocol):
    key: str; version: str; category: ConnectorCategory
    def initialize(self, cfg: ConnectorConfig) -> None
    def health_check(self) -> Health
    def check_permissions(self, tenant: TenantId, source: Source) -> PermissionDecision
    def get_capabilities(self) -> set[Capability]          # search, collect, discover, comments…
    def get_coverage_status(self, source: Source) -> CoverageStatus
    def discover(self, q: DiscoveryQuery) -> Iterator[SourceCandidate]
    def search(self, q: SearchQuery) -> Iterator[RawItem]
    def collect(self, ref: ItemRef) -> RawItem
    def normalize(self, raw: RawItem) -> NormalizedMention   # never overwrites original_text
```

Categories: `official_apis, search_engines, news, rss, social_media, review_platforms, forums, video_audio, ecommerce, business_directories, websites, agent_reach`.
Coverage states: `AVAILABLE, PARTIALLY_AVAILABLE, PENDING_AUTHORIZATION, RESTRICTED, UNAVAILABLE, CONNECTOR_ERROR`.

## Rules

- A deterministic **policy engine** (not an LLM) decides `check_permissions` from `source_access_status`: legal basis, terms review (who, when), access method, data categories, retention, restrictions. No record ⇒ `PENDING_AUTHORIZATION` ⇒ no collection.
- No official API ≠ browser automation allowed. No bypassing logins, paywalls or anti-bot measures.
- All HTTP goes through one **SSRF-safe fetcher**: resolve-then-connect to public IPs only, block RFC1918/link-local/metadata ranges, cap redirects/size/time, record final URL.
- Search snippets are leads, not evidence: content is verified against the fetched page when accessible; otherwise marked `unverified`.

## First connectors (M3)

RSS/Atom, generic website reader (robots.txt honoured), one commercial search API (provider TBD — open question 1), Algerian news sites with RSS.

## Agent Reach

Before writing the adapter: review the repository, licence and commercial-use terms, auth requirements, and test each sub-tool. Then wrap it behind an allowlisted subprocess interface (fixed subcommands, validated args, timeouts, memory caps, no shell, per-tenant credentials from the vault). It is one provider among several and never orchestrates the platform. Findings go in this file.
