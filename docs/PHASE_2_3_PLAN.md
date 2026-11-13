# Phase 2 & 3 — Implementation plan

Status: **proposal, awaiting decisions in §9** · Based on: repository at `be5d015` (v0.9.0),
master prompt "Phase 2: Advanced Connectivity & Phase 3: Reputation Intelligence",
provider documentation and secondary sources checked on 2026-11-13 (§10).

This plan follows the brief's rule: inspect first, reuse what exists, add only what is
genuinely missing, and never present a connector as working before it has been tested against
an authorised provider environment.

---

## 1. What the provider landscape actually allows (November 2026)

The brief asks for seven social platforms and five review platforms. Their official APIs differ
far more than the brief's uniform method list suggests. For a **commercial monitoring SaaS**:

| Platform | What an official API gives us | What it does **not** give | Access requirements | Plan |
|---|---|---|---|---|
| **YouTube** (Data API v3) | Search public videos by keyword, video metadata and statistics, public comment threads and replies, channel info; reply to comments on a channel the client owns | Watch-time / reach of others' videos; deleted comments | Google Cloud project + API key (public data) or OAuth (own channel). Default quota 10 000 units/day; `search.list` has its own 100 calls/day bucket. YouTube API Services policies limit storage of API data (refresh or delete — to be confirmed at implementation) | **Build** — only social platform with real keyword search over public content |
| **Facebook** (Graph API) | Posts, comments, reactions and ratings **on Pages the client manages** (OAuth by the client); reply to comments on those Pages | **Keyword search of public posts (none exists)**, groups, personal profiles, other businesses' Pages (only through the reviewed "Page Public Content Access" feature, granted case by case) | Meta developer app, Business Verification, App Review for each permission | **Build for own Pages**; apply for Page Public Content Access (competitor Pages) — status shown as "requires authorisation" until granted |
| **Instagram** (Graph API) | Own business/creator account media, comments, mentions; **hashtag search** (30 unique hashtags per 7 days per account, recent = last 24 h, ≤ 250 posts, no author identity); **business discovery** of other business/creator accounts (profile + recent media, current counts only) | Personal accounts, stories, keyword search, author identity on hashtag results | Same Meta app; Instagram Public Content Access feature for hashtag search | **Build** (own account, hashtags, business discovery) |
| **LinkedIn** (Community Management API) | Posts, comments, reactions on **organisation pages the client administers** | Search of public posts, other companies' pages, member profiles | App + Community Management API business review (legal entity, use case) | **Build for own pages**, after vetting |
| **X** | Recent search and post lookup with metrics — pay-per-use ($0.005 per post read, cap 3 M reads/month on self-serve) | Nothing structural, but **every post read is billed** | Developer account + prepaid credits | **Decision needed (§9)** — build with hard spending caps per tenant, or skip |
| **Reddit** | — | — | Reddit requires explicit approval for all Data API access; per its 8 Oct 2026 changelog, **new public API requests ended 31 Oct 2026** and commercial use requires a contract (secondary source — official pages not reachable from here, to verify) | **Not buildable now.** Shown as "unavailable — requires a commercial agreement with Reddit" |
| **TikTok** | Display API: the client's own account videos and basic stats | **Any public content search**; the Research API is closed to commercial companies and to Algeria | TikTok developer app | **Own account only** (low value) — proposed: shown as "unavailable for monitoring" unless you want the own-account connector (§9) |
| **Google Business Profile** | All reviews of **locations the client manages**, reply / delete reply | Competitors' reviews | GBP API access request (manual approval, profile ≥ 60 days, website, use case) + client OAuth | **Build** — key for Algeria (Google Maps reviews), with human-approved replies |
| **Google Places API** | ≤ 5 reviews per place, rating, review count, for **any** place (competitors) | Full review history; Google's terms restrict caching of content | API key, billed | **Build as "competitor sample"**, clearly labelled partial; storage rules checked before enabling |
| **Google Play** | Reviews **of the client's own apps** created/modified in the last 7 days (with text only); reply (350 chars) | Older reviews (CSV export only), competitor apps, star-only ratings | Client's service account with "Reply to reviews" | **Build** (incremental sync every few hours) + CSV import for history |
| **App Store** | App Store Connect API: reviews of the client's own apps, reply | Competitor apps (only a capped public RSS feed whose terms restrict automated access) | Client's App Store Connect API key | **Build** own apps; public RSS feed only after legal review |
| **Trustpilot** | Reviews and replies for the client's own business unit | Other businesses (Data Solutions is a separate commercial product) | Client must have Trustpilot's API module (paid add-on / Enterprise) | **Build** if a client uses Trustpilot (little used in Algeria) — §9 |
| **Tripadvisor** | ≤ 5 reviews per location (Content API) | Full reviews; use limited to consumer-facing sites | Launch review | **Not eligible** for a B2B monitoring tool as far as published terms say → "requires partner access" |

