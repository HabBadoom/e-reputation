"""Aynpulse Scanner, phase 3: uploaded screenshots.

Revision ID: 0012_scanner_uploads
Revises: 0011_scanner_access
Create Date: 2026-11-21

One uploaded file per screenshot item. The image itself lives in private object storage under
`storage_key`; the database keeps its metadata: sanitised size and dimensions, SHA-256 (exact
duplicates are refused within a collection) and a 64-bit difference hash (near-duplicates are
flagged on the item, never merged automatically). Isolation by Scanner account, like every
Scanner table.
"""

import os

from alembic import op

revision = "0012_scanner_uploads"
down_revision = "0011_scanner_access"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")


def upgrade() -> None:
    op.execute(f"""
    ALTER TABLE scanner_collection_items
      ADD CONSTRAINT scanner_items_account_id_uq UNIQUE (account_id, id),
      ADD COLUMN possible_duplicate_of uuid,
      ADD CONSTRAINT scanner_items_duplicate_fk FOREIGN KEY (account_id, possible_duplicate_of)
        REFERENCES scanner_collection_items (account_id, id) ON DELETE SET NULL (possible_duplicate_of);

    CREATE TABLE scanner_uploaded_files (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL,
      session_id uuid NOT NULL,
      item_id uuid NOT NULL,
      storage_key varchar(300) NOT NULL UNIQUE,
      original_filename varchar(255) NOT NULL DEFAULT '',
      mime_type varchar(30) NOT NULL CHECK (mime_type IN ('image/png','image/jpeg','image/webp')),
      size_bytes integer NOT NULL CHECK (size_bytes > 0),
      width integer NOT NULL CHECK (width > 0),
      height integer NOT NULL CHECK (height > 0),
      sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
      dhash bigint NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      retention_until timestamptz,
      FOREIGN KEY (account_id, item_id)
        REFERENCES scanner_collection_items (account_id, id) ON DELETE CASCADE,
      FOREIGN KEY (account_id, session_id)
        REFERENCES scanner_collection_sessions (account_id, id) ON DELETE CASCADE,
      UNIQUE (session_id, sha256)
    );
    CREATE INDEX scanner_files_item_idx ON scanner_uploaded_files (item_id);

    ALTER TABLE scanner_uploaded_files ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scanner_uploaded_files FORCE ROW LEVEL SECURITY;
    CREATE POLICY scanner_account_isolation ON scanner_uploaded_files
      USING (account_id = app_current_scanner_account())
      WITH CHECK (account_id = app_current_scanner_account());

    GRANT SELECT, INSERT, DELETE ON scanner_uploaded_files TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS scanner_uploaded_files;
    ALTER TABLE scanner_collection_items
      DROP CONSTRAINT IF EXISTS scanner_items_duplicate_fk,
      DROP COLUMN IF EXISTS possible_duplicate_of,
      DROP CONSTRAINT IF EXISTS scanner_items_account_id_uq;
    """)
