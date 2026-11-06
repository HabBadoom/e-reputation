"""Auth hardening: email verification, password reset, TOTP MFA, recovery codes.

Revision ID: 0002_auth_hardening
Revises: 0001_foundation
Create Date: 2026-11-06

All new tables are user-scoped: a user can only see and modify their own rows (RLS on
app.user_id). MFA secrets live in their own table, not in `users`, so that listing team
members never loads another user's (encrypted) secret.
"""

import os

from alembic import op

revision = "0002_auth_hardening"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")


def upgrade() -> None:
    x = op.execute

    x("ALTER TABLE users ADD COLUMN email_verified_at timestamptz")

    x("""
    CREATE TABLE user_tokens (
      id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      purpose varchar(30) NOT NULL
        CHECK (purpose IN ('email_verify','password_reset','mfa_login')),
      token_hash varchar(64) NOT NULL UNIQUE,
      tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,  -- mfa_login: chosen tenant
      expires_at timestamptz NOT NULL,
      used_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX user_tokens_user_purpose_idx ON user_tokens (user_id, purpose);

    CREATE TABLE user_mfa (
      user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      secret_enc text,              -- active TOTP secret (encrypted), NULL until enabled
      pending_secret_enc text,      -- secret being set up, not yet confirmed
      last_step bigint NOT NULL DEFAULT 0,  -- last accepted TOTP time step (replay guard)
      enabled_at timestamptz,
      updated_at timestamptz NOT NULL DEFAULT now(),
      CHECK ((enabled_at IS NULL) = (secret_enc IS NULL))
    );

    CREATE TABLE mfa_recovery_codes (
      id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      code_hash varchar(64) NOT NULL,
      used_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (user_id, code_hash)
    );
    """)

    for t in ("user_tokens", "user_mfa", "mfa_recovery_codes"):
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY own_rows ON {t}
          USING (user_id = app_current_user()) WITH CHECK (user_id = app_current_user());
        """)

    # Pre-context lookup by token hash (email link / MFA challenge). Narrow output.
    x("""
    CREATE FUNCTION auth_find_user_token(p_hash text, p_purpose text)
      RETURNS TABLE (id uuid, user_id uuid, tenant_id uuid,
                     expires_at timestamptz, used_at timestamptz)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
    $$ SELECT t.id, t.user_id, t.tenant_id, t.expires_at, t.used_at
         FROM user_tokens t WHERE t.token_hash = p_hash AND t.purpose = p_purpose $$;
    REVOKE ALL ON FUNCTION auth_find_user_token(text, text) FROM PUBLIC;
    """)

    x(f"""
    GRANT SELECT, INSERT, UPDATE, DELETE ON user_tokens, user_mfa, mfa_recovery_codes
      TO {APP_ROLE};
    GRANT EXECUTE ON FUNCTION auth_find_user_token(text, text) TO {APP_ROLE};
    """)


def downgrade() -> None:
    op.execute("""
    DROP FUNCTION IF EXISTS auth_find_user_token(text, text);
    DROP TABLE IF EXISTS mfa_recovery_codes, user_mfa, user_tokens;
    ALTER TABLE users DROP COLUMN IF EXISTS email_verified_at;
    """)