**Consequence for an Algerian client.** Most public conversation in Algeria is on Facebook
(public Pages *and* groups). The official API never exposes groups or a public search. RepIntel
will cover: the client's own Pages and accounts completely, YouTube widely, Instagram hashtags
and business accounts, competitor Pages only if Meta grants Page Public Content Access, and the
rest of the public web as today (search engines, news, RSS, browser fleet). The Conversation
Coverage module (§4, Module 5) exists precisely to make this gap visible instead of hiding it.

---

## 2. Inventory: what already exists and how each module reuses it

| Brief module | Existing (reused) | Missing (to build) |
|---|---|---|
| 1 Social connectors | `Connector` base (`app/connectors/base.py`: capabilities, limitations, coverage states, compliance gate via `connector_policies`); `sources` table; Fernet encryption (`REPINTEL_ENCRYPTION_KEYS`, used for MFA seeds); SSRF-safe fetcher; Sources page | Account-based connectors (OAuth, tokens, renewal), per-platform implementations, credential storage, account/page selection, source↔brand/project association, per-source frequency |
| 2 Reviews | `mentions` + `mention_analysis` (sentiment, emotions, topics, intents, sarcasm, dialect — Darija aware); analytics queries; AI response drafting with approval (`use_response`) | Review attributes (rating, title, reply state, history), review analytics, location/product dimensions, official reply submission after approval |
| 3 Unified collection | `collection/pipeline.py` (normalisation, canonical URL, `content_hash` dedup, entity matching, language detection, provenance fields), Postgres job outbox with retries and `dead` status (DLQ), NLP job, alert evaluation job, monitoring runs | Per-source incremental sync with cursors, `external_id` idempotency, comments as child mentions, metric snapshots, unified scheduler for account sources |
| 4 Quotas & reliability | Redis limiter (`hit(key, limit, window)`), plan entitlements and monthly quotas (`usage_events`), browser domain cooldown / back-off, `sources.consecutive_errors` | Per-connector token buckets (minute/hour/day), `Retry-After`, circuit breaker, source suspension/recovery, error log with categories, health dashboard, reliability score |
| 5 Coverage | `sources.coverage_status` (AVAILABLE … CONNECTOR_ERROR), Collection agent's coverage summary | Coverage index (0–100) with methodology, confidence and limitations; snapshots; dashboard |
| 6 Influencers & communities | Mentions store publisher/domain only | Author entity (platform public IDs), influence score, interaction graph, community detection, map |
| 7 Anomalies & coordination | `analytics/stats.py` (Poisson baseline, spikes, emerging topics), `trend_events`, `content_hash` | Near-duplicate clustering, timing/author-concentration/URL signals, labelled anomaly events with evidence and alternative explanations |
| 8 Crisis management | `crisis_events` (auto detection: surge of negatives, propagation, escalation intents; severities INFO…CRITICAL), crisis alerts, Crisis agent | Workspace: lifecycle (9 statuses), 4 configurable levels, manager/team, comments, timeline, actions with deadlines, dashboard |
| 9 Communication plans | Response agent (brand tone, guidelines, injection defences, never invents facts), `approval_requests` | Versioned plans, message types (holding statement, FAQ, press…), review workflow (draft → archived), simulation mode |
| 10 Responses & decisions | `ai_actions` / `approval_requests` audit, `audit_logs` | Unified response register, decision register, lessons-learned analytics and report |
| Agents | 13 agents + orchestrator, tool allowlists, permission ceiling, compliance policy, injection suite | New tools and outputs on existing agents (§6); no new framework |

Nothing in the repository duplicates these modules today; the risk is the opposite (creating
parallel tables). The decisions below extend existing entities wherever possible.

---

## 3. Data model (logical entity → physical decision)

