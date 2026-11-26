"""Social sources: Apify (third-party provider) and the YouTube Data API (official).

Revision ID: 0019_social_sources
Revises: 0018_scanner_transmission
Create Date: 2026-11-26

Decisions (product owner, 2026-11-25/26): public Facebook Pages and public groups, TikTok,
LinkedIn company pages and Reddit are collected through Apify actors that run **logged out**
(no account, no cookies); YouTube through its official API. Monthly result caps per client
are plan entitlements (trial: Facebook posts 3,000, Facebook comments 10,000, TikTok 2,000,
Reddit 1,000, LinkedIn 500).

- `sources`: social kinds, attached to a monitoring project (which gives the brand and the
  terms), with `match_mode` (`terms`: keep items naming a tracked name; `brand`: the brand's
  own page/account, keep everything), `query` (search kinds), own frequency and sync state.
  The same page may be followed by several projects (uniqueness is per project).
- `source_syncs`: one row per provider run (items or comments stage): provider run id,
  items fetched / unreadable, mentions created / already known, results billed, cost as
  reported by the provider, error category.
- `mentions`: `platform`, `external_id` (idempotency), `content_type`, `parent_mention_id`
  (comments under their post), `engagement` (as returned; interactions, not people),
  `author_external_id`, `author_url`; methods `platform_api` / `third_party_provider`;
  verification `platform_api_record` / `provider_record`.
- Usage metrics and monthly entitlements per platform; connector policies `apify` and
  `youtube` (pending legal review: blocked in production until approved).
- `schedule_social_syncs()`: enqueues due syncs (active subscriptions only);
  `youtube_units_today()`: platform-wide YouTube quota used today (Pacific time).
"""

import os

from alembic import op

revision = "0019_social_sources"
down_revision = "0018_scanner_transmission"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

OLD_KINDS = ("website", "rss")
SOCIAL_KINDS = ("facebook_page", "facebook_group", "tiktok_profile", "tiktok_hashtag",
                "tiktok_search", "linkedin_company", "reddit_community", "reddit_search",
                "youtube_channel", "youtube_search")
METHODS = ("search_api", "news_api", "rss", "page_fetch", "scanner")
VERIFICATION = ("verified_on_page", "snippet_only", "not_found_on_page", "page_unavailable",
                "blocked_by_robots", "feed_item", "pending_render", "blocked_by_challenge",
                "scanner_validated", "scanner_unvalidated")
OLD_METRICS = ("mentions_collected", "search_queries", "ai_calls", "ai_tokens",
               "browser_missions", "reports_generated", "agent_tasks", "emails_sent")
NEW_METRICS = ("facebook_posts", "facebook_comments", "tiktok_results", "reddit_results",
               "linkedin_results", "youtube_results", "youtube_units")
