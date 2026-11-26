"""Aynpulse Scanner, phase 6: how long screenshots are kept (decision D9, 2026-11-24).

Revision ID: 0017_scanner_retention
Revises: 0016_scanner_links
Create Date: 2026-11-25

- Images of a sent collection: kept 30 days after sending (`retention_until`, set when the
  collection is sent in phase 7), then deleted. Extracted texts went to AnyPulse.
- Collections never sent (open, or cancelled) without activity for 30 days: deleted with their
  images; the agent sees the date from 7 days before.

The daily maintenance job lists the accounts concerned through `scanner_retention_accounts`
(the only cross-account read) and then deletes inside each agent's own context (RLS).
"""

import os

from alembic import op

revision = "0017_scanner_retention"
down_revision = "0016_scanner_links"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")


def upgrade() -> None:
    op.execute(f"""
    CREATE INDEX scanner_files_retention_idx ON scanner_uploaded_files (retention_until)
      WHERE retention_until IS NOT NULL;

    CREATE FUNCTION scanner_retention_accounts(p_idle_days integer) RETURNS SETOF uuid
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT account_id FROM scanner_uploaded_files
        WHERE retention_until IS NOT NULL AND retention_until < now()
      UNION
      SELECT account_id FROM scanner_collection_sessions
        WHERE status IN ('draft', 'collecting', 'ready', 'cancelled')
          AND updated_at < now() - make_interval(days => p_idle_days)
    $$;
    REVOKE ALL ON FUNCTION scanner_retention_accounts(integer) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION scanner_retention_accounts(integer) TO {APP_ROLE};

    -- Phase 7 sets the deletion date of a sent collection's images.
    GRANT UPDATE (retention_until) ON scanner_uploaded_files TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
    REVOKE UPDATE (retention_until) ON scanner_uploaded_files FROM {APP_ROLE};
    DROP FUNCTION IF EXISTS scanner_retention_accounts(integer);
    DROP INDEX IF EXISTS scanner_files_retention_idx;
    """)
