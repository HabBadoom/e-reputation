"""Aynpulse Scanner: built-in browser profile (the agent's signed-in sessions, encrypted).

Revision ID: 0013_scanner_browser
Revises: 0012_scanner_uploads
Create Date: 2026-11-21

The built-in browser keeps the agent's cookies and local storage between sessions, so they do
not sign in to their dedicated social-media account every time. Passwords are never stored: only
the browser state the sites themselves set, encrypted with the application key
(REPINTEL_ENCRYPTION_KEYS, Fernet). One row per Scanner account; the agent can delete it.
"""

import os

from alembic import op

revision = "0013_scanner_browser"
down_revision = "0012_scanner_uploads"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE scanner_browser_profiles (
      account_id uuid PRIMARY KEY REFERENCES scanner_accounts(id) ON DELETE CASCADE,
      state_encrypted text NOT NULL,
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    ALTER TABLE scanner_browser_profiles ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scanner_browser_profiles FORCE ROW LEVEL SECURITY;
    CREATE POLICY scanner_account_isolation ON scanner_browser_profiles
      USING (account_id = app_current_scanner_account())
      WITH CHECK (account_id = app_current_scanner_account());
    GRANT SELECT, INSERT, UPDATE, DELETE ON scanner_browser_profiles TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scanner_browser_profiles;")
