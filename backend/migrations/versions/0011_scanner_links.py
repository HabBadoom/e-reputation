"""Aynpulse Scanner, phase 2: every Scanner account is linked automatically to its own AnyPulse
organization, for as long as a platform administrator does not revoke it; each collection gets
a destination brand in that organization.

Revision ID: 0011_scanner_links
Revises: 0010_scanner_foundation
Create Date: 2026-11-21

Decisions (2026-11-21): no consent step; one organization per Scanner account, created when
the account is created; only platform administrators revoke (and may restore) the access.

  * `scanner_links` holds the pair (Scanner account, organization). One per account and one
    per organization. It can only be inserted in the provisioning context — the Scanner account
    *and* the new organization set at once — and only for a brand-new organization (no member
    yet, created minutes ago), so no code path can attach a Scanner account to an existing
    customer organization.
  * Both parties can read the link; nobody but a platform administrator changes its status,
    through `admin_scanner_set_access()`.
  * The collection destination (link + brand) is checked by a trigger: the account's own active
    link and an active brand of its organization.

A link lets Scanner list and create brands of its organization and, from phase 7, send
collections to it. It gives no read access to mentions, analyses or reports.
"""

import os

from alembic import op

revision = "0011_scanner_links"
down_revision = "0010_scanner_foundation"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")


