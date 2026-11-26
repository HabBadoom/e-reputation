"""Aynpulse Scanner, phase 2: AnyPulse access of each Scanner account, granted automatically at
sign-up and changed only by platform administrators.

Revision ID: 0011_scanner_access
Revises: 0010_scanner_foundation
Create Date: 2026-11-21

Decisions (2026-11-21):
  * Scanner users are collection agents: they add screenshots and URLs, never see analyses and
    never choose a brand.
  * Every account may send collections to AnyPulse as soon as it exists (no consent step); the
    collections of all agents go to a central RepIntel space and RepIntel's AI attributes each
    one to a brand, hence to a customer, afterwards (option A; ingestion comes in phase 7).
  * Only platform administrators revoke that access (with a reason shown to the agent) and
    may restore it.

The access columns live on `scanner_accounts`. The app role can no longer UPDATE them (column
grants) and a trigger makes every new account start with access granted, so neither an agent nor
a bug in the Scanner API can change them; only `admin_scanner_set_access()` can, for a platform
administrator.
"""

import os

from alembic import op

revision = "0011_scanner_access"
down_revision = "0010_scanner_foundation"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")
APP_UPDATABLE = "email, password_hash, full_name, locale, status, last_login_at"


def upgrade() -> None:
    x = op.execute

    x("""
    ALTER TABLE scanner_accounts
      ADD COLUMN anypulse_access varchar(10) NOT NULL DEFAULT 'active'
        CHECK (anypulse_access IN ('active','revoked')),
      ADD COLUMN access_revoked_at timestamptz,
      ADD COLUMN access_revoked_by uuid REFERENCES users(id) ON DELETE SET NULL,
      ADD COLUMN access_revoke_reason varchar(500) NOT NULL DEFAULT '',
      ADD CONSTRAINT scanner_accounts_access_consistent
        CHECK ((anypulse_access = 'active') = (access_revoked_at IS NULL));

    -- New accounts always start with access granted, whatever the INSERT says.
    CREATE FUNCTION scanner_accounts_access_default() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      NEW.anypulse_access := 'active';
      NEW.access_revoked_at := NULL;
      NEW.access_revoked_by := NULL;
      NEW.access_revoke_reason := '';
      RETURN NEW;
    END $$;
    CREATE TRIGGER scanner_accounts_access_default BEFORE INSERT ON scanner_accounts
      FOR EACH ROW EXECUTE FUNCTION scanner_accounts_access_default();
    """)

    x(f"""
    REVOKE UPDATE ON scanner_accounts FROM {APP_ROLE};
    GRANT UPDATE ({APP_UPDATABLE}) ON scanner_accounts TO {APP_ROLE};
    """)

    # Platform administration (app_is_platform_admin(), migration 0005).
    x("""
    CREATE FUNCTION admin_scanner_accounts() RETURNS TABLE (
        account_id uuid, email text, full_name text, account_status text,
        created_at timestamptz, last_login_at timestamptz, anypulse_access text,
        access_revoked_at timestamptz, access_revoke_reason text, collections bigint)
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      BEGIN
        IF NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'platform administrators only' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY
        SELECT a.id, a.email::text, a.full_name::text, a.status::text, a.created_at,
               a.last_login_at, a.anypulse_access::text, a.access_revoked_at,
               a.access_revoke_reason::text,
               (SELECT count(*) FROM scanner_collection_sessions s WHERE s.account_id = a.id)
          FROM scanner_accounts a
         ORDER BY a.created_at DESC;
      END $$;

    CREATE FUNCTION admin_scanner_set_access(p_account uuid, p_active boolean, p_reason text)
      RETURNS void
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      BEGIN
        IF NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'platform administrators only' USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF p_active THEN
          UPDATE scanner_accounts SET anypulse_access = 'active', access_revoked_at = NULL,
                 access_revoked_by = NULL, access_revoke_reason = ''
           WHERE id = p_account;
        ELSE
          UPDATE scanner_accounts SET anypulse_access = 'revoked',
                 access_revoked_at = coalesce(access_revoked_at, now()),
                 access_revoked_by = app_current_user(),
                 access_revoke_reason = left(coalesce(p_reason, ''), 500)
           WHERE id = p_account;
        END IF;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'Scanner account not found' USING ERRCODE = 'no_data_found';
        END IF;
      END $$;

    REVOKE ALL ON FUNCTION admin_scanner_accounts(), admin_scanner_set_access(uuid, boolean, text)
      FROM PUBLIC;
    """)
    x(f"""
    GRANT EXECUTE ON FUNCTION admin_scanner_accounts(),
      admin_scanner_set_access(uuid, boolean, text) TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute(f"""
    DROP FUNCTION IF EXISTS admin_scanner_set_access(uuid, boolean, text);
    DROP FUNCTION IF EXISTS admin_scanner_accounts();
    DROP TRIGGER IF EXISTS scanner_accounts_access_default ON scanner_accounts;
    DROP FUNCTION IF EXISTS scanner_accounts_access_default();
    ALTER TABLE scanner_accounts
      DROP CONSTRAINT IF EXISTS scanner_accounts_access_consistent,
      DROP COLUMN IF EXISTS access_revoke_reason,
      DROP COLUMN IF EXISTS access_revoked_by,
      DROP COLUMN IF EXISTS access_revoked_at,
      DROP COLUMN IF EXISTS anypulse_access;
    GRANT UPDATE ON scanner_accounts TO {APP_ROLE};
    """)
