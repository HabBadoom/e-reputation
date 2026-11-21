"""Aynpulse Scanner, phase 2: link a Scanner account to an AnyPulse organization, and give
each collection a destination (link + AnyPulse brand).

Revision ID: 0011_scanner_links
Revises: 0010_scanner_foundation
Create Date: 2026-11-21

Flow (an authorization grant, like OAuth consent):
  1. Scanner creates a link request for its account (random token, stored hashed, 15 min).
  2. A member of an AnyPulse organization holding `scanner:link` opens the request in AnyPulse,
     sees which Scanner account asks, and approves (or declines) it for their *current*
     organization. Approval runs in `scanner_link_approve()`, which re-checks the AnyPulse
     context and the permission itself.
  3. The resulting `scanner_links` row is visible to both sides (the Scanner account and the
     organization) and either side can revoke it. Nobody can insert or re-activate a link
     directly.

A link only lets the Scanner account list the organization's brand names and, from phase 7,
send collections into it. It never gives read access to AnyPulse analyses.
"""

import os
import uuid

from alembic import op

revision = "0011_scanner_links"
down_revision = "0010_scanner_foundation"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

NEW_PERMISSIONS = {
    "scanner:link": ("Approve and revoke Aynpulse Scanner links", ["owner", "admin"]),
}


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:{kind}:{key}")