All tenant tables: `tenant_id`, RLS enabled and **forced**, composite FKs `(tenant_id, id)`,
indexes on query paths, reversible Alembic migrations, SQL isolation suite per migration.

| Logical entity (brief) | Decision |
|---|---|
| SocialSource, ReviewSource | **Extend `sources`**: `kind` adds `social_account`, `review_profile`; new columns `platform`, `external_id`, `handle`, `ownership` (`owned` / `public`), `frequency_minutes`, `sync_cursor` (jsonb), `status` (`active` / `paused` / `needs_reauth` / `suspended` / `disabled`), `suspended_until`. Association with brands/projects via new `source_links (source_id, brand_id, project_id)` |
| SourceCredential | **New `source_credentials`**: one per connected account; tokens encrypted with the existing Fernet keyring (rotation supported), scopes granted, expiry, last refresh, revoked flag. Never selected by API serializers; readable only by a `SECURITY DEFINER` function used by workers |
| SourceSynchronization, CollectionJob | **New `source_syncs`** (one per sync: mode incremental/historical, cursor before/after, items fetched/new/updated, API calls, quota units, status, error category). Jobs stay in the **existing `jobs` outbox** (`dead` = dead-letter) |
| CollectionError | **New `collection_errors`**: category (`auth`, `permission`, `rate_limit`, `quota`, `not_found`, `provider_5xx`, `network`, `parse`, `policy`), redacted message, HTTP status, provider error code, never raw bodies or tokens |
| SourceQuota | Config **`connector_quotas`** (global per connector: per-minute/hour/day, provider units) + per-tenant overrides; live counters in Redis token buckets; consumption persisted daily |
| SourceHealth | Computed from `source_syncs` + `collection_errors`; daily **`source_health_daily`** snapshot for trends; reliability score formula in §4 |
| Mention | **Extend `mentions`**: `content_type` (`article`, `post`, `comment`, `reply`, `video`, `review`), `platform`, `external_id` with **unique (tenant_id, platform, external_id)** for idempotency, `parent_mention_id`, `author_id`, `engagement` (jsonb, as returned), `processing_status`. Existing `content_hash` dedup kept for web content |
| Review attributes | **New `mention_reviews`** (1:1 extension): rating, title, location/product refs, reply status, reply text/at, provider review URL. Original text never overwritten (already a rule: `original_text` vs `normalized_text`) |
| Locations, products | Reuse existing **`products`**; **new `brand_locations`** (name, city/wilaya, provider place/location IDs) for GBP/Places and location comparisons |
| MentionSnapshot | **New `mention_metric_snapshots`** (mention, captured_at, metrics jsonb) — only when the provider returns metrics; labelled "interactions", never "people" |
| ConversationCoverage | **New `coverage_snapshots`** (tenant, brand, period, components, index, confidence, limitations) |
| InfluencerProfile | **New `authors`** (platform, platform public ID, handle, display name, profile URL, audience indicators as returned, first/last seen) + **`author_stats`** per brand/period. No inference of sensitive traits; no cross-platform merge unless the same verified handle/URL is published by the author |
| Community, CommunityMembership | **New `communities`**, **`community_members`**, **`interaction_edges`** (reply / mention / quote edges from API data only) |
| AnomalyEvent | **New `anomaly_events`** (label from the brief's six, signals, baseline, observed, confidence, evidence mention IDs, alternative explanations, reviewed_by). `trend_events` kept for topics |
| Crisis | **Extend `crisis_events`** → crisis: `title`, `description`, `project_id`, `level` 1–4, `status` (9 lifecycle statuses + workflow config), `manager_id`, `detected_at`, `resolved_at`, `archived_at`, `simulation_id`. Existing auto-detection keeps creating them (mapping INFO/LOW→1, MEDIUM→2, HIGH→3, CRITICAL→4); thresholds become per-tenant settings |
| CrisisTimelineEvent | **New `crisis_timeline`** (append-only; system and human events) |
| Crisis team, comments, actions | **New `crisis_members`**, **`crisis_comments`**, **`crisis_actions`** (owner, deadline, status) |
| CrisisResponsePlan | **New `crisis_plans`** + **`crisis_plan_versions`** (immutable versions of the 12 sections) |
| CrisisMessage, CrisisApproval | **New `responses`** (unified register for Module 9 and 10: target mention or crisis, type, channel, language, AI proposal ref, current text, status draft → under_review → changes_requested → approved → published → archived, publication URL/time, metrics) + **`response_events`** (who drafted, reviewed, approved, published, with text hash). Existing approved `use_response` actions migrate into it |
| CrisisDecision | **New `crisis_decisions`** (description, evidence refs, decision maker, time, expected / actual outcome, follow-up) |
| CrisisSimulation | **New `crisis_simulations`**; simulated crises/messages carry `simulation_id` and are excluded from every real query, alert, report and metric by database views + tests |

Retention: every new tenant table joins the existing daily purge (plan retention, cap
`REPINTEL_RETENTION_MAX_DAYS`); provider-specific limits (e.g. YouTube data refresh rules) are
applied per connector. Disconnecting a source revokes and deletes its credentials.

---

## 4. Module designs (key points)

**Connector interface (Module 1).** `AccountConnector(Connector)` adds the brief's methods in the
project's style: `authorization_url()` / `exchange_code()` (= authenticate), `validate_permissions()`
(granted scopes vs required, per capability), `list_accounts()` (= fetch_sources),
`fetch_items(cursor)`, `fetch_comments(item, cursor)`, `fetch_metrics(ids)`, `sync(source, mode)`,
`rate_limit_policy()` / `classify_error()` (= handle_rate_limit), `refresh_credentials()`,
`revoke()` (= disconnect). Each connector declares a **capability matrix** (what it can return,
what it cannot) that the UI shows per source. OAuth uses PKCE where supported, `state` bound to
the user session, redirect URI allow-list, and runs only for members with `sources:manage`.

**Unified collection (Module 3).** Sync jobs per source (`source.sync`) next to the existing
project runs; both feed the same normalisation → dedup (external ID, then content hash) →
entity matching → language → NLP job → indicators → alert evaluation. Items already analysed are
not re-analysed when only metrics change (metrics go to snapshots).

**Reviews (Module 2).** Rating trend and distribution, positive/negative ratios (from rating
*and* text sentiment, mismatches flagged), recurring complaints and praised attributes (topic and
intent clusters from the existing NLP), product/service issue detection, location comparison,
competitor comparison (only where competitor data is legitimately available — labelled
"sample" for Places), unanswered reviews with age, volume anomalies (same engine as Module 7).
Filters: brand, product, location, platform, rating, sentiment, date, topic, response status.

**Quotas and reliability (Module 4).** Token buckets in Redis per (connector, credential) and
per tenant; `Retry-After` honoured; exponential back-off with jitter, max attempts per job, then
`dead`; circuit breaker per (connector, source): 5 consecutive failures → open 15 min → half-open
probe; auth errors → `needs_reauth` (no retries); quota exhaustion → paused until reset.
Reliability score (0–100, not reputation): success rate 35 %, freshness vs configured frequency
25 %, availability (circuit closed time) 20 %, completeness (fields returned vs capability
matrix) 10 %, consistency (variance of items per sync) 10 %. Documented in a methodology page. The Collection Health dashboard shows connected / active /
disconnected / needs-reauth sources, last success and next scheduled sync, success and failure
rates, quota consumption per connector, error categories and average sync delay; the technical
error log (redacted) is limited to `collection:admin`.

**Coverage index (Module 5).** Deterministic, per brand and period, with each component shown:
configured coverage (platforms connected / platforms relevant for the brand), active coverage
(sources healthy / sources configured), freshness, success rate, content availability, source
diversity. Confidence from volume and age of data. Always displayed with: observed volume,
estimated *observable* coverage, and a list of known-inaccessible areas (Facebook groups, public
search on Facebook/TikTok, Reddit, etc.). Never presented as a share of all online conversation.

**Influence (Module 6).** Score per author per brand: relevant engagement (log-scaled), number
of independent interactions, publication frequency, relevance (share of the author's items
about the brand), consistency over time; audience size is one capped signal, never the only one.
Measured values and estimates are labelled separately. Communities from the interaction graph
(reply/mention edges actually returned by APIs) with a deterministic algorithm (Louvain via
`networkx`, seeded); clusters below a minimum size or edge density are not shown. The map states
which platforms contributed edges.

**Anomalies (Module 7).** Signals: volume (robust z on median/MAD), posting-rate per author,
near-duplicate text (MinHash/shingles on normalised Darija/Arabizi text), timing burstiness,
repeated URLs, author concentration (Gini/HHI), sentiment shift, cross-platform synchrony,
engagement ratio outliers. Rules map signal combinations to the six labels; "possible
coordinated activity" requires ≥ 2 independent coordination signals (duplicates + timing, or
duplicates + concentration), never volume or negativity alone. The Trend agent writes the
human-readable interpretation from the evidence; it cannot change the label upward.

