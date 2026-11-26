"""Aynpulse Scanner, phase 5: links pasted by the agent, and captures taken from them.

Revision ID: 0016_scanner_links
Revises: 0015_scanner_validation
Create Date: 2026-11-24

A pasted link is a `url` item of the collection. It opens in the built-in browser; captures
taken from it point to it (`link_item_id`). `url_key` (SHA-256 of the normalised address) makes
the same link once per collection.
"""

from alembic import op

revision = "0016_scanner_links"
down_revision = "0015_scanner_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE scanner_collection_items
      ADD COLUMN link_item_id uuid,
      ADD COLUMN url_key char(64),
      ADD CONSTRAINT scanner_items_link_fk FOREIGN KEY (account_id, link_item_id)
        REFERENCES scanner_collection_items (account_id, id) ON DELETE SET NULL (link_item_id),
      ADD CONSTRAINT scanner_items_link_not_self CHECK (link_item_id IS NULL OR link_item_id <> id),
      ADD CONSTRAINT scanner_items_url_key CHECK (source_type <> 'url' OR url_key IS NOT NULL);
    CREATE UNIQUE INDEX scanner_items_url_once
      ON scanner_collection_items (session_id, url_key) WHERE source_type = 'url';
    CREATE INDEX scanner_items_link_idx
      ON scanner_collection_items (link_item_id) WHERE link_item_id IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
    DROP INDEX IF EXISTS scanner_items_link_idx;
    DROP INDEX IF EXISTS scanner_items_url_once;
    ALTER TABLE scanner_collection_items
      DROP CONSTRAINT IF EXISTS scanner_items_url_key,
      DROP CONSTRAINT IF EXISTS scanner_items_link_not_self,
      DROP CONSTRAINT IF EXISTS scanner_items_link_fk,
      DROP COLUMN IF EXISTS url_key,
      DROP COLUMN IF EXISTS link_item_id;
    """)
