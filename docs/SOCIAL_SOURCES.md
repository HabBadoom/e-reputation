# Social sources (0.11)

Public social-network content is collected per **monitoring project** (which gives the brand,
its products, competitors and exclusion terms) from two providers:

| Provider | Access | Sources |
|---|---|---|
| **Apify** (third-party provider) | Actors run **logged out**: no account, no cookies, public content only | Facebook Pages, public Facebook groups (+ comments), TikTok accounts / hashtags / searches, LinkedIn company pages, Reddit communities / searches |
| **YouTube Data API v3** (official) | API key | Channels (uploads), keyword searches (region DZ), comment threads with replies |

**Decision record.** The product owner chose Apify on 2026-11-25 to reach public Facebook Pages
and groups, which no official API exposes. The platforms' terms prohibit automated collection,
and authors' and commenters' names are personal data under Law 18-07. Both connectors
(`apify`, `youtube`) are created as `pending_legal_review`: **blocked in production** until a
platform administrator records the legal review in the admin console. This deviates from the
Phase 2/3 brief's rule against scraping that breaches platform terms; it is the owner's choice.

## How a sync works

1. The scheduler (`schedule_social_syncs`) enqueues `social.sync` for enabled sources of
   active projects whose `next_sync_at` is due (active subscriptions only). "Collect now"
   enqueues a manual sync (6 per hour per source).
2. Checks, in order: source enabled → project active → subscription active → connector allowed
   (compliance record) → provider configured → **monthly budget** for the platform. A refused
   sync is recorded as `skipped` with its reason.
3. **Apify**: the actor is started with `maxItems` (results billed) and `maxTotalChargeUsd`
   (`REPINTEL_APIFY_MAX_RUN_USD`); `social.poll` checks the run every
   `REPINTEL_APIFY_POLL_SECONDS` and reads its dataset when it ends (a run still going after
   `REPINTEL_APIFY_MAX_WAIT_MINUTES` is aborted and its partial results kept). After the first
   sync, the date filter starts from the newest post already collected.
4. **YouTube**: synchronous; each call's quota units are recorded before the call and the sync
   stops cleanly when the platform's daily quota (`REPINTEL_YOUTUBE_DAILY_QUOTA_UNITS`, reset at
   midnight Pacific time, shared by all organisations) would be exceeded.
5. Items become mentions (`collection_method` `third_party_provider` / `platform_api`,
   verification `provider_record` / `platform_api_record`) and are analysed by the NLP pipeline.

### Attribution

- `match_mode = terms` (default; groups, third-party pages, searches): a post is kept only when
  it names a tracked name of the project (brand, product, competitor — in that priority) and no
  exclusion term. Its comments are kept with it. Under a post that is not kept, a comment is kept
  only if it names a tracked name itself (stored without parent).
- `match_mode = brand` (the brand's own page or channel): every post is kept for the brand.

### Comments

- Facebook: a second run of the comments actor, on kept posts at least
  `12 hours` old (comments have accumulated), up to `REPINTEL_APIFY_COMMENTS_PER_POST` (30) per
  post, `REPINTEL_APIFY_COMMENT_POSTS_PER_SYNC` (20) posts per sync, **once per post**. Group
  results also include the actor's "top comments" at no extra cost.
- YouTube: up to `REPINTEL_YOUTUBE_COMMENTS_PER_VIDEO` most recent threads per video, with the
  replies YouTube includes; once per video. Channels that disable comments are skipped.
- TikTok, LinkedIn, Reddit: posts/videos only in this version.

### Idempotency and duplicates

- An item is stored once per brand (`platform` + `external_id`, unique). Seen again, only its
  counts (`engagement`) and `last_seen_at` / `seen_count` change.
- **Scanner**: when the same Facebook post (same address) or comment (same post, same text) was
  already received from a Scanner capture, the new mention is marked `duplicate_of` it and is
  counted once (and vice versa for Scanner's own dedup). Different address formats for the same
  post (share links) are not matched.
- Long identical texts (≥ 40 characters) are marked as reposts, as for web mentions.

## Budgets (plan entitlements)

| Entitlement | Trial / tiers (test) | Usage metric |
|---|---|---|
| `max_facebook_posts_per_month` | 3,000 | `facebook_posts` |
| `max_facebook_comments_per_month` | 10,000 | `facebook_comments` |
| `max_tiktok_results_per_month` | 2,000 | `tiktok_results` |
| `max_reddit_results_per_month` | 1,000 | `reddit_results` |
| `max_linkedin_results_per_month` | 500 | `linkedin_results` |
| `max_youtube_results_per_month` | unlimited (metered) | `youtube_results` (+ `youtube_units`) |

Usage counts **results returned by the provider** (what Apify bills), not mentions kept.
Scheduled runs request at most their share: what is left of the monthly cap ÷ runs left this
month ÷ sources of that platform; a manual run may use up to the per-run maximum. The monthly
mention quota (`max_mentions_per_month`) still applies to what is stored.

**Cost.** Apify prices per actor change; at the time of writing, Apify-maintained Facebook
posts/groups actors cost about $4–5 per 1,000 posts on the Starter plan, comments about $1.40–2.50
per 1,000, TikTok about $1.70, Reddit about $2.50, LinkedIn (harvestapi) about $1.50–2 per 1,000.
At the trial caps that is roughly $35–50 per organisation per month if every cap is used. Each
sync records the cost reported by Apify (`provider_cost_usd`). **Set a monthly spending limit
in the Apify console** as the final safeguard.

## Actors (configuration, not code)

`REPINTEL_APIFY_ACTORS` maps a source kind to an actor (`user~actor`):

| Key | Default |
|---|---|
| `facebook_page` | `apify~facebook-posts-scraper` |
| `facebook_group` | `apify~facebook-groups-scraper` |
| `facebook_comments` | `apify~facebook-comments-scraper` |
| `tiktok` | `clockworks~tiktok-scraper` |
| `reddit` | `apify~reddit-scraper` |
| `linkedin_company` | `harvestapi~linkedin-company-posts` (no Apify-maintained actor works without cookies) |

Adapters (`app/connectors/apify.py`) read the documented output fields and their known
alternatives. An actor can change its format at any time: every sync records
`items_unreadable`, and a sync where most items could not be read is `partial` with category
`parse`. Replacing an actor with one whose input differs needs an adapter change.

**Not verified live.** The adapters were written from the actors' published documentation and
tested against fake servers; they have not yet run against Apify with a real token. The first
syncs after configuration should be watched (Social sources → History).

## Data and limits

- Stored per mention: text (original, never rewritten), permalink, publication time (when the
  provider gives it — never guessed), author display name, author profile link and id,
  interaction counts as returned (interactions, not unique people), page/group/channel name,
  provider run ids.
- Not collected: private groups, content behind login, private messages, profile enrichment,
  followers lists.
- No keyword search on Facebook (pages and groups must be listed).
- Retention: plan retention applies (daily purge); connector policy retention 365 days.

## API

See API_DOCUMENTATION: `GET/POST /social-sources`, `PATCH/DELETE /social-sources/{id}`,
`POST /social-sources/{id}/sync`, `GET /social-sources/{id}/syncs`.