**Crisis workspace (Modules 8–10).** Lifecycle with configurable allowed transitions per tenant
(default = brief's 9 statuses); every transition, decision, approval and publication is a
timeline event and an audit entry. AI drafts (holding statement, FAQ, press statement, customer
template, internal note, executive brief, media answer, follow-up) come from the Response agent
using only the plan's *verified facts* section, in French, Arabic, Darija (Arabic script or
Arabizi) or English, with the brand's tone and guidelines; unverified items are marked as such in the
output schema. Publication: only through an official integration the source supports
(GBP review reply, Google Play reply, App Store reply, YouTube comment reply, Facebook/Instagram/
LinkedIn comment reply on owned pages) **and** only after the approval step by a member with the
approve permission; otherwise "published" is recorded manually with its URL. Simulation mode
uses the same workspace with `simulation_id`, synthetic mentions clearly marked, and a
response-time report export.

---

## 5. API, permissions, UI

New permissions: `sources:manage` (connect/disconnect accounts), `collection:admin` (error log),
`crisis:read`, `crisis:manage`, `crisis:approve`, `crisis:publish`, `intel:read`. Role mapping
follows the existing RBAC (owner/admin/analyst/viewer…).

Routers (existing conventions, `/api/v1`): `/connections` (OAuth start/callback, accounts),
`/sources` (extended), `/collection/health`, `/collection/errors`, `/coverage`, `/reviews`,
`/authors`, `/communities`, `/anomalies`, `/crises`, `/crises/{id}/plan|messages|decisions|
actions|timeline`, `/responses`, `/simulations`. Documented in API_DOCUMENTATION.

