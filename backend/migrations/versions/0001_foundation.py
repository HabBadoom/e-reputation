"""Foundation: tenancy, users, RBAC, auth tokens, invitations, audit log, brands. RLS everywhere.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-11-05

Assumes two cluster roles already exist (see infra/postgres/init-roles.sql):
  repintel_owner  -- owns schema objects, runs migrations, BYPASSRLS (never used by the API)
  repintel_app    -- used by the API: LOGIN, NOBYPASSRLS, DML only
"""

import os
import uuid

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

TENANT_TABLES = ["memberships", "invitations", "audit_logs", "brands", "brand_aliases"]

PERMISSIONS = {
    "tenant:read": "View organization details",
    "tenant:update": "Edit organization settings",
    "tenant:delete": "Delete the organization",
    "members:read": "View team members",
    "members:manage": "Invite, remove and change roles of members",
    "brands:read": "View brands",
    "brands:write": "Create and edit brands",
    "brands:delete": "Delete brands",
    "audit:read": "View the audit log",
    "billing:manage": "Manage subscription and billing",
}

ROLES = {
    "owner": ("Owner", 100, list(PERMISSIONS)),
    "admin": (
        "Admin",
        80,
        [p for p in PERMISSIONS if p not in ("tenant:delete", "billing:manage")],
    ),
    "analyst": ("Analyst", 50, ["tenant:read", "members:read", "brands:read", "brands:write"]),
    "viewer": ("Viewer", 10, ["tenant:read", "members:read", "brands:read"]),
}


