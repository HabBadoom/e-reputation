"""Aynpulse Scanner, phase 7: sending collections to AnyPulse (central space, option A).

Revision ID: 0018_scanner_transmission
Revises: 0017_scanner_retention
Create Date: 2026-11-25

Decisions (product owner): collections go to a central space; AnyPulse attributes each
screenshot to the brands it concerns and delivers it to **every organisation that follows
that brand** (2026-11-25); when unsure, a **platform administrator** decides (review queue);
unvalidated screenshots may be sent and arrive **marked as not validated** (2026-11-24).

- `mentions` accept Scanner content: `collection_method = 'scanner'`, verification
  `scanner_validated` / `scanner_unvalidated`; connector policy `aynpulse_scanner`.
- `scanner_attributions`: one row per sent screenshot (status, method, brand names chosen,
  confidence, mentions delivered, admin decision). Visible to the agent who sent it and to
  platform administrators only.
- `scanner_brand_catalogue()`: active brands of all organisations with their names, aliases
  and keywords, for the attribution job (SECURITY DEFINER, API role only).
"""

import os

from alembic import op

revision = "0018_scanner_transmission"
down_revision = "0017_scanner_retention"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")
VERIFICATION = ("verified_on_page", "snippet_only", "not_found_on_page", "page_unavailable",
                "blocked_by_robots", "feed_item", "pending_render", "blocked_by_challenge")
SCANNER_VERIFICATION = ("scanner_validated", "scanner_unvalidated")
METHODS = ("search_api", "news_api", "rss", "page_fetch")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(f"""
    ALTER TABLE mentions DROP CONSTRAINT mentions_collection_method_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_collection_method_check
      CHECK (collection_method IN ({_in(METHODS + ("scanner",))}));
    ALTER TABLE mentions DROP CONSTRAINT mentions_verification_status_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_verification_status_check
      CHECK (verification_status IN ({_in(VERIFICATION + SCANNER_VERIFICATION)}));

    INSERT INTO connector_policies (key, name, access_method, legal_basis, data_categories,
                                    restrictions, retention_days)
    VALUES ('aynpulse_scanner', 'Aynpulse Scanner (agent screenshots)', 'agent_capture',
      'Content seen and captured by collection agents (screenshots, dedicated social-media '
      'accounts), transcribed by a vision model. Includes names of commenters: review under '
      'Law 18-07 and the platforms'' terms before production.',
      ARRAY['post and comment text', 'author display name', 'page or group name',
            'publication time label'],
      'No profile data, no private messages; images stay in Scanner (30 days after sending).',
      365)
    ON CONFLICT (key) DO NOTHING;

    CREATE TABLE scanner_attributions (
      item_id uuid PRIMARY KEY,
      account_id uuid NOT NULL,
      session_id uuid NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','delivered','to_review','discarded','empty')),
      method varchar(20) CHECK (method IN ('keywords','ai','admin')),
      brand_names text[] NOT NULL DEFAULT '{{}}',
      confidence numeric(3,2) CHECK (confidence BETWEEN 0 AND 1),
      reason text NOT NULL DEFAULT '',
      mentions_created integer NOT NULL DEFAULT 0,
      organisations integer NOT NULL DEFAULT 0,
      decided_by uuid REFERENCES users(id) ON DELETE SET NULL,
      decided_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (account_id, item_id)
        REFERENCES scanner_collection_items (account_id, id) ON DELETE CASCADE,
      FOREIGN KEY (account_id, session_id)
        REFERENCES scanner_collection_sessions (account_id, id) ON DELETE CASCADE
    );
    CREATE INDEX scanner_attributions_review_idx ON scanner_attributions (created_at)
      WHERE status = 'to_review';
    CREATE INDEX scanner_attributions_session_idx ON scanner_attributions (session_id);
    ALTER TABLE scanner_attributions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scanner_attributions FORCE ROW LEVEL SECURITY;
    CREATE POLICY scanner_attribution_access ON scanner_attributions
      USING (account_id = app_current_scanner_account() OR app_is_platform_admin())
      WITH CHECK (account_id = app_current_scanner_account() OR app_is_platform_admin());
    GRANT SELECT, INSERT, UPDATE ON scanner_attributions TO {APP_ROLE};

    CREATE FUNCTION scanner_brand_catalogue()
      RETURNS TABLE (tenant_id uuid, brand_id uuid, name text, aliases text[],
                     include_terms text[], exclude_terms text[])
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT b.tenant_id, b.id, b.name::text,
             coalesce((SELECT array_agg(a.alias::text) FROM brand_aliases a
                        WHERE a.tenant_id = b.tenant_id AND a.brand_id = b.id), '{{}}'),
             coalesce((SELECT array_agg(k.expression::text) FROM brand_keywords k
                        WHERE k.tenant_id = b.tenant_id AND k.brand_id = b.id
                          AND k.kind = 'include'), '{{}}'),
             coalesce((SELECT array_agg(k.expression::text) FROM brand_keywords k
                        WHERE k.tenant_id = b.tenant_id AND k.brand_id = b.id
                          AND k.kind = 'exclude'), '{{}}')
        FROM brands b JOIN tenants t ON t.id = b.tenant_id
       WHERE b.status = 'active' AND t.status = 'active'
       ORDER BY b.name, b.tenant_id
    $$;
    REVOKE ALL ON FUNCTION scanner_brand_catalogue() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION scanner_brand_catalogue() TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
    DROP FUNCTION IF EXISTS scanner_brand_catalogue();
    DROP TABLE IF EXISTS scanner_attributions;
    DELETE FROM mentions WHERE collection_method = 'scanner';
    DELETE FROM connector_policies WHERE key = 'aynpulse_scanner';
    ALTER TABLE mentions DROP CONSTRAINT mentions_verification_status_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_verification_status_check
      CHECK (verification_status IN ({_in(VERIFICATION)}));
    ALTER TABLE mentions DROP CONSTRAINT mentions_collection_method_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_collection_method_check
      CHECK (collection_method IN ({_in(METHODS)}));
    """)