# Trial and the commercial tiers get the test caps; the internal 'unlimited' plan gets NULL.
NEW_ENTITLEMENTS = {"max_facebook_posts_per_month": 3000,
                    "max_facebook_comments_per_month": 10000,
                    "max_tiktok_results_per_month": 2000,
                    "max_reddit_results_per_month": 1000,
                    "max_linkedin_results_per_month": 500,
                    "max_youtube_results_per_month": None}   # free API: metered, not capped


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(f"""
    -- ------------------------------------------------------------------ sources
    ALTER TABLE sources DROP CONSTRAINT sources_kind_check;
    ALTER TABLE sources ADD CONSTRAINT sources_kind_check
      CHECK (kind IN ({_in(OLD_KINDS + SOCIAL_KINDS)}));
    ALTER TABLE sources
      ADD COLUMN project_id uuid,
      ADD COLUMN match_mode varchar(10) NOT NULL DEFAULT 'terms'
        CHECK (match_mode IN ('terms', 'brand')),
      ADD COLUMN query text NOT NULL DEFAULT '' CHECK (length(query) <= 300),
      ADD COLUMN frequency_minutes int CHECK (frequency_minutes BETWEEN 15 AND 10080),
      ADD COLUMN next_sync_at timestamptz,
      ADD COLUMN last_synced_at timestamptz,
      ADD COLUMN sync_state jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      ADD CONSTRAINT sources_project_fk FOREIGN KEY (tenant_id, project_id)
        REFERENCES monitoring_projects (tenant_id, id) ON DELETE CASCADE,
      ADD CONSTRAINT sources_social_project CHECK (
        (kind IN ({_in(OLD_KINDS)}) AND project_id IS NULL)
        OR (kind IN ({_in(SOCIAL_KINDS)}) AND project_id IS NOT NULL));
    DROP INDEX sources_tenant_url_uq;
    CREATE UNIQUE INDEX sources_tenant_url_uq ON sources (tenant_id, kind, lower(url))
      WHERE project_id IS NULL;
    CREATE UNIQUE INDEX sources_project_url_uq ON sources (tenant_id, project_id, kind, lower(url))
      WHERE project_id IS NOT NULL;
    CREATE INDEX sources_due_idx ON sources (next_sync_at)
      WHERE project_id IS NOT NULL AND enabled;

    -- ------------------------------------------------------------- source_syncs
    CREATE TABLE source_syncs (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      source_id uuid NOT NULL,
      parent_sync_id uuid,
      provider varchar(20) NOT NULL CHECK (provider IN ('apify', 'youtube')),
      stage varchar(10) NOT NULL DEFAULT 'items' CHECK (stage IN ('items', 'comments')),
      trigger varchar(10) NOT NULL DEFAULT 'schedule' CHECK (trigger IN ('schedule', 'manual')),
      status varchar(12) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'skipped')),
      actor varchar(120),
      external_run_id varchar(100),
      dataset_id varchar(100),
      max_items int CHECK (max_items >= 0),
      items_fetched int NOT NULL DEFAULT 0,
      items_unreadable int NOT NULL DEFAULT 0,
      mentions_new int NOT NULL DEFAULT 0,
      mentions_known int NOT NULL DEFAULT 0,
      results_billed int NOT NULL DEFAULT 0,
      api_units int NOT NULL DEFAULT 0,
      provider_cost_usd numeric(10, 4),
      polls int NOT NULL DEFAULT 0,
      error_category varchar(20) CHECK (error_category IN ('not_configured', 'not_authorized',
        'quota', 'subscription', 'auth', 'rate_limit', 'provider', 'network', 'timeout',
        'parse', 'not_found', 'input')),
      error text,
      created_at timestamptz NOT NULL DEFAULT now(),
      started_at timestamptz,
      finished_at timestamptz,
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, source_id) REFERENCES sources (tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, parent_sync_id) REFERENCES source_syncs (tenant_id, id)
        ON DELETE SET NULL (parent_sync_id)
    );
    CREATE INDEX source_syncs_source_idx ON source_syncs (tenant_id, source_id, created_at DESC);
    CREATE INDEX source_syncs_open_idx ON source_syncs (status) WHERE status IN ('queued', 'running');
    ALTER TABLE source_syncs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE source_syncs FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON source_syncs
      USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
    GRANT SELECT, INSERT, UPDATE, DELETE ON source_syncs TO {APP_ROLE};

    -- ------------------------------------------------------------------ mentions
    ALTER TABLE mentions DROP CONSTRAINT mentions_collection_method_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_collection_method_check
      CHECK (collection_method IN ({_in(METHODS + ("platform_api", "third_party_provider"))}));
    ALTER TABLE mentions DROP CONSTRAINT mentions_verification_status_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_verification_status_check
      CHECK (verification_status IN
             ({_in(VERIFICATION + ("platform_api_record", "provider_record"))}));
    ALTER TABLE mentions
      ADD COLUMN platform varchar(20) CHECK (platform IN ('facebook', 'instagram', 'tiktok',
        'linkedin', 'reddit', 'youtube', 'x')),
      ADD COLUMN external_id varchar(200),
      ADD COLUMN content_type varchar(10) CHECK (content_type IN ('post', 'video', 'comment',
        'reply')),
      ADD COLUMN parent_mention_id uuid,
      ADD COLUMN engagement jsonb,
      ADD COLUMN author_external_id varchar(200),
      ADD COLUMN author_url varchar(500),
      ADD CONSTRAINT mentions_parent_fk FOREIGN KEY (tenant_id, parent_mention_id)
        REFERENCES mentions (tenant_id, id) ON DELETE SET NULL (parent_mention_id),
      ADD CONSTRAINT mentions_parent_not_self CHECK (parent_mention_id <> id),
      ADD CONSTRAINT mentions_external_needs_platform
        CHECK (external_id IS NULL OR platform IS NOT NULL);
    CREATE UNIQUE INDEX mentions_external_uq ON mentions (tenant_id, brand_id, platform, external_id)
      WHERE external_id IS NOT NULL;
    CREATE INDEX mentions_parent_idx ON mentions (tenant_id, parent_mention_id)
      WHERE parent_mention_id IS NOT NULL;

    -- ------------------------------------------------------------ usage and plans
    ALTER TABLE usage_events DROP CONSTRAINT usage_events_metric_check;
    ALTER TABLE usage_events ADD CONSTRAINT usage_events_metric_check
      CHECK (metric IN ({_in(OLD_METRICS + NEW_METRICS)}));
    """)
    for key, value in NEW_ENTITLEMENTS.items():
        v = "NULL" if value is None else str(value)
        op.execute(f"""
        INSERT INTO plan_entitlements (plan_id, key, value)
        SELECT id, '{key}', CASE WHEN key = 'unlimited' THEN NULL ELSE {v}::int END FROM plans
        ON CONFLICT DO NOTHING;
        """)

    op.execute(f"""
    INSERT INTO connector_policies (key, name, access_method, legal_basis, data_categories,
                                    restrictions, retention_days) VALUES
    ('apify', 'Apify (third-party collection provider)', 'third_party_provider',
     'Public content of public Facebook Pages and groups, TikTok, LinkedIn company pages and '
     'Reddit, collected by Apify actors run logged out (no account, no cookies). The platforms'' '
     'terms prohibit automated collection; includes names and profile links of authors and '
     'commenters (personal data under Law 18-07). Product-owner decision 2026-11-25; legal '
     'review required before production.',
     ARRAY['post and comment text', 'author display name', 'author profile link and id',
           'publication time', 'interaction counts', 'page or group name'],
     'Public content only; no private groups, no login, no private messages, no profile '
     'enrichment. Results capped per client and per month.', 365),
    ('youtube', 'YouTube Data API v3 (official)', 'official_api',
     'Public videos and comments through the official YouTube Data API with an API key, under '
     'the YouTube API Services Terms and Developer Policies (data refresh/retention rules to '
     'be confirmed by review). Includes commenter display names and channel links.',
     ARRAY['video title and description', 'comment text', 'channel / commenter display name',
           'channel link and id', 'publication time', 'view, like and comment counts'],
     'Public data only; daily quota of the Google Cloud project; no private videos.', 365)
    ON CONFLICT (key) DO NOTHING;

    -- -------------------------------------------------------------- scheduling
    CREATE FUNCTION schedule_social_syncs(p_default_minutes int) RETURNS int
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      WITH due AS (
        SELECT s.id, s.tenant_id,
               coalesce(s.frequency_minutes, p_default_minutes) AS every
        FROM sources s
        JOIN monitoring_projects p ON p.tenant_id = s.tenant_id AND p.id = s.project_id
        JOIN subscriptions sub ON sub.tenant_id = s.tenant_id
        WHERE s.project_id IS NOT NULL AND s.enabled AND p.status = 'active'
          AND sub.status IN ('trialing', 'active', 'past_due')
          AND s.next_sync_at IS NOT NULL AND s.next_sync_at <= now()
        FOR UPDATE OF s SKIP LOCKED),
      upd AS (
        UPDATE sources s SET next_sync_at = now() + make_interval(mins => due.every)
        FROM due WHERE s.id = due.id
        RETURNING s.id, s.tenant_id),
      ins AS (
        INSERT INTO jobs (id, tenant_id, kind, payload, max_attempts)
        SELECT gen_random_uuid(), upd.tenant_id, 'social.sync',
               jsonb_build_object('source_id', upd.id, 'trigger', 'schedule'), 3
        FROM upd RETURNING 1)
      SELECT count(*)::int FROM ins
    $$;
    REVOKE ALL ON FUNCTION schedule_social_syncs(int) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION schedule_social_syncs(int) TO {APP_ROLE};

    -- The YouTube quota belongs to the platform's Google Cloud project and resets at
    -- midnight Pacific time: sum of every tenant's units today (a number, no tenant data).
    CREATE FUNCTION youtube_units_today() RETURNS bigint
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT coalesce(sum(quantity), 0) FROM usage_events
      WHERE metric = 'youtube_units'
        AND occurred_at >= (date_trunc('day', now() AT TIME ZONE 'America/Los_Angeles')
                            AT TIME ZONE 'America/Los_Angeles')
    $$;
    REVOKE ALL ON FUNCTION youtube_units_today() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION youtube_units_today() TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
    DROP FUNCTION youtube_units_today();
    DROP FUNCTION schedule_social_syncs(int);
    DELETE FROM plan_entitlements WHERE key IN ({_in(tuple(NEW_ENTITLEMENTS))});
    DELETE FROM usage_events WHERE metric IN ({_in(NEW_METRICS)});
    ALTER TABLE usage_events DROP CONSTRAINT usage_events_metric_check;
    ALTER TABLE usage_events ADD CONSTRAINT usage_events_metric_check
      CHECK (metric IN ({_in(OLD_METRICS)}));

    DELETE FROM mentions WHERE collection_method IN ('platform_api', 'third_party_provider');
    DROP INDEX mentions_parent_idx;
    DROP INDEX mentions_external_uq;
    ALTER TABLE mentions DROP CONSTRAINT mentions_parent_fk,
      DROP CONSTRAINT mentions_parent_not_self, DROP CONSTRAINT mentions_external_needs_platform,
      DROP COLUMN platform, DROP COLUMN external_id, DROP COLUMN content_type,
      DROP COLUMN parent_mention_id, DROP COLUMN engagement, DROP COLUMN author_external_id,
      DROP COLUMN author_url;
    ALTER TABLE mentions DROP CONSTRAINT mentions_verification_status_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_verification_status_check
      CHECK (verification_status IN ({_in(VERIFICATION)}));
    ALTER TABLE mentions DROP CONSTRAINT mentions_collection_method_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_collection_method_check
      CHECK (collection_method IN ({_in(METHODS)}));

    DROP TABLE source_syncs;
    DELETE FROM sources WHERE project_id IS NOT NULL;
    DROP INDEX sources_due_idx;
    DROP INDEX sources_project_url_uq;
    DROP INDEX sources_tenant_url_uq;
    CREATE UNIQUE INDEX sources_tenant_url_uq ON sources (tenant_id, kind, lower(url));
    ALTER TABLE sources DROP CONSTRAINT sources_social_project,
      DROP CONSTRAINT sources_project_fk,
      DROP COLUMN project_id, DROP COLUMN match_mode, DROP COLUMN query,
      DROP COLUMN frequency_minutes, DROP COLUMN next_sync_at, DROP COLUMN last_synced_at,
      DROP COLUMN sync_state;
    ALTER TABLE sources DROP CONSTRAINT sources_kind_check;
    ALTER TABLE sources ADD CONSTRAINT sources_kind_check CHECK (kind IN ({_in(OLD_KINDS)}));
    DELETE FROM connector_policies WHERE key IN ('apify', 'youtube');
    """)