Navigation (brief §3) maps onto the existing sidebar: **Monitoring** (Social Sources, Review
Sources, Collection Health, Conversation Coverage — next to Projects, Sources, Mentions),
**Intelligence** (Influencers, Communities, Anomaly Detection, Coordinated Activity — next to
Analytics), **Crisis Center** (Overview, Active Crises, Workspace, Communication Plans, Response
History, Decision Register, Simulations). FR / AR (RTL) / EN, empty / loading / error /
not-authorised states, real data only.

---

## 6. Agents (extend the existing 13 — no new framework)

| Agent | New responsibility | New tools (allow-listed) |
|---|---|---|
| Collection | Sync health, re-auth needs, quota exhaustion, reliability | `sources.health`, `sync.request` (action, low risk) |
| Discovery | Suggest official accounts/pages/channels to connect (from search + business discovery), never auto-connect | `sources.suggest_accounts` |
| Language | Unchanged pipeline; reports per-platform language mix | — |
| Entity | Author resolution within a platform; brand/product/location linking for reviews | `authors.lookup` |
| Sentiment | Review rating vs text sentiment mismatches | `reviews.stats` |
| Trend | Interprets anomaly evidence (cannot raise labels) | `anomalies.recent` |
| Competitor | Review and coverage comparisons | `reviews.compare` |
| Crisis | Proposes level and status changes (approval required), escalation suggestions | `crises.summary`, action `crisis_propose_level` |
| Response | Plan-grounded crisis messages, review replies | `plans.verified_facts` (read) |
| Reporting | Crisis report, lessons learned, simulation report | `reports.crisis` |
| Orchestrator | New playbooks: `crisis_assessment`, `review_digest`, `coverage_check` | — |

No new agent is proposed. Every output keeps provenance, confidence and a strict JSON schema;
collected content stays inside `<data>` and cannot trigger tools (existing injection suite
extended with social/review payloads). Deterministic calculations (scores, anomalies, coverage,
quotas) are never delegated to a model.

---

## 7. Sprints (each ends with: report, files, DB changes, tests, gaps, blockers, docs)

Numbered after the eight delivered milestones.

