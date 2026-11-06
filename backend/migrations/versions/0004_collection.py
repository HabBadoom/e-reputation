"""Milestone 3: source registry, mentions with provenance, connector compliance records.

Revision ID: 0004_collection
Revises: 0003_monitoring
Create Date: 2026-11-06
"""

import os
import uuid

from alembic import op

revision = "0004_collection"
down_revision = "0003_monitoring"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

COVERAGE = ("AVAILABLE", "PARTIALLY_AVAILABLE", "PENDING_AUTHORIZATION", "RESTRICTED",
            "UNAVAILABLE", "CONNECTOR_ERROR")

NEW_PERMISSIONS = {
    "mentions:read": ("View collected mentions", ["owner", "admin", "analyst", "viewer"]),
    "sources:read": ("View the source registry", ["owner", "admin", "analyst", "viewer"]),
    "sources:write": ("Add, enable and check sources", ["owner", "admin", "analyst"]),
}

# One row per connector: the compliance record required before production collection
# (master prompt §6, §18). review_status must be 'approved' for the connector to run in
# production; any other value blocks it there (development/test are not blocked).
CONNECTORS = [
    ("serper", "Serper.dev Google search & news API", "third_party_search_api",
     "Commercial API subscription (Serper terms of service). Results originate from Google "
     "Search; Google's terms do not authorise this reuse and litigation about SERP scraping "
     "is ongoing in the US (Google v. SerpApi).",
     ["url", "title", "snippet", "publisher name", "publication date"],
     "Snippets are short excerpts of third-party publications.", 365),
    ("rss", "RSS / Atom feeds", "public_feed",
     "Feeds are published for syndication; reuse terms vary per publisher.",
     ["url", "title", "summary", "author name", "publication date"],
     "Store excerpts and links, not full articles.", 365),
    ("web", "Public web page reader", "public_web_page",
     "Publicly accessible pages, robots.txt honoured, no login/paywall/anti-bot bypass.",
     ["url", "title", "text excerpt around the brand mention", "publication date"],
     "Only an excerpt around the match is stored; full pages are not kept.", 365),
]


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:{kind}:{key}")


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def upgrade() -> None:
    x = op.execute
    coverage = ", ".join(f"'{c}'" for c in COVERAGE)

    x(f"""
    CREATE TABLE connector_policies (
      key varchar(40) PRIMARY KEY,
      name varchar(200) NOT NULL,
      access_method varchar(40) NOT NULL,
      legal_basis text NOT NULL,
      data_categories text[] NOT NULL DEFAULT '{{}}',
      restrictions text NOT NULL DEFAULT '',
      retention_days integer NOT NULL CHECK (retention_days > 0),
      review_status varchar(30) NOT NULL DEFAULT 'pending_legal_review'
        CHECK (review_status IN ('pending_legal_review','approved','rejected')),
      reviewed_by text,
      reviewed_at timestamptz,
      updated_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE sources (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      kind varchar(20) NOT NULL CHECK (kind IN ('website','rss')),
      url varchar(1000) NOT NULL,
      domain varchar(255) NOT NULL,
      name varchar(200) NOT NULL DEFAULT '',
      category varchar(40) NOT NULL DEFAULT 'unknown'
        CHECK (category IN ('unknown','news','blog','forum','social','review','directory',
                            'ecommerce','video','app_store','company','other')),
      country char(2),
      language varchar(10),
      connector varchar(40) NOT NULL REFERENCES connector_policies(key),
      enabled boolean NOT NULL DEFAULT true,
      discovered_via varchar(20) NOT NULL DEFAULT 'manual'
        CHECK (discovered_via IN ('manual','search','feed_discovery')),
      coverage_status varchar(30) NOT NULL DEFAULT 'PARTIALLY_AVAILABLE'
        CHECK (coverage_status IN ({coverage})),
      restrictions text NOT NULL DEFAULT '',
      mentions_count integer NOT NULL DEFAULT 0,
      last_collected_at timestamptz,
      last_checked_at timestamptz,
      last_error text,
      consecutive_errors integer NOT NULL DEFAULT 0,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    );
    CREATE UNIQUE INDEX sources_tenant_url_uq ON sources (tenant_id, kind, lower(url));
    CREATE INDEX sources_tenant_domain_idx ON sources (tenant_id, domain);

    CREATE TABLE mentions (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      brand_id uuid NOT NULL,
      project_id uuid,
      source_id uuid,
      first_run_id uuid,
      entity_type varchar(20) NOT NULL CHECK (entity_type IN ('brand','product','competitor')),
      entity_name varchar(200) NOT NULL,
      url varchar(2000) NOT NULL,
      canonical_url varchar(2000) NOT NULL,
      domain varchar(255) NOT NULL,
      title text NOT NULL DEFAULT '',
      snippet text NOT NULL DEFAULT '',     -- as returned by the search provider / feed
      original_text text NOT NULL DEFAULT '',  -- excerpt as published (never rewritten)
      normalized_text text,                 -- Milestone 4 (kept beside the original)
      language varchar(10),                 -- Milestone 4; NULL = not analysed
      matched_terms text[] NOT NULL DEFAULT '{{}}',
      publisher varchar(200),
      published_at timestamptz,             -- NULL when unknown: never invented
      published_at_source varchar(30),      -- where the date came from
      collected_at timestamptz NOT NULL DEFAULT now(),
      last_seen_at timestamptz NOT NULL DEFAULT now(),
      seen_count integer NOT NULL DEFAULT 1,
      collection_method varchar(30) NOT NULL
        CHECK (collection_method IN ('search_api','news_api','rss','page_fetch')),
      connector varchar(40) NOT NULL,
      connector_version varchar(20) NOT NULL,
      verification_status varchar(30) NOT NULL
        CHECK (verification_status IN ('verified_on_page','snippet_only','not_found_on_page',
                                       'page_unavailable','blocked_by_robots','feed_item')),
      content_hash char(64) NOT NULL,
      duplicate_of uuid REFERENCES mentions(id) ON DELETE SET NULL,
      raw jsonb NOT NULL DEFAULT '{{}}'::jsonb,   -- provider fields kept for provenance
      created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, project_id) REFERENCES monitoring_projects (tenant_id, id)
        ON DELETE SET NULL (project_id),
      FOREIGN KEY (tenant_id, source_id) REFERENCES sources (tenant_id, id)
        ON DELETE SET NULL (source_id)
    );
    CREATE UNIQUE INDEX mentions_brand_url_uq ON mentions (tenant_id, brand_id, canonical_url);
    CREATE INDEX mentions_tenant_time_idx ON mentions (tenant_id, collected_at DESC);
    CREATE INDEX mentions_hash_idx ON mentions (tenant_id, brand_id, content_hash);
    """)

    for t in ("sources", "mentions"):
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
        """)

    for key, name, method, basis, cats, restrictions, days in CONNECTORS:
        cat_sql = "ARRAY[" + ", ".join(_q(c) for c in cats) + "]::text[]"
        x(f"INSERT INTO connector_policies (key, name, access_method, legal_basis, "
          f"data_categories, restrictions, retention_days) VALUES ({_q(key)}, {_q(name)}, "
          f"{_q(method)}, {_q(basis)}, {cat_sql}, {_q(restrictions)}, {days})")

    x(f"""
    GRANT SELECT ON connector_policies TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE, DELETE ON sources, mentions TO {APP_ROLE};
    """)
    for key, (desc, roles) in NEW_PERMISSIONS.items():
        pid = _id("perm", key)
        x(f"INSERT INTO permissions (id, key, description) VALUES ('{pid}', '{key}', '{desc}')")
        for r in roles:
            x(f"INSERT INTO role_permissions VALUES ('{_id('role', r)}', '{pid}')")


def downgrade() -> None:
    perm_ids = ", ".join(f"'{_id('perm', k)}'" for k in NEW_PERMISSIONS)
    op.execute(f"""
    DELETE FROM role_permissions WHERE permission_id IN ({perm_ids});
    DELETE FROM permissions WHERE id IN ({perm_ids});
    DROP TABLE IF EXISTS mentions, sources, connector_policies;
    """)
