"""Aynpulse Scanner, phase 4: text extracted from screenshots.

Revision ID: 0014_scanner_extraction
Revises: 0013_scanner_browser
Create Date: 2026-11-23

A vision model reads each screenshot and returns what is visible: the post (author, text, text
inside its image, counters) and the comments and replies (author, text, relative time). Scanner
stores that as collection data only: no sentiment, topic, brand or score.

- `scanner_extractions`: one row per item, the latest extraction run (status, model, tokens,
  raw model output for audit, context such as the page or group shown, quality issues).
- `scanner_extracted_entries`: one row per post / comment / reply, in reading order, with the
  thread link inside the screenshot (`parent_id`). Overlapping screenshots of the same thread
  give the same comment twice: the later copy points to the first (`duplicate_of`) and is not
  counted. The agent can correct author and text, or delete an entry, before sending.
"""

import os

from alembic import op

revision = "0014_scanner_extraction"
down_revision = "0013_scanner_browser"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE scanner_extractions (
      item_id uuid PRIMARY KEY,
      account_id uuid NOT NULL,
      session_id uuid NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','done','failed')),
      model varchar(100),
      attempts integer NOT NULL DEFAULT 0,
      input_tokens integer NOT NULL DEFAULT 0,
      output_tokens integer NOT NULL DEFAULT 0,
      captured_at timestamptz,
      context jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      issues jsonb NOT NULL DEFAULT '[]'::jsonb,
      confidence numeric(3,2) CHECK (confidence BETWEEN 0 AND 1),
      raw jsonb,
      error_code varchar(50),
      started_at timestamptz,
      finished_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (account_id, item_id)
        REFERENCES scanner_collection_items (account_id, id) ON DELETE CASCADE,
      FOREIGN KEY (account_id, session_id)
        REFERENCES scanner_collection_sessions (account_id, id) ON DELETE CASCADE
    );
    CREATE INDEX scanner_extractions_session_idx ON scanner_extractions (session_id);

    CREATE TABLE scanner_extracted_entries (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL,
      session_id uuid NOT NULL,
      item_id uuid NOT NULL,
      position integer NOT NULL CHECK (position >= 0),
      kind varchar(10) NOT NULL CHECK (kind IN ('post','comment','reply')),
      parent_id uuid,
      author_name varchar(200) NOT NULL DEFAULT '',
      author_is_page boolean NOT NULL DEFAULT false,
      text text NOT NULL DEFAULT '',
      image_text text NOT NULL DEFAULT '',
      script varchar(10) NOT NULL DEFAULT 'none'
        CHECK (script IN ('arabic','latin','mixed','none')),
      text_truncated boolean NOT NULL DEFAULT false,
      partially_visible boolean NOT NULL DEFAULT false,
      relative_time varchar(60) NOT NULL DEFAULT '',
      published_at timestamptz,
      published_precision varchar(10)
        CHECK (published_precision IN ('minute','hour','day','week','month','year')),
      reactions_count integer CHECK (reactions_count >= 0),
      comments_count integer CHECK (comments_count >= 0),
      shares_count integer CHECK (shares_count >= 0),
      dedup_key char(64) NOT NULL,
      duplicate_of uuid,
      edited_by_agent boolean NOT NULL DEFAULT false,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (account_id, id),
      UNIQUE (item_id, position),
      FOREIGN KEY (account_id, item_id)
        REFERENCES scanner_collection_items (account_id, id) ON DELETE CASCADE,
      FOREIGN KEY (account_id, session_id)
        REFERENCES scanner_collection_sessions (account_id, id) ON DELETE CASCADE,
      FOREIGN KEY (account_id, parent_id)
        REFERENCES scanner_extracted_entries (account_id, id) ON DELETE SET NULL (parent_id),
      FOREIGN KEY (account_id, duplicate_of)
        REFERENCES scanner_extracted_entries (account_id, id) ON DELETE SET NULL (duplicate_of),
      CHECK (kind = 'post' OR parent_id IS NULL OR parent_id <> id)
    );
    CREATE INDEX scanner_entries_session_idx
      ON scanner_extracted_entries (session_id, dedup_key);
    CREATE INDEX scanner_entries_item_idx ON scanner_extracted_entries (item_id, position);

    ALTER TABLE scanner_extractions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scanner_extractions FORCE ROW LEVEL SECURITY;
    CREATE POLICY scanner_account_isolation ON scanner_extractions
      USING (account_id = app_current_scanner_account())
      WITH CHECK (account_id = app_current_scanner_account());
    ALTER TABLE scanner_extracted_entries ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scanner_extracted_entries FORCE ROW LEVEL SECURITY;
    CREATE POLICY scanner_account_isolation ON scanner_extracted_entries
      USING (account_id = app_current_scanner_account())
      WITH CHECK (account_id = app_current_scanner_account());

    GRANT SELECT, INSERT, UPDATE, DELETE ON scanner_extractions TO {APP_ROLE};
    GRANT SELECT, INSERT, DELETE ON scanner_extracted_entries TO {APP_ROLE};
    -- The agent corrects author and text only; everything else comes from the extraction.
    GRANT UPDATE (author_name, text, edited_by_agent, duplicate_of, parent_id, updated_at)
      ON scanner_extracted_entries TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS scanner_extracted_entries;
    DROP TABLE IF EXISTS scanner_extractions;
    """)