def upgrade() -> None:
    x = op.execute

    x("""
    CREATE TABLE scanner_links (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL REFERENCES scanner_accounts(id) ON DELETE CASCADE,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      -- snapshots shown to the other side, which cannot read the source rows
      account_email varchar(320) NOT NULL,
      account_name varchar(200) NOT NULL,
      tenant_name varchar(200) NOT NULL,
      status varchar(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
      approved_by uuid REFERENCES users(id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      revoked_at timestamptz,
      revoked_by varchar(10) CHECK (revoked_by IN ('scanner','anypulse')),
      UNIQUE (account_id, id),
      CHECK ((status = 'active') = (revoked_at IS NULL))
    );
    CREATE UNIQUE INDEX scanner_links_one_active_uq ON scanner_links (account_id, tenant_id)
      WHERE status = 'active';
    CREATE INDEX scanner_links_tenant_idx ON scanner_links (tenant_id, created_at DESC);

    CREATE TABLE scanner_link_requests (
      id uuid PRIMARY KEY,
      account_id uuid NOT NULL REFERENCES scanner_accounts(id) ON DELETE CASCADE,
      token_hash varchar(64) NOT NULL UNIQUE,
      status varchar(10) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','declined')),
      expires_at timestamptz NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      decided_at timestamptz,
      decided_by uuid REFERENCES users(id) ON DELETE SET NULL,
      tenant_id uuid REFERENCES tenants(id) ON DELETE SET NULL,
      link_id uuid REFERENCES scanner_links(id) ON DELETE SET NULL
    );
    CREATE INDEX scanner_link_requests_account_idx
      ON scanner_link_requests (account_id, created_at DESC);

    -- Collection destination. ON DELETE SET NULL (link_id) keeps account_id when a link
    -- disappears with its organization.
    ALTER TABLE scanner_collection_sessions
      ADD COLUMN link_id uuid,
      ADD COLUMN anypulse_brand_id uuid REFERENCES brands(id) ON DELETE SET NULL,
      ADD COLUMN anypulse_brand_name varchar(200),
      ADD CONSTRAINT scanner_sessions_link_fk FOREIGN KEY (account_id, link_id)
        REFERENCES scanner_links (account_id, id) ON DELETE SET NULL (link_id),
      ADD CONSTRAINT scanner_sessions_brand_needs_link
        CHECK (anypulse_brand_id IS NULL OR link_id IS NOT NULL);
    """)

    for t in ("scanner_links", "scanner_link_requests"):
        x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY; ALTER TABLE {t} FORCE ROW LEVEL SECURITY;")

    x("""
    -- Both sides see a link; either side may revoke it (column grants below limit what an
    -- UPDATE can touch). No INSERT/DELETE policy: links are created by scanner_link_approve().
    CREATE POLICY scanner_links_select ON scanner_links FOR SELECT
      USING (account_id = app_current_scanner_account() OR tenant_id = app_current_tenant());
    CREATE POLICY scanner_links_update ON scanner_links FOR UPDATE
      USING (account_id = app_current_scanner_account() OR tenant_id = app_current_tenant())
      WITH CHECK (account_id = app_current_scanner_account()
                  OR tenant_id = app_current_tenant());

    CREATE POLICY scanner_link_requests_own ON scanner_link_requests
      USING (account_id = app_current_scanner_account())
      WITH CHECK (account_id = app_current_scanner_account());

    -- A revoked link stays revoked; revocation records when.
    CREATE FUNCTION scanner_links_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.status = 'revoked' AND NEW.status <> 'revoked' THEN
        RAISE EXCEPTION 'a revoked Scanner link cannot be re-activated'
          USING ERRCODE = 'check_violation';
      END IF;
      IF OLD.status = 'active' AND NEW.status = 'revoked' THEN
        NEW.revoked_at := coalesce(NEW.revoked_at, now());
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER scanner_links_guard BEFORE UPDATE ON scanner_links
      FOR EACH ROW EXECUTE FUNCTION scanner_links_guard();
    """)

    # Destination check: a collection can only point at an active link of its own account,
    # and at a brand of that link's organization. SECURITY DEFINER because a Scanner context
    # cannot read `brands`.
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
        RAISE EXCEPTION 'collection destination: link is not active for this account'
          USING ERRCODE = 'check_violation';
      END IF;
      IF NEW.anypulse_brand_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM brands b WHERE b.id = NEW.anypulse_brand_id
             AND b.tenant_id = l.tenant_id AND b.status = 'active') THEN
        RAISE EXCEPTION 'collection destination: brand is not an active brand of the linked organization'
          USING ERRCODE = 'check_violation';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION scanner_sessions_destination_check() FROM PUBLIC;
    CREATE TRIGGER scanner_sessions_destination_check
      BEFORE INSERT OR UPDATE OF link_id, anypulse_brand_id ON scanner_collection_sessions
      FOR EACH ROW EXECUTE FUNCTION scanner_sessions_destination_check();
    """)

    # AnyPulse-side functions. Each requires an AnyPulse context (tenant + user), and the
    # deciding ones re-check the `scanner:link` permission in the database.
    x("""
    CREATE FUNCTION scanner_link_can_decide() RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT app_current_tenant() IS NOT NULL AND app_current_user() IS NOT NULL AND EXISTS (
        SELECT 1 FROM memberships m
          JOIN users u ON u.id = m.user_id
          JOIN tenants t ON t.id = m.tenant_id
          JOIN role_permissions rp ON rp.role_id = m.role_id
          JOIN permissions p ON p.id = rp.permission_id
         WHERE m.tenant_id = app_current_tenant() AND m.user_id = app_current_user()
           AND m.status = 'active' AND u.status = 'active' AND t.status = 'active'
           AND p.key = 'scanner:link')
    $$;

    -- What the consent screen shows: which Scanner account asks, and whether the request is
    -- still usable. Nothing without an AnyPulse context.
    CREATE FUNCTION scanner_link_request_peek(p_hash text)
      RETURNS TABLE (id uuid, status varchar, expires_at timestamptz,
                     account_email varchar, account_name varchar)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT r.id, r.status, r.expires_at, a.email, a.full_name
        FROM scanner_link_requests r JOIN scanner_accounts a ON a.id = r.account_id
       WHERE r.token_hash = p_hash AND a.status = 'active'
         AND app_current_tenant() IS NOT NULL AND app_current_user() IS NOT NULL
    $$;

    CREATE FUNCTION scanner_link_approve(p_hash text) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE
      r scanner_link_requests;
      a scanner_accounts;
      v_link uuid;
    BEGIN
      IF NOT scanner_link_can_decide() THEN
        RAISE EXCEPTION 'missing permission scanner:link' USING ERRCODE = 'insufficient_privilege';
      END IF;
      SELECT * INTO r FROM scanner_link_requests WHERE token_hash = p_hash FOR UPDATE;
      IF NOT FOUND OR r.status <> 'pending' OR r.expires_at <= now() THEN
        RAISE EXCEPTION 'link request not found or no longer valid' USING ERRCODE = 'no_data_found';
      END IF;
      SELECT * INTO a FROM scanner_accounts WHERE id = r.account_id;
      IF a.status <> 'active' THEN
        RAISE EXCEPTION 'link request not found or no longer valid' USING ERRCODE = 'no_data_found';
      END IF;
      SELECT l.id INTO v_link FROM scanner_links l
       WHERE l.account_id = r.account_id AND l.tenant_id = app_current_tenant()
         AND l.status = 'active';
      IF v_link IS NULL THEN
        v_link := gen_random_uuid();
        INSERT INTO scanner_links (id, account_id, tenant_id, account_email, account_name,
                                   tenant_name, approved_by)
        SELECT v_link, a.id, t.id, a.email, a.full_name, t.name, app_current_user()
          FROM tenants t WHERE t.id = app_current_tenant();
      END IF;
      UPDATE scanner_link_requests
         SET status = 'approved', decided_at = now(), decided_by = app_current_user(),
             tenant_id = app_current_tenant(), link_id = v_link
       WHERE id = r.id;
      RETURN v_link;
    END $$;

    CREATE FUNCTION scanner_link_decline(p_hash text) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    BEGIN
      IF NOT scanner_link_can_decide() THEN
        RAISE EXCEPTION 'missing permission scanner:link' USING ERRCODE = 'insufficient_privilege';
      END IF;
      UPDATE scanner_link_requests
         SET status = 'declined', decided_at = now(), decided_by = app_current_user()
       WHERE token_hash = p_hash AND status = 'pending' AND expires_at > now();
      RETURN FOUND;
    END $$;

    -- Used by the integration endpoint after it has verified the signed request: the link's
    -- organization and owner, nothing else.
    CREATE FUNCTION integration_scanner_link(p_link uuid)
      RETURNS TABLE (tenant_id uuid, account_id uuid, status varchar, tenant_active boolean)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT l.tenant_id, l.account_id, l.status, t.status = 'active'
        FROM scanner_links l JOIN tenants t ON t.id = l.tenant_id WHERE l.id = p_link
    $$;

    REVOKE ALL ON FUNCTION scanner_link_can_decide(), scanner_link_request_peek(text),
      scanner_link_approve(text), scanner_link_decline(text), integration_scanner_link(uuid)
      FROM PUBLIC;
    """)

    x(f"""
    GRANT SELECT ON scanner_links TO {APP_ROLE};
    GRANT UPDATE (status, revoked_at, revoked_by) ON scanner_links TO {APP_ROLE};
    GRANT SELECT, INSERT ON scanner_link_requests TO {APP_ROLE};
    GRANT EXECUTE ON FUNCTION scanner_link_can_decide(), scanner_link_request_peek(text),
      scanner_link_approve(text), scanner_link_decline(text), integration_scanner_link(uuid)
      TO {APP_ROLE};
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
    DROP FUNCTION IF EXISTS integration_scanner_link(uuid);
    DROP FUNCTION IF EXISTS scanner_link_decline(text);
    DROP FUNCTION IF EXISTS scanner_link_approve(text);
    DROP FUNCTION IF EXISTS scanner_link_request_peek(text);
    DROP FUNCTION IF EXISTS scanner_link_can_decide();
    DROP TRIGGER IF EXISTS scanner_sessions_destination_check ON scanner_collection_sessions;
    DROP FUNCTION IF EXISTS scanner_sessions_destination_check();
    ALTER TABLE scanner_collection_sessions
      DROP CONSTRAINT IF EXISTS scanner_sessions_brand_needs_link,
      DROP CONSTRAINT IF EXISTS scanner_sessions_link_fk,
      DROP COLUMN IF EXISTS anypulse_brand_name,
      DROP COLUMN IF EXISTS anypulse_brand_id,
      DROP COLUMN IF EXISTS link_id;
    DROP TABLE IF EXISTS scanner_link_requests, scanner_links CASCADE;
    DROP FUNCTION IF EXISTS scanner_links_guard();
    """)