| Sprint | Content | Testable without your credentials? |
|---|---|---|
| **9 — Connector framework & source management** | Migration (sources extension, links, credentials, syncs, errors, quotas), `AccountConnector`, OAuth flow (state/PKCE/redirect allow-list), credential encryption and rotation, Social/Review Sources pages, capability matrix UI, permissions | Yes (provider stubs) |
| **10 — Connectors** | YouTube (key + OAuth), Facebook Pages, Instagram (own, hashtags, business discovery), LinkedIn org pages, Google Business Profile, Google Places sample, Google Play, App Store Connect, Trustpilot; X if approved; `mention_reviews`, comments as child mentions, metric snapshots, review replies (approval-gated) | Contract tests against recorded fixtures; **live status "unverified" per connector until tested with your app credentials** |
| **11 — Unified engine, quotas, health, coverage** | Sync scheduler, token buckets, circuit breaker, suspension/recovery, error log, Collection Health dashboard, reliability score, coverage index + dashboard + methodology | Yes |
| **12 — Influencers, communities, anomalies** | Authors, influence score, interaction graph, communities map, anomaly engine + labels + evidence, Coordinated Activity page, Trend agent interpretation | Yes (fixtures) |
| **13 — Crisis workspace & plans** | Crisis extension + migration of existing crises, lifecycle/workflow config, levels config, team, comments, actions, timeline, dashboard, plans with versions, AI drafts, approval workflow | Yes |
| **14 — Responses, decisions, simulations, reporting** | Unified response register (+ migration of approved replies), official publication where supported, decision register, lessons-learned analytics, simulations, crisis/simulation reports (PDF/XLSX) | Yes, except live publication |
| **15 — Hardening** | Security review (OAuth, tokens, SSRF, injection), performance (indexes, partitioning of mentions if needed), E2E tests of the brief's 10 scenarios (Playwright), docs, deployment update (new secrets, OAuth redirect URIs) | Yes |

---

## 8. What you will need to provide (blockers for live connectors)

| For | You provide | Lead time (published / typical) |
|---|---|---|
| YouTube | Google Cloud project, YouTube Data API key; OAuth client for channel replies | Same day; quota increase by request |
| Facebook / Instagram | Meta developer app, **Business Verification** (company documents), App Review per permission; optional application for Page / Instagram Public Content Access | Verification ~1–3 weeks; review days to weeks; PPCA not guaranteed |
| LinkedIn | Developer app linked to your company page, Community Management API application | Business review, weeks |
| Google Business Profile | GBP API access request (Google Cloud project) | Days to weeks |
| Google Places (competitor sample) | API key with billing | Same day |
| Google Play / App Store | Nothing from you — each client connects its own developer account | — |
| X | Developer account + credits (if chosen) | Same day |
| Trustpilot | Nothing from you — clients with the API module connect their account | — |
| All | Public **privacy policy** and **terms** URLs, OAuth redirect domain (`https://<your domain>/…`) — required by every platform review | Before submitting app reviews |
| Legal | Review of each new connector (personal data in comments/authors, law 18-07, platform terms) before approval in the admin console | — |

---

## 9. Decisions needed before Sprint 9

1. **X**: build the X connector with per-tenant spending caps (every post read is billed), or skip it for now?
2. **TikTok**: offer the own-account connector (client's own videos/comments only), or mark TikTok "unavailable for monitoring"?
3. **Reddit**: confirm it is marked unavailable (no new API access; commercial contract only).
4. **Trustpilot / Tripadvisor**: Trustpilot only for clients who have its API module; Tripadvisor marked "requires partner access" — agree?
5. **Meta Page Public Content Access**: do you want us to prepare the application (use-case description, screencast) once Sprint 10 has a working flow?

---

## 10. Sources consulted (2026-11-13)

Official: X API pricing (docs.x.com), YouTube quota costs (developers.google.com), Google Play reply-to-reviews guide (developers.google.com), Trustpilot developer pages (developers.trustpilot.com).
Secondary (to confirm against official pages at implementation): Reddit API changes (imisofts.com, citing Reddit help-center changelog of 8 Oct 2026), Meta APIs (datashake.com), Instagram public data limits (docs.supermetrics.com), TikTok Research API eligibility (tokconnect.com), App Store review access (datashake.com), Tripadvisor Content API (supergood.ai), Google Business Profile access (slashpost.ai), LinkedIn permissions (bundle.social).
