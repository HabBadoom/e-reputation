# Connectors

Status: Milestone 6. Four connectors are implemented: **Serper** (search), **RSS/Atom** (feeds), **Web page** (verification and feed discovery) and **Browser** (rendering of JavaScript pages). Agent Reach, social networks and review platforms come later.

## Interface (`app/connectors/base.py`)

Each connector declares `key`, `version`, `category`, `access_method`, `capabilities` and `limitations`, and implements the methods from master prompt §7 that make sense for it: `initialize`, `health_check`, `check_permissions`, `get_capabilities`, `get_coverage_status`, `discover`, `search`, `collect`. `describe()` is what `GET /api/v1/connectors` shows to users.

## Compliance gate

`connector_policies` holds one compliance record per connector: legal basis, access method, data categories, restrictions, retention and **review status**. `check_permissions` reads it:

| review_status | development / test | staging / production |
|---|---|---|
| `pending_legal_review` (default) | allowed, flagged in the run summary | **blocked** |
| `approved` | allowed | allowed |
| `rejected` | blocked | blocked |

Tenants can read these records but cannot change them. Approval is done by an operator in the database:

```sql
UPDATE connector_policies SET review_status = 'approved', reviewed_by = 'Name, firm',
       reviewed_at = now() WHERE key = 'rss';
```

Page verification uses the `web` connector and is gated the same way. When it is blocked, search hits are stored as `snippet_only`.

## Serper (`serper`)

- `POST https://google.serper.dev/news` and `/search`, header `X-API-KEY`, body `{q, gl, hl, num, tbs}`.
- **Language → `hl`:** French → `fr`, Arabic and Darija → `ar`, English → `en`. Kabyle has no Google interface language, so its terms are searched without `hl`.
- **Country:** `gl = REPINTEL_SEARCH_COUNTRY` (default `dz`).
- **Time window (`tbs`):** the first run searches the last month; later runs search the last day, or the last week for weekly projects.
- **Query:** one boolean query per entity group (`"a" OR "b" -"excluded"`), from the project's query plan.
- **Cost control:** each run is capped at `REPINTEL_MAX_SEARCH_QUERIES_PER_RUN` queries. The count per run is groups × languages × endpoints. A 401, 403 or 429 stops Serper for the rest of the run, and the run records why.
- **Legal:** results originate from Google via a third party. Google's terms do not authorise this, and there is US litigation on SERP scraping (Google v. SerpApi; most claims were dismissed in July 2026 and Google amended its complaint). The record stays at `pending_legal_review` until counsel approves it for production.

## RSS / Atom (`rss`)

Feeds come from the tenant's source registry, either added by a user or discovered on a website and then enabled by a user. Each feed is fetched once per run, and items are matched against the plan's terms. Items are labelled `feed_item`, with the feed's date when present. A feed that fails three times in a row is marked `CONNECTOR_ERROR`.

## Web page (`web`)

Used to:

1. **Verify search hits:** fetch the page, check that the brand really appears, store an excerpt around it, and read the publication date from page metadata (`article:published_time`, JSON-LD `datePublished`, `<time>`).
2. **Check a website and discover its RSS feeds** (`<link rel="alternate" type="application/rss+xml">`). Discovered feeds are created **disabled**.

It honours `robots.txt` (RFC 9309, token `RepIntelBot`). It never logs in, never bypasses paywalls or CAPTCHAs, and runs no JavaScript. Every response goes through the challenge detector; a challenge stops there (see `CAPTCHA_INTERVENTIONS.md`).

## Browser (`browser`)

Headless Chromium rendering of public pages (`BROWSER_FLEET.md`): used when a page's static HTML has no text without JavaScript, for sources set to "Always browser", and for "Check with browser". Same rules as the web reader (robots.txt, honest user agent, no login), plus a fresh isolated context per visit. Its compliance record is `pending_legal_review` and therefore blocked in production until approved.

## Verification statuses (per mention)

| Status | Meaning |
|---|---|
| `verified_on_page` | The page was fetched and contains the term; the excerpt comes from the page |
| `feed_item` | From an RSS item whose title or summary contains the term |
| `snippet_only` | Not checked (page budget used up, or verification disabled or blocked) |
| `not_found_on_page` | The page was fetched but the term was not found (possibly JavaScript-rendered or removed) |
| `page_unavailable` | Fetch failed (HTTP error, timeout, internal address refused) |
| `blocked_by_robots` | `robots.txt` disallows our crawler; only the provider's snippet is kept |
| `pending_render` | Handed to the browser fleet; becomes `verified_on_page` / `not_found_on_page` after rendering |
| `blocked_by_challenge` | The site answered with a CAPTCHA / anti-bot check; the snippet or feed text is kept, nothing bypassed |

## Source coverage states

| State | Set when |
|---|---|
| `AVAILABLE` | A page or feed was fetched successfully |
| `PARTIALLY_AVAILABLE` | Known only from search results so far |
| `RESTRICTED` | `robots.txt` disallows us |
| `UNAVAILABLE` | A manual check failed (unreachable, refused address) |
| `CONNECTOR_ERROR` | A feed failed three times in a row, or isn't a valid feed |
| `PENDING_AUTHORIZATION` | Reserved for connectors needing customer credentials (later milestones) |

"Monitored" means enabled and in `AVAILABLE` or `PARTIALLY_AVAILABLE`. The dashboard counts monitored sources separately from registered ones.

## Data minimisation

The platform stores the URL, title, the provider's snippet, an excerpt of about 300 characters around the match, the date and the provenance. **Full pages and full articles are never stored.** Retention per connector is recorded in `connector_policies.retention_days` (365 by default); the deletion job comes with Milestone 8.

## Agent Reach (not yet integrated)

Before writing its adapter: review the repository and its licence, check commercial-use terms, list the authentication each sub-tool needs, and test each sub-tool. It will then be wrapped behind an allowlisted subprocess interface: fixed subcommands, validated arguments, timeouts and memory caps, no shell, and per-tenant credentials from the vault. It will be one provider among several and will never orchestrate the platform.
