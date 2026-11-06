"""Milestone 5: reputation intelligence — alerts, rules, webhooks, crises, trends, reports.

Revision ID: 0006_intelligence
Revises: 0005_nlp
Create Date: 2026-11-06

Daily sentiment / reputation figures are computed on demand from mentions + analysis (one
source of truth); they are not duplicated into a metrics table at this scale.
"""

import os
import uuid

from alembic import op

revision = "0006_intelligence"
down_revision = "0005_nlp"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")
TABLES = ["webhooks", "alert_rules", "alerts", "crisis_events", "trend_events", "reports"]
SEVERITIES = "'INFO','LOW','MEDIUM','HIGH','CRITICAL'"

NEW_PERMISSIONS = {
    "alerts:read": ("View alerts, crises and trends", ["owner", "admin", "analyst", "viewer"]),
    "alerts:manage": ("Manage alert rules and webhooks; acknowledge alerts",
                      ["owner", "admin", "analyst"]),
    "reports:read": ("View and download reports", ["owner", "admin", "analyst", "viewer"]),
    "reports:create": ("Generate reports", ["owner", "admin", "analyst"]),
}


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:{kind}:{key}")


def upgrade() -> None:
    x = op.execute
    x(f"""
    CREATE TABLE webhooks (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      name varchar(100) NOT NULL,
      url varchar(1000) NOT NULL CHECK (url ~ '^https://'),
      secret_enc text NOT NULL,          -- HMAC signing secret, encrypted at rest
      enabled boolean NOT NULL DEFAULT true,
      last_status integer,
      last_error text,
      last_delivery_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id)
    );

    CREATE TABLE alert_rules (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      name varchar(200) NOT NULL,
      kind varchar(30) NOT NULL CHECK (kind IN ('negative_mention','negative_spike','crisis',
        'emerging_topic','competitor_spike','connector_failure')),
      brand_id uuid,                     -- NULL = all brands
      params jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      min_severity varchar(10) NOT NULL DEFAULT 'LOW' CHECK (min_severity IN ({SEVERITIES})),
      channels text[] NOT NULL DEFAULT '{{in_app}}'
        CHECK (channels <@ ARRAY['in_app','email','webhook']::text[]),
      notify_roles text[] NOT NULL DEFAULT '{{owner,admin}}',
      webhook_id uuid,
      enabled boolean NOT NULL DEFAULT true,
      last_evaluated_at timestamptz,
      created_by uuid REFERENCES users(id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, webhook_id) REFERENCES webhooks (tenant_id, id)
        ON DELETE SET NULL (webhook_id)
    );

    CREATE TABLE crisis_events (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      brand_id uuid NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','resolved')),
      severity varchar(10) NOT NULL CHECK (severity IN ({SEVERITIES})),
      started_at timestamptz NOT NULL DEFAULT now(),
      last_detected_at timestamptz NOT NULL DEFAULT now(),
      resolved_at timestamptz,
      peak_negative integer NOT NULL DEFAULT 0,
      metrics jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX crisis_one_active_per_brand ON crisis_events (tenant_id, brand_id)
      WHERE status = 'active';

    CREATE TABLE alerts (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      rule_id uuid,
      kind varchar(30) NOT NULL,
      severity varchar(10) NOT NULL CHECK (severity IN ({SEVERITIES})),
      title varchar(300) NOT NULL,
      body text NOT NULL DEFAULT '',
      brand_id uuid,
      dedup_key varchar(300) NOT NULL,
      data jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      status varchar(20) NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','acknowledged','resolved')),
      acknowledged_by uuid REFERENCES users(id) ON DELETE SET NULL,
      acknowledged_at timestamptz,
      resolved_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, dedup_key),
      FOREIGN KEY (tenant_id, rule_id) REFERENCES alert_rules (tenant_id, id)
        ON DELETE SET NULL (rule_id),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX alerts_tenant_time_idx ON alerts (tenant_id, created_at DESC);

    CREATE TABLE trend_events (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      brand_id uuid NOT NULL,
      topic varchar(120) NOT NULL,
      day date NOT NULL,
      count integer NOT NULL,
      expected numeric(10,3) NOT NULL,
      z numeric(10,3) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX trend_events_uq ON trend_events (tenant_id, brand_id, lower(topic), day);

    CREATE TABLE reports (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      kind varchar(20) NOT NULL CHECK (kind IN ('daily','weekly','monthly','crisis',
                                                'competitor','custom')),
      brand_id uuid,
      period_start timestamptz NOT NULL,
      period_end timestamptz NOT NULL CHECK (period_end > period_start),
      format varchar(10) NOT NULL CHECK (format IN ('pdf','xlsx','csv','html')),
      locale varchar(5) NOT NULL DEFAULT 'fr' CHECK (locale IN ('fr','ar','en')),
      status varchar(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','ready','failed')),
      content bytea,
      content_type varchar(100),
      filename varchar(200),
      size_bytes integer,
      error text,
      created_by uuid REFERENCES users(id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz,
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX reports_tenant_time_idx ON reports (tenant_id, created_at DESC);
    """)

    for t in TABLES:
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
        """)

    # Periodic evaluation of alert rules: one pending job per tenant at most.
    x(f"""
    CREATE FUNCTION schedule_alert_evaluations(p_every_minutes int) RETURNS int
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      WITH due AS (
        SELECT DISTINCT r.tenant_id FROM alert_rules r
        WHERE r.enabled AND NOT EXISTS (
          SELECT 1 FROM jobs j WHERE j.tenant_id = r.tenant_id AND j.kind = 'alerts.evaluate'
            AND (j.status IN ('pending','running')
                 OR j.created_at > now() - make_interval(mins => p_every_minutes)))),
      ins AS (
        INSERT INTO jobs (id, tenant_id, kind, payload, max_attempts)
        SELECT gen_random_uuid(), tenant_id, 'alerts.evaluate', '{{}}'::jsonb, 2 FROM due
        RETURNING 1)
      SELECT count(*)::int FROM ins
    $$;
    REVOKE ALL ON FUNCTION schedule_alert_evaluations(int) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION schedule_alert_evaluations(int) TO {APP_ROLE};

    -- After new analyses: evaluate the current tenant soon, unless an evaluation is already
    -- waiting. Uses the caller's tenant context only (no tenant argument to spoof).
    CREATE FUNCTION request_alert_evaluation() RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      DECLARE t uuid := app_current_tenant();
      BEGIN
        IF t IS NULL THEN RETURN false; END IF;
        IF EXISTS (SELECT 1 FROM jobs WHERE tenant_id = t AND kind = 'alerts.evaluate'
                   AND status = 'pending') THEN RETURN false; END IF;
        INSERT INTO jobs (id, tenant_id, kind, payload, max_attempts)
          VALUES (gen_random_uuid(), t, 'alerts.evaluate', '{{}}'::jsonb, 2);
        RETURN true;
      END $$;
    REVOKE ALL ON FUNCTION request_alert_evaluation() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION request_alert_evaluation() TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE, DELETE ON {", ".join(TABLES)} TO {APP_ROLE};
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
    DROP FUNCTION IF EXISTS schedule_alert_evaluations(int);
    DROP FUNCTION IF EXISTS request_alert_evaluation();
    DROP TABLE IF EXISTS reports, trend_events, alerts, crisis_events, alert_rules, webhooks;
    """)
