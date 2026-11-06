"""Milestone 4: Algerian language intelligence.

Revision ID: 0005_nlp
Revises: 0004_collection
Create Date: 2026-11-06

- mention_analysis: per-mention AI/linguistic analysis (tenant-scoped), with corrections.
- language_annotations: the platform corpus (ALGERIAN_NLP_BENCHMARK). Only platform admins can
  read/verify it; tenants contribute only if they opted in (tenants.nlp_corpus_opt_in), through
  SECURITY DEFINER functions. Opting out deletes their contributions.
- language_evaluation_runs: benchmark results (no tenant data).
- dialect_expressions: seed expression dictionary (unvalidated until reviewed).
- users.is_platform_admin: set by an operator in the database; the app role cannot change it.
- connector_policies('openai'): sending text to an AI provider is gated like a connector.
"""

import os
import sys
import uuid
from pathlib import Path

from alembic import op

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.nlp.lexicon import SEED  # noqa: E402

revision = "0005_nlp"
down_revision = "0004_collection"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

NEW_PERMISSIONS = {
    "nlp:correct": ("Correct the analysis of a mention", ["owner", "admin", "analyst"]),
}


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:{kind}:{key}")


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def upgrade() -> None:
    x = op.execute

    x(f"""
    ALTER TABLE users ADD COLUMN is_platform_admin boolean NOT NULL DEFAULT false;
    ALTER TABLE tenants ADD COLUMN nlp_corpus_opt_in boolean NOT NULL DEFAULT false;

    -- The API role can update a user's own row (name, locale…) but never promote itself.
    CREATE FUNCTION protect_platform_admin() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.is_platform_admin IS DISTINCT FROM OLD.is_platform_admin
         AND current_user = '{APP_ROLE}' THEN
        RAISE EXCEPTION 'is_platform_admin can only be changed by an operator'
          USING ERRCODE = '42501';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER users_protect_platform_admin BEFORE UPDATE ON users
      FOR EACH ROW EXECUTE FUNCTION protect_platform_admin();
    CREATE FUNCTION protect_platform_admin_insert() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.is_platform_admin AND current_user = '{APP_ROLE}' THEN
        RAISE EXCEPTION 'is_platform_admin can only be set by an operator' USING ERRCODE = '42501';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER users_protect_platform_admin_ins BEFORE INSERT ON users
      FOR EACH ROW EXECUTE FUNCTION protect_platform_admin_insert();

    CREATE FUNCTION app_is_platform_admin() RETURNS boolean
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT coalesce((SELECT is_platform_admin FROM users WHERE id = app_current_user()), false)
    $$;

    ALTER TABLE mentions ADD CONSTRAINT mentions_tenant_id_uq UNIQUE (tenant_id, id);

    CREATE TABLE mention_analysis (
      mention_id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      status varchar(30) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','done','failed','not_configured','blocked_by_policy')),
      -- deterministic linguistic layer
      script varchar(20),
      heuristic_language varchar(10),
      code_switching boolean,
      normalized_text text,
      -- AI ensemble (final labels)
      language varchar(10),
      dialect varchar(30),
      sentiment varchar(20) CHECK (sentiment IN ('positive','negative','neutral','mixed','UNCERTAIN')),
      sarcasm varchar(10) CHECK (sarcasm IN ('yes','no','UNCERTAIN')),
      about_target varchar(10) CHECK (about_target IN ('yes','no','UNCERTAIN')),
      intents text[] NOT NULL DEFAULT '{{}}',
      emotions text[] NOT NULL DEFAULT '{{}}',
      topics text[] NOT NULL DEFAULT '{{}}',
      translation_fr text,
      agreement numeric(4,3),           -- share of annotators agreeing on the sentiment
      arbitration_used boolean NOT NULL DEFAULT false,
      annotators jsonb NOT NULL DEFAULT '[]'::jsonb,   -- every annotator's raw output
      models jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      prompt_version varchar(30),
      analysis_version varchar(30),
      analysed_at timestamptz,
      error text,
      -- human corrections (take precedence; never overwrite the AI output)
      corrected_sentiment varchar(20),
      corrected_sarcasm varchar(10),
      corrected_about_target varchar(10),
      corrected_intents text[],
      corrected_by uuid REFERENCES users(id) ON DELETE SET NULL,
      corrected_at timestamptz,
      FOREIGN KEY (tenant_id, mention_id) REFERENCES mentions (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX mention_analysis_tenant_idx ON mention_analysis (tenant_id, sentiment);

    CREATE TABLE language_annotations (
      id uuid PRIMARY KEY,
      origin_tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
      origin_mention_id uuid UNIQUE,
      text text NOT NULL,
      normalized_text text,
      source_url text,
      script varchar(20),
      auto_labels jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      auto_status varchar(20) NOT NULL DEFAULT 'auto_agreed'
        CHECK (auto_status IN ('auto_agreed','auto_arbitrated','uncertain','manual')),
      labels jsonb,                         -- human-verified labels (gold)
      status varchar(20) NOT NULL DEFAULT 'auto'
        CHECK (status IN ('auto','human_verified','human_corrected','tenant_corrected','rejected')),
      split varchar(20) NOT NULL DEFAULT 'unassigned'
        CHECK (split IN ('unassigned','gold_test','gold_dev','train')),
      verified_by uuid REFERENCES users(id) ON DELETE SET NULL,
      verified_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX language_annotations_split_idx ON language_annotations (split, status);

    CREATE TABLE language_evaluation_runs (
      id uuid PRIMARY KEY,
      status varchar(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','failed')),
      split varchar(20) NOT NULL DEFAULT 'gold_test',
      n_items integer NOT NULL DEFAULT 0,
      prompt_version varchar(30),
      analysis_version varchar(30),
      models jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      metrics jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      error text,
      requested_by uuid REFERENCES users(id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz
    );

    CREATE TABLE dialect_expressions (
      id uuid PRIMARY KEY,
      canonical varchar(100) NOT NULL UNIQUE,
      arabic varchar(100),
      variants text[] NOT NULL DEFAULT '{{}}',
      meaning_fr text NOT NULL DEFAULT '',
      polarity varchar(10) NOT NULL DEFAULT 'none' CHECK (polarity IN ('positive','negative','none')),
      status varchar(20) NOT NULL DEFAULT 'seed_unvalidated'
        CHECK (status IN ('seed_unvalidated','validated','rejected')),
      created_at timestamptz NOT NULL DEFAULT now()
    );
    """)

    x("""
    ALTER TABLE mention_analysis ENABLE ROW LEVEL SECURITY;
    ALTER TABLE mention_analysis FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_isolation ON mention_analysis
      USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

    -- Corpus: platform admins only. Tenants never read it.
    ALTER TABLE language_annotations ENABLE ROW LEVEL SECURITY;
    ALTER TABLE language_annotations FORCE ROW LEVEL SECURITY;
    CREATE POLICY platform_admin_only ON language_annotations
      USING (app_is_platform_admin()) WITH CHECK (app_is_platform_admin());
    """)

    # Tenant contributions go through narrow functions that enforce the opt-in.
    x("""
    CREATE FUNCTION corpus_add(p_mention_id uuid, p_text text, p_normalized text, p_url text,
                               p_script text, p_auto_labels jsonb, p_auto_status text)
      RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = app_current_tenant() AND nlp_corpus_opt_in)
         OR NOT EXISTS (SELECT 1 FROM mentions WHERE id = p_mention_id
                        AND tenant_id = app_current_tenant()) THEN
        RETURN false;
      END IF;
      INSERT INTO language_annotations (id, origin_tenant_id, origin_mention_id, text,
                                        normalized_text, source_url, script, auto_labels, auto_status)
      VALUES (gen_random_uuid(), app_current_tenant(), p_mention_id, left(p_text, 4000),
              p_normalized, p_url, p_script, p_auto_labels, p_auto_status)
      ON CONFLICT (origin_mention_id) DO UPDATE
        SET auto_labels = EXCLUDED.auto_labels, auto_status = EXCLUDED.auto_status,
            normalized_text = EXCLUDED.normalized_text
        WHERE language_annotations.status = 'auto';
      RETURN true;
    END $$;

    CREATE FUNCTION corpus_add_correction(p_mention_id uuid, p_labels jsonb)
      RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = app_current_tenant() AND nlp_corpus_opt_in) THEN
        RETURN false;
      END IF;
      -- Tenant corrections feed training data, never the gold test set.
      UPDATE language_annotations
        SET labels = p_labels, status = 'tenant_corrected',
            split = CASE WHEN split = 'unassigned' THEN 'train' ELSE split END
        WHERE origin_mention_id = p_mention_id AND origin_tenant_id = app_current_tenant()
          AND status IN ('auto', 'tenant_corrected') AND split <> 'gold_test';
      RETURN FOUND;
    END $$;

    CREATE FUNCTION corpus_purge_tenant() RETURNS integer
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      WITH d AS (DELETE FROM language_annotations WHERE origin_tenant_id = app_current_tenant()
                 RETURNING 1)
      SELECT count(*)::int FROM d
    $$;
    """)
    for f in ("app_is_platform_admin()",
              "corpus_add(uuid, text, text, text, text, jsonb, text)",
              "corpus_add_correction(uuid, jsonb)", "corpus_purge_tenant()"):
        x(f"REVOKE ALL ON FUNCTION {f} FROM PUBLIC; GRANT EXECUTE ON FUNCTION {f} TO {APP_ROLE};")

    x(f"""
    GRANT SELECT, INSERT, UPDATE, DELETE ON mention_analysis TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE ON language_annotations TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE ON language_evaluation_runs TO {APP_ROLE};
    GRANT SELECT ON dialect_expressions TO {APP_ROLE};
    """)

    for canonical, arabic, variants, meaning, polarity in SEED:
        arr = "ARRAY[" + ", ".join(_q(v) for v in dict.fromkeys(variants)) + "]::text[]"
        x(f"INSERT INTO dialect_expressions (id, canonical, arabic, variants, meaning_fr, polarity) "
          f"VALUES ('{_id('expr', canonical)}', {_q(canonical)}, {_q(arabic)}, {arr}, "
          f"{_q(meaning)}, {_q(polarity)})")

    x("""
    INSERT INTO connector_policies (key, name, access_method, legal_basis, data_categories,
                                    restrictions, retention_days)
    VALUES ('openai', 'OpenAI API (language analysis)', 'llm_processing',
      'Collected excerpts are sent to OpenAI for analysis (processing outside Algeria, under '
      'OpenAI API data terms). Requires a data-processing agreement and review under Law 18-07 '
      'before production.',
      ARRAY['mention excerpt', 'title', 'brand name'],
      'Only the excerpt and title are sent; no user or customer personal data.', 30);
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
    DELETE FROM connector_policies WHERE key = 'openai';
    DROP FUNCTION IF EXISTS corpus_purge_tenant(), corpus_add_correction(uuid, jsonb),
      corpus_add(uuid, text, text, text, text, jsonb, text);
    DROP TABLE IF EXISTS dialect_expressions, language_evaluation_runs, language_annotations,
      mention_analysis;
    ALTER TABLE mentions DROP CONSTRAINT IF EXISTS mentions_tenant_id_uq;
    DROP FUNCTION IF EXISTS app_is_platform_admin();
    DROP TRIGGER IF EXISTS users_protect_platform_admin_ins ON users;
    DROP TRIGGER IF EXISTS users_protect_platform_admin ON users;
    DROP FUNCTION IF EXISTS protect_platform_admin_insert(), protect_platform_admin();
    ALTER TABLE tenants DROP COLUMN IF EXISTS nlp_corpus_opt_in;
    ALTER TABLE users DROP COLUMN IF EXISTS is_platform_admin;
    """)