def upgrade() -> None:
    x = op.execute

    x("""
    CREATE TABLE scanner_links (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL UNIQUE REFERENCES scanner_accounts(id) ON DELETE CASCADE,
      tenant_id uuid NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
      tenant_name varchar(200) NOT NULL,   -- snapshot: Scanner cannot read `tenants`
      status varchar(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
      created_at timestamptz NOT NULL DEFAULT now(),
      revoked_at timestamptz,
      revoked_by uuid REFERENCES users(id) ON DELETE SET NULL,
      revoke_reason varchar(500) NOT NULL DEFAULT '',
      UNIQUE (account_id, id),
      CHECK ((status = 'active') = (revoked_at IS NULL))
    );

    -- Collection destination. ON DELETE SET NULL (link_id) keeps account_id when the link
    -- disappears with its organization.
    ALTER TABLE scanner_collection_sessions
      ADD COLUMN link_id uuid,
      ADD COLUMN anypulse_brand_id uuid REFERENCES brands(id) ON DELETE SET NULL,
      ADD COLUMN anypulse_brand_name varchar(200),
      ADD CONSTRAINT scanner_sessions_link_fk FOREIGN KEY (account_id, link_id)
        REFERENCES scanner_links (account_id, id) ON DELETE SET NULL (link_id),
      ADD CONSTRAINT scanner_sessions_brand_needs_link
        CHECK (anypulse_brand_id IS NULL OR link_id IS NOT NULL);

    ALTER TABLE scanner_links ENABLE ROW LEVEL SECURITY;
    ALTER TABLE scanner_links FORCE ROW LEVEL SECURITY;
    CREATE POLICY scanner_links_select ON scanner_links FOR SELECT
      USING (account_id = app_current_scanner_account() OR tenant_id = app_current_tenant());
    -- Provisioning context only: both the Scanner account and the new organization are set.
    CREATE POLICY scanner_links_provision ON scanner_links FOR INSERT
      WITH CHECK (account_id = app_current_scanner_account()
                  AND tenant_id = app_current_tenant());
    """)

    x("""
    -- The organization must be brand new: no member yet and created within the last 10 minutes.
    CREATE FUNCTION scanner_links_provision_guard() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    BEGIN
      IF EXISTS (SELECT 1 FROM memberships m WHERE m.tenant_id = NEW.tenant_id)
         OR NOT EXISTS (SELECT 1 FROM tenants t WHERE t.id = NEW.tenant_id
                          AND t.created_at > now() - interval '10 minutes') THEN
        RAISE EXCEPTION 'a Scanner account can only be linked to the organization created for it'
          USING ERRCODE = 'check_violation';
      END IF;
      NEW.status := 'active';
      NEW.revoked_at := NULL;
      NEW.revoked_by := NULL;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION scanner_links_provision_guard() FROM PUBLIC;
    CREATE TRIGGER scanner_links_provision_guard BEFORE INSERT ON scanner_links
      FOR EACH ROW EXECUTE FUNCTION scanner_links_provision_guard();
    """)

    # Destination check: the account's own active link, and an active brand of its
    # organization. SECURITY DEFINER because a Scanner context cannot read `brands`.
    x("""
    CREATE FUNCTION scanner_sessions_destination_check() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE l scanner_links;
    BEGIN
      IF NEW.link_id IS NULL THEN
        RETURN NEW;
      END IF;
      IF TG_OP = 'UPDATE' AND NEW.link_id IS NOT DISTINCT FROM OLD.link_id
         AND (NEW.anypulse_brand_id IS NULL
              OR NEW.anypulse_brand_id IS NOT DISTINCT FROM OLD.anypulse_brand_id) THEN
        RETURN NEW;   -- unchanged destination, or a brand removed by ON DELETE SET NULL
      END IF;
      SELECT * INTO l FROM scanner_links WHERE id = NEW.link_id;
      IF NOT FOUND OR l.status <> 'active' OR l.account_id <> NEW.account_id THEN
        RAISE EXCEPTION 'collection destination: access to AnyPulse is not active for this account'
          USING ERRCODE = 'check_violation';
      END IF;
      IF NEW.anypulse_brand_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM brands b WHERE b.id = NEW.anypulse_brand_id
             AND b.tenant_id = l.tenant_id AND b.status = 'active') THEN
        RAISE EXCEPTION 'collection destination: not an active brand of the account''s organization'
          USING ERRCODE = 'check_violation';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION scanner_sessions_destination_check() FROM PUBLIC;
    CREATE TRIGGER scanner_sessions_destination_check
      BEFORE INSERT OR UPDATE OF link_id, anypulse_brand_id ON scanner_collection_sessions
      FOR EACH ROW EXECUTE FUNCTION scanner_sessions_destination_check();
    """)

    x("""
    -- Used by the integration endpoints after they have verified a signed Scanner call.
    CREATE FUNCTION integration_scanner_account(p_account uuid)
      RETURNS TABLE (email varchar, full_name varchar, locale varchar, status varchar,
                     link_id uuid, tenant_id uuid, link_status varchar)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT a.email, a.full_name, a.locale, a.status, l.id, l.tenant_id, l.status
        FROM scanner_accounts a LEFT JOIN scanner_links l ON l.account_id = a.id
       WHERE a.id = p_account
    $$;

    CREATE FUNCTION integration_scanner_link(p_link uuid)
      RETURNS TABLE (tenant_id uuid, account_id uuid, status varchar, tenant_active boolean,
                     account_active boolean)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT l.tenant_id, l.account_id, l.status, t.status = 'active', a.status = 'active'
        FROM scanner_links l JOIN tenants t ON t.id = l.tenant_id
        JOIN scanner_accounts a ON a.id = l.account_id
       WHERE l.id = p_link
    $$;

    -- Platform administration (app_is_platform_admin(), migration 0005).
    CREATE FUNCTION admin_scanner_accounts() RETURNS TABLE (
        account_id uuid, email text, full_name text, account_status text,
        created_at timestamptz, last_login_at timestamptz, link_id uuid, tenant_id uuid,
        organization_name text, link_status text, revoked_at timestamptz, revoke_reason text,
        collections bigint)
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      BEGIN
        IF NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'platform administrators only' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY
        SELECT a.id, a.email::text, a.full_name::text, a.status::text, a.created_at,
               a.last_login_at, l.id, l.tenant_id, t.name::text, l.status::text, l.revoked_at,
               l.revoke_reason::text,
               (SELECT count(*) FROM scanner_collection_sessions s WHERE s.account_id = a.id)
          FROM scanner_accounts a
          LEFT JOIN scanner_links l ON l.account_id = a.id
          LEFT JOIN tenants t ON t.id = l.tenant_id
         ORDER BY a.created_at DESC;
      END $$;

    CREATE FUNCTION admin_scanner_set_access(p_account uuid, p_active boolean, p_reason text)
      RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      DECLARE l scanner_links;
      BEGIN
        IF NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'platform administrators only' USING ERRCODE = 'insufficient_privilege';
        END IF;
        SELECT * INTO l FROM scanner_links WHERE account_id = p_account FOR UPDATE;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'no AnyPulse link for this Scanner account' USING ERRCODE = 'no_data_found';
        END IF;
        IF p_active THEN
          UPDATE scanner_links SET status = 'active', revoked_at = NULL, revoked_by = NULL,
                 revoke_reason = '' WHERE id = l.id;
        ELSE
          UPDATE scanner_links SET status = 'revoked', revoked_at = coalesce(l.revoked_at, now()),
                 revoked_by = app_current_user(), revoke_reason = left(coalesce(p_reason, ''), 500)
           WHERE id = l.id;
        END IF;
        RETURN l.tenant_id;
      END $$;

    REVOKE ALL ON FUNCTION integration_scanner_account(uuid), integration_scanner_link(uuid),
      admin_scanner_accounts(), admin_scanner_set_access(uuid, boolean, text) FROM PUBLIC;
    """)

    x(f"""
    GRANT SELECT, INSERT ON scanner_links TO {APP_ROLE};
    GRANT EXECUTE ON FUNCTION integration_scanner_account(uuid), integration_scanner_link(uuid),
      admin_scanner_accounts(), admin_scanner_set_access(uuid, boolean, text) TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS admin_scanner_set_access(uuid, boolean, text);
    DROP FUNCTION IF EXISTS admin_scanner_accounts();
    DROP FUNCTION IF EXISTS integration_scanner_link(uuid);
    DROP FUNCTION IF EXISTS integration_scanner_account(uuid);
    DROP TRIGGER IF EXISTS scanner_sessions_destination_check ON scanner_collection_sessions;
    DROP FUNCTION IF EXISTS scanner_sessions_destination_check();
    ALTER TABLE scanner_collection_sessions
      DROP CONSTRAINT IF EXISTS scanner_sessions_brand_needs_link,
      DROP CONSTRAINT IF EXISTS scanner_sessions_link_fk,
      DROP COLUMN IF EXISTS anypulse_brand_name,
      DROP COLUMN IF EXISTS anypulse_brand_id,
      DROP COLUMN IF EXISTS link_id;
    DROP TABLE IF EXISTS scanner_links CASCADE;
    DROP FUNCTION IF EXISTS scanner_links_provision_guard();
    """)