def upgrade() -> None:
    x = op.execute

    # --- context helpers (fail closed: unset -> NULL -> no rows match) -------------------------
    x("""
    CREATE FUNCTION app_current_tenant() RETURNS uuid LANGUAGE sql STABLE AS
    $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;
    CREATE FUNCTION app_current_user() RETURNS uuid LANGUAGE sql STABLE AS
    $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;
    """)

    # --- global tables -----------------------------------------------------------------------
    x("""
    CREATE TABLE tenants (
      id uuid PRIMARY KEY,
      name varchar(200) NOT NULL CHECK (length(trim(name)) > 0),
      slug varchar(80) NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,78}[a-z0-9]$'),
      status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','deleted')),
      default_locale varchar(5) NOT NULL DEFAULT 'fr' CHECK (default_locale IN ('fr','ar','en')),
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE users (
      id uuid PRIMARY KEY,
      email varchar(320) NOT NULL CHECK (email = lower(email) AND position('@' in email) > 1),
      password_hash text NOT NULL,
      full_name varchar(200) NOT NULL,
      locale varchar(5) NOT NULL DEFAULT 'fr' CHECK (locale IN ('fr','ar','en')),
      status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
      last_login_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX users_email_uq ON users (email);

    CREATE TABLE roles (
      id uuid PRIMARY KEY,
      key varchar(50) NOT NULL UNIQUE,
      name varchar(100) NOT NULL,
      description text NOT NULL DEFAULT '',
      rank integer NOT NULL,
      is_system boolean NOT NULL DEFAULT true
    );
    CREATE TABLE permissions (
      id uuid PRIMARY KEY,
      key varchar(80) NOT NULL UNIQUE,
      description text NOT NULL DEFAULT ''
    );
    CREATE TABLE role_permissions (
      role_id uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
      permission_id uuid NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
      PRIMARY KEY (role_id, permission_id)
    );
    """)

    # --- tenant-scoped tables ----------------------------------------------------------------
    x("""
    CREATE TABLE memberships (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      role_id uuid NOT NULL REFERENCES roles(id),
      status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, user_id)
    );
    CREATE INDEX memberships_user_idx ON memberships (user_id);

    CREATE TABLE refresh_tokens (
      id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      token_hash varchar(64) NOT NULL UNIQUE,
      family_id uuid NOT NULL,
      expires_at timestamptz NOT NULL,
      revoked_at timestamptz,
      user_agent varchar(400),
      ip inet,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX refresh_tokens_family_idx ON refresh_tokens (family_id);
    CREATE INDEX refresh_tokens_user_idx ON refresh_tokens (user_id);

    CREATE TABLE invitations (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      email varchar(320) NOT NULL CHECK (email = lower(email)),
      role_id uuid NOT NULL REFERENCES roles(id),
      token_hash varchar(64) NOT NULL UNIQUE,
      invited_by uuid REFERENCES users(id) ON DELETE SET NULL,
      expires_at timestamptz NOT NULL,
      accepted_at timestamptz,
      revoked_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX invitations_tenant_idx ON invitations (tenant_id, created_at DESC);

    CREATE TABLE audit_logs (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
      action varchar(80) NOT NULL,
      target_type varchar(50),
      target_id uuid,
      metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
      ip inet,
      request_id varchar(64),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX audit_logs_tenant_time_idx ON audit_logs (tenant_id, created_at DESC);

    CREATE TABLE brands (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      name varchar(200) NOT NULL CHECK (length(trim(name)) > 0),
      description text NOT NULL DEFAULT '',
      website varchar(500),
      country char(2) NOT NULL DEFAULT 'DZ',
      primary_language varchar(10) NOT NULL DEFAULT 'fr',
      status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','archived')),
      created_by uuid REFERENCES users(id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    );
    CREATE UNIQUE INDEX brands_tenant_name_uq ON brands (tenant_id, lower(name));

    CREATE TABLE brand_aliases (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      brand_id uuid NOT NULL,
      alias varchar(200) NOT NULL CHECK (length(trim(alias)) > 0),
      script varchar(20) NOT NULL CHECK (script IN ('latin','arabic','arabizi','tifinagh','other')),
      kind varchar(20) NOT NULL DEFAULT 'alias' CHECK (kind IN ('alias','misspelling','transliteration','product','abbreviation')),
      created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX brand_aliases_uq ON brand_aliases (brand_id, lower(alias));
    """)

    # --- Row-Level Security --------------------------------------------------------------------
    for t in TENANT_TABLES + ["tenants", "users", "refresh_tokens"]:
        x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY; ALTER TABLE {t} FORCE ROW LEVEL SECURITY;")

    # Plain tenant isolation for business tables.
    for t in ["invitations", "audit_logs", "brands", "brand_aliases"]:
        x(f"""
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant())
          WITH CHECK (tenant_id = app_current_tenant());
        """)

    # Memberships: tenant members see their tenant; a user also sees their own memberships
    # (needed for login and the tenant switcher). Writes only within the current tenant.
    x("""
    CREATE POLICY memberships_select ON memberships FOR SELECT
      USING (tenant_id = app_current_tenant() OR user_id = app_current_user());
    CREATE POLICY memberships_write ON memberships FOR INSERT
      WITH CHECK (tenant_id = app_current_tenant());
    CREATE POLICY memberships_update ON memberships FOR UPDATE
      USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
    CREATE POLICY memberships_delete ON memberships FOR DELETE
      USING (tenant_id = app_current_tenant());
    """)

    # Tenants: visible if current, or if the user is a member (tenant switcher).
    x("""
    CREATE POLICY tenants_select ON tenants FOR SELECT
      USING (id = app_current_tenant()
             OR id IN (SELECT tenant_id FROM memberships WHERE user_id = app_current_user()));
    CREATE POLICY tenants_insert ON tenants FOR INSERT WITH CHECK (id = app_current_tenant());
    CREATE POLICY tenants_update ON tenants FOR UPDATE
      USING (id = app_current_tenant()) WITH CHECK (id = app_current_tenant());
    CREATE POLICY tenants_delete ON tenants FOR DELETE USING (id = app_current_tenant());
    """)

    # Users: yourself, or members of the current tenant. Only yourself is writable.
    x("""
    CREATE POLICY users_select ON users FOR SELECT
      USING (id = app_current_user()
             OR id IN (SELECT user_id FROM memberships WHERE tenant_id = app_current_tenant()));
    CREATE POLICY users_insert ON users FOR INSERT WITH CHECK (id = app_current_user());
    CREATE POLICY users_update ON users FOR UPDATE
      USING (id = app_current_user()) WITH CHECK (id = app_current_user());
    """)

    x("""
    CREATE POLICY refresh_tokens_own ON refresh_tokens
      USING (user_id = app_current_user()) WITH CHECK (user_id = app_current_user());
    """)

    # --- narrow SECURITY DEFINER lookups for pre-context auth steps ---------------------------
    # Each returns only what the caller needs. search_path pinned against hijacking.
    x("""
    CREATE FUNCTION auth_find_user_by_email(p_email text)
      RETURNS TABLE (id uuid, password_hash text, status varchar)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT u.id, u.password_hash, u.status FROM users u WHERE u.email = lower(p_email) $$;

    CREATE FUNCTION auth_find_refresh_token(p_hash text)
      RETURNS TABLE (id uuid, user_id uuid, tenant_id uuid, family_id uuid,
                     expires_at timestamptz, revoked_at timestamptz)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT r.id, r.user_id, r.tenant_id, r.family_id, r.expires_at, r.revoked_at
         FROM refresh_tokens r WHERE r.token_hash = p_hash $$;

    CREATE FUNCTION auth_find_invitation(p_hash text)
      RETURNS TABLE (id uuid, tenant_id uuid, email varchar, role_id uuid,
                     expires_at timestamptz, accepted_at timestamptz, revoked_at timestamptz)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT i.id, i.tenant_id, i.email, i.role_id, i.expires_at, i.accepted_at, i.revoked_at
         FROM invitations i WHERE i.token_hash = p_hash $$;

    REVOKE ALL ON FUNCTION auth_find_user_by_email(text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION auth_find_refresh_token(text) FROM PUBLIC;
    REVOKE ALL ON FUNCTION auth_find_invitation(text) FROM PUBLIC;
    """)

    # --- grants to the restricted app role -----------------------------------------------------
    x(f"""
    GRANT USAGE ON SCHEMA public TO {APP_ROLE};
    GRANT SELECT ON roles, permissions, role_permissions TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, users, memberships, refresh_tokens,
      invitations, brands, brand_aliases TO {APP_ROLE};
    GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE};   -- append-only for the app
    GRANT EXECUTE ON FUNCTION app_current_tenant(), app_current_user(),
      auth_find_user_by_email(text), auth_find_refresh_token(text),
      auth_find_invitation(text) TO {APP_ROLE};
    """)

    # --- RBAC seed --------------------------------------------------------------------------
    perm_ids = {k: uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:perm:{k}") for k in PERMISSIONS}
    for k, desc in PERMISSIONS.items():
        x(f"INSERT INTO permissions (id, key, description) VALUES ('{perm_ids[k]}', '{k}', '{desc}')")
    for key, (name, rank, perms) in ROLES.items():
        rid = uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:role:{key}")
        x(f"INSERT INTO roles (id, key, name, rank) VALUES ('{rid}', '{key}', '{name}', {rank})")
        for p in perms:
            x(f"INSERT INTO role_permissions VALUES ('{rid}', '{perm_ids[p]}')")


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS auth_find_invitation(text);
    DROP FUNCTION IF EXISTS auth_find_refresh_token(text);
    DROP FUNCTION IF EXISTS auth_find_user_by_email(text);
    DROP TABLE IF EXISTS brand_aliases, brands, audit_logs, invitations, refresh_tokens,
      memberships, role_permissions, permissions, roles, users, tenants CASCADE;
    DROP FUNCTION IF EXISTS app_current_user();
    DROP FUNCTION IF EXISTS app_current_tenant();
    """)
