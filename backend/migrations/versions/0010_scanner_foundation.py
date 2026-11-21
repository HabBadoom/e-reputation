"""Aynpulse Scanner foundation: Scanner accounts (one account type, separate from AnyPulse users), their refresh
tokens, collection sessions and items, and an append-only Scanner audit trail.

Revision ID: 0010_scanner_foundation
Revises: 0009_billing
Create Date: 2026-11-21

Scanner is a collection module. It has its own accounts (decision of 2026-11-21) and never
reads or writes AnyPulse analytical tables. Isolation is by Scanner account:

  * every Scanner table has RLS enabled and FORCED;
  * policies compare `account_id` to `app.scanner_account_id` (transaction-local, set by the
    API from the signed Scanner token); unset -> NULL -> no rows (fail closed);
  * an AnyPulse request never sets `app.scanner_account_id`, and a Scanner request never sets
    `app.tenant_id` / `app.user_id`, so neither side can see the other's rows.

Linking a Scanner account to an AnyPulse organization (needed before transmission) comes in a
later migration with the AnyPulse integration.
"""

import os

from alembic import op

revision = "0010_scanner_foundation"
down_revision = "0009_billing"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

SESSION_STATUSES = ("draft", "collecting", "ready", "validating", "queued", "transmitting",
                    "transmitted", "acknowledged", "failed", "retrying", "cancelled")
OPEN_STATUSES = ("draft", "collecting", "ready")
PLATFORMS = ("facebook", "instagram", "tiktok", "youtube", "linkedin", "x", "web", "other")
ACCOUNT_TABLES = ["scanner_refresh_tokens", "scanner_collection_sessions",
                  "scanner_collection_items", "scanner_audit_logs"]


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    x = op.execute

    x("""
    CREATE FUNCTION app_current_scanner_account() RETURNS uuid LANGUAGE sql STABLE AS
    $$ SELECT nullif(current_setting('app.scanner_account_id', true), '')::uuid $$;
    """)

    x("""
    CREATE TABLE scanner_accounts (
      id uuid PRIMARY KEY,
      email varchar(320) NOT NULL CHECK (email = lower(email) AND position('@' in email) > 1),
      password_hash text NOT NULL,
      full_name varchar(200) NOT NULL CHECK (length(trim(full_name)) > 0),
      locale varchar(5) NOT NULL DEFAULT 'fr' CHECK (locale IN ('fr','ar','en')),
      status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
      last_login_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX scanner_accounts_email_uq ON scanner_accounts (email);

    CREATE TABLE scanner_refresh_tokens (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL REFERENCES scanner_accounts(id) ON DELETE CASCADE,
      token_hash varchar(64) NOT NULL UNIQUE,
      family_id uuid NOT NULL,
      expires_at timestamptz NOT NULL,
      revoked_at timestamptz,
      user_agent varchar(400),
      ip inet,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX scanner_refresh_tokens_family_idx ON scanner_refresh_tokens (family_id);
    CREATE INDEX scanner_refresh_tokens_account_idx ON scanner_refresh_tokens (account_id);
    """)

    x(f"""
    CREATE TABLE scanner_collection_sessions (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL REFERENCES scanner_accounts(id) ON DELETE CASCADE,
      status varchar(20) NOT NULL DEFAULT 'draft' CHECK (status IN ({_in(SESSION_STATUSES)})),
      title varchar(200) NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      transmitted_at timestamptz,
      anypulse_collection_id uuid,
      UNIQUE (account_id, id)
    );
    -- At most one open session per account: the "active collection" the UI always returns to.
    CREATE UNIQUE INDEX scanner_sessions_one_open_uq ON scanner_collection_sessions (account_id)
      WHERE status IN ({_in(OPEN_STATUSES)});
    CREATE INDEX scanner_sessions_account_time_idx
      ON scanner_collection_sessions (account_id, created_at DESC);

    CREATE TABLE scanner_collection_items (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL,
      session_id uuid NOT NULL,
      source_type varchar(20) NOT NULL CHECK (source_type IN ('screenshot','url','import')),
      platform varchar(20) CHECK (platform IN ({_in(PLATFORMS)})),
      content_type varchar(30)
        CHECK (content_type IN ('post','comments','post_and_comments','profile','unknown')),
      position integer NOT NULL CHECK (position >= 0),
      status varchar(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','ready','needs_review','failed')),
      source_url varchar(2000),
      error_code varchar(50),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (account_id, session_id)
        REFERENCES scanner_collection_sessions (account_id, id) ON DELETE CASCADE,
      CHECK (source_type <> 'url' OR source_url IS NOT NULL)
    );
    CREATE INDEX scanner_items_session_idx ON scanner_collection_items (session_id, position);

    CREATE TABLE scanner_audit_logs (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL REFERENCES scanner_accounts(id) ON DELETE CASCADE,
      action varchar(80) NOT NULL,
      target_type varchar(50),
      target_id uuid,
      metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      ip inet,
      request_id varchar(64),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX scanner_audit_logs_account_time_idx
      ON scanner_audit_logs (account_id, created_at DESC);
    """)

    for t in ["scanner_accounts", *ACCOUNT_TABLES]:
        x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY; ALTER TABLE {t} FORCE ROW LEVEL SECURITY;")

    x("""
    CREATE POLICY scanner_accounts_self ON scanner_accounts
      USING (id = app_current_scanner_account())
      WITH CHECK (id = app_current_scanner_account());
    """)
    for t in ACCOUNT_TABLES:
        x(f"""
        CREATE POLICY scanner_account_isolation ON {t}
          USING (account_id = app_current_scanner_account())
          WITH CHECK (account_id = app_current_scanner_account());
        """)

    # Narrow lookups for the steps that run before a Scanner context exists (login, refresh).
    x("""
    CREATE FUNCTION scanner_auth_find_account_by_email(p_email text)
      RETURNS TABLE (id uuid, password_hash text, status varchar)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT a.id, a.password_hash, a.status FROM scanner_accounts a
         WHERE a.email = lower(p_email) $$;

    CREATE FUNCTION scanner_auth_find_refresh_token(p_hash text)
      RETURNS TABLE (id uuid, account_id uuid, family_id uuid,
                     expires_at timestamptz, revoked_at timestamptz)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT r.id, r.account_id, r.family_id, r.expires_at, r.revoked_at
         FROM scanner_refresh_tokens r WHERE r.token_hash = p_hash $$;

    REVOKE ALL ON FUNCTION scanner_auth_find_account_by_email(text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION scanner_auth_find_refresh_token(text) FROM PUBLIC;
    """)

    x(f"""
    GRANT SELECT, INSERT, UPDATE ON scanner_accounts TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE, DELETE ON scanner_refresh_tokens, scanner_collection_sessions,
      scanner_collection_items TO {APP_ROLE};
    GRANT SELECT, INSERT ON scanner_audit_logs TO {APP_ROLE};   -- append-only for the app
    GRANT EXECUTE ON FUNCTION app_current_scanner_account(),
      scanner_auth_find_account_by_email(text), scanner_auth_find_refresh_token(text)
      TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS scanner_auth_find_refresh_token(text);
    DROP FUNCTION IF EXISTS scanner_auth_find_account_by_email(text);
    DROP TABLE IF EXISTS scanner_audit_logs, scanner_collection_items,
      scanner_collection_sessions, scanner_refresh_tokens, scanner_accounts CASCADE;
    DROP FUNCTION IF EXISTS app_current_scanner_account();
    """)
