"""Milestone 2: product catalog, monitoring projects and runs, job queue, plans/entitlements.

Revision ID: 0003_monitoring
Revises: 0002_auth_hardening
Create Date: 2026-11-06

Job queue design: a transactional outbox in PostgreSQL. Jobs are enqueued in the same
transaction as the change that causes them (no email for a rolled-back signup) and claimed
with FOR UPDATE SKIP LOCKED. The `jobs` table has RLS forced and *no* policies or grants for
the app role: it is only reachable through the SECURITY DEFINER functions below.
"""

import os
import uuid

from alembic import op

revision = "0003_monitoring"
down_revision = "0002_auth_hardening"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

TENANT_TABLES = ["subscriptions", "products", "competitors", "brand_keywords",
                 "monitoring_projects", "monitoring_runs"]

NEW_PERMISSIONS = {
    "monitoring:read": ("View monitoring projects and runs", ["owner", "admin", "analyst", "viewer"]),
    "monitoring:write": ("Create, edit and run monitoring projects", ["owner", "admin", "analyst"]),
}

# Limits are configuration data, editable in the database (admin console in Milestone 8).
# NULL = unlimited. Values for "trial" are placeholders to be confirmed by the product owner.
PLANS = {
    "trial": ("Trial", True, {
        "max_brands": 2, "max_monitoring_projects": 2, "max_members": 3,
        "min_frequency_minutes": 360, "max_competitors_per_brand": 5,
        "max_keywords_per_brand": 50,
    }),
    "unlimited": ("Unlimited (internal)", False, {
        "max_brands": None, "max_monitoring_projects": None, "max_members": None,
        "min_frequency_minutes": 15, "max_competitors_per_brand": None,
        "max_keywords_per_brand": None,
    }),
}


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:{kind}:{key}")


def upgrade() -> None:
    x = op.execute

    # ------------------------------------------------------------ plans & subscriptions
    x("""
    CREATE TABLE plans (
      id uuid PRIMARY KEY,
      key varchar(50) NOT NULL UNIQUE,
      name varchar(100) NOT NULL,
      is_default boolean NOT NULL DEFAULT false,
      is_active boolean NOT NULL DEFAULT true,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX plans_single_default ON plans (is_default) WHERE is_default;
    CREATE TABLE plan_entitlements (
      plan_id uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
      key varchar(60) NOT NULL,
      value integer CHECK (value IS NULL OR value >= 0),
      PRIMARY KEY (plan_id, key)
    );
    CREATE TABLE subscriptions (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
      plan_id uuid NOT NULL REFERENCES plans(id),
      status varchar(20) NOT NULL DEFAULT 'trialing'
        CHECK (status IN ('trialing','active','past_due','canceled')),
      trial_ends_at timestamptz,
      current_period_end timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    """)
    for key, (name, default, ents) in PLANS.items():
        pid = _id("plan", key)
        x(f"INSERT INTO plans (id, key, name, is_default) VALUES ('{pid}', '{key}', '{name}', {default})")
        for k, v in ents.items():
            x(f"INSERT INTO plan_entitlements VALUES ('{pid}', '{k}', {'NULL' if v is None else v})")
    # Existing organizations get the default plan.
    x("""
    INSERT INTO subscriptions (id, tenant_id, plan_id, status, trial_ends_at)
    SELECT gen_random_uuid(), t.id, p.id, 'trialing', now() + interval '14 days'
    FROM tenants t CROSS JOIN plans p WHERE p.is_default;
    """)

    # ------------------------------------------------------------ catalog
    x("""
    CREATE TABLE products (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      brand_id uuid NOT NULL,
      name varchar(200) NOT NULL CHECK (length(trim(name)) > 0),
      category varchar(100) NOT NULL DEFAULT '',
      description text NOT NULL DEFAULT '',
      aliases text[] NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX products_brand_name_uq ON products (brand_id, lower(name));

    CREATE TABLE competitors (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      brand_id uuid NOT NULL,
      name varchar(200) NOT NULL CHECK (length(trim(name)) > 0),
      website varchar(500),
      aliases text[] NOT NULL DEFAULT '{}',
      notes text NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX competitors_brand_name_uq ON competitors (brand_id, lower(name));

    CREATE TABLE brand_keywords (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      brand_id uuid NOT NULL,
      expression varchar(200) NOT NULL CHECK (length(trim(expression)) > 0),
      kind varchar(10) NOT NULL DEFAULT 'include' CHECK (kind IN ('include','exclude')),
      language varchar(10),
      created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX brand_keywords_uq ON brand_keywords (brand_id, kind, lower(expression));
    """)

    # ------------------------------------------------------------ monitoring
    x("""
    CREATE TABLE monitoring_projects (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      brand_id uuid NOT NULL,
      name varchar(200) NOT NULL CHECK (length(trim(name)) > 0),
      status varchar(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused')),
      frequency_minutes integer NOT NULL CHECK (frequency_minutes BETWEEN 15 AND 10080),
      include_competitors boolean NOT NULL DEFAULT true,
      languages text[] NOT NULL DEFAULT '{fr,ar}',
      next_run_at timestamptz,
      last_run_at timestamptz,
      created_by uuid REFERENCES users(id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE UNIQUE INDEX monitoring_projects_name_uq ON monitoring_projects (tenant_id, lower(name));
    CREATE INDEX monitoring_projects_due_idx ON monitoring_projects (next_run_at)
      WHERE status = 'active';

    CREATE TABLE monitoring_runs (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      project_id uuid NOT NULL,
      trigger varchar(20) NOT NULL CHECK (trigger IN ('schedule','manual')),
      status varchar(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','skipped','failed')),
      queued_at timestamptz NOT NULL DEFAULT now(),
      started_at timestamptz,
      finished_at timestamptz,
      summary jsonb NOT NULL DEFAULT '{}'::jsonb,
      error text,
      FOREIGN KEY (tenant_id, project_id) REFERENCES monitoring_projects (tenant_id, id)
        ON DELETE CASCADE
    );
    CREATE INDEX monitoring_runs_project_idx ON monitoring_runs (project_id, queued_at DESC);
    """)

    for t in TENANT_TABLES:
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
        """)

    # ------------------------------------------------------------ job queue
    x("""
    CREATE TABLE jobs (
      id uuid PRIMARY KEY,
      tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
      kind varchar(50) NOT NULL,
      payload jsonb NOT NULL DEFAULT '{}'::jsonb,
      sensitive boolean NOT NULL DEFAULT false,   -- payload wiped once done
      status varchar(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','succeeded','dead')),
      attempts integer NOT NULL DEFAULT 0,
      max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
      run_after timestamptz NOT NULL DEFAULT now(),
      locked_by varchar(100),
      locked_at timestamptz,
      last_error text,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz
    );
    CREATE INDEX jobs_pending_idx ON jobs (run_after) WHERE status = 'pending';
    CREATE INDEX jobs_running_idx ON jobs (locked_at) WHERE status = 'running';
    ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE jobs FORCE ROW LEVEL SECURITY;   -- no policies: app role has no direct access
    """)

    x("""
    CREATE FUNCTION enqueue_job(p_kind text, p_payload jsonb, p_tenant uuid,
                                p_run_after timestamptz, p_max_attempts int, p_sensitive boolean)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
    DECLARE v_id uuid := gen_random_uuid();
    BEGIN
      -- A request may only enqueue work for its own tenant (or tenant-less work).
      IF p_tenant IS NOT NULL AND p_tenant IS DISTINCT FROM app_current_tenant() THEN
        RAISE EXCEPTION 'cannot enqueue a job for another tenant' USING ERRCODE = '42501';
      END IF;
      INSERT INTO jobs (id, tenant_id, kind, payload, run_after, max_attempts, sensitive)
      VALUES (v_id, p_tenant, p_kind, coalesce(p_payload, '{}'::jsonb),
              coalesce(p_run_after, now()), coalesce(p_max_attempts, 5),
              coalesce(p_sensitive, false));
      RETURN v_id;
    END $$;

    CREATE FUNCTION claim_jobs(p_worker text, p_limit int)
      RETURNS TABLE (id uuid, tenant_id uuid, kind varchar, payload jsonb,
                     attempts int, max_attempts int)
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      UPDATE jobs j SET status = 'running', locked_by = p_worker, locked_at = now(),
                        attempts = j.attempts + 1, updated_at = now()
      WHERE j.id IN (SELECT q.id FROM jobs q WHERE q.status = 'pending' AND q.run_after <= now()
                     ORDER BY q.run_after LIMIT p_limit FOR UPDATE SKIP LOCKED)
      RETURNING j.id, j.tenant_id, j.kind, j.payload, j.attempts, j.max_attempts
    $$;

    CREATE FUNCTION complete_job(p_id uuid) RETURNS void
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      UPDATE jobs SET status = 'succeeded', finished_at = now(), updated_at = now(),
                      locked_by = NULL, last_error = NULL,
                      payload = CASE WHEN sensitive THEN '{}'::jsonb ELSE payload END
      WHERE id = p_id AND status = 'running'
    $$;

    -- Retry later, or move to the dead-letter state once attempts are exhausted.
    CREATE FUNCTION fail_job(p_id uuid, p_error text, p_retry_in_seconds int) RETURNS text
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      UPDATE jobs SET
        status = CASE WHEN attempts >= max_attempts OR p_retry_in_seconds IS NULL
                      THEN 'dead' ELSE 'pending' END,
        run_after = now() + make_interval(secs => coalesce(p_retry_in_seconds, 0)),
        finished_at = CASE WHEN attempts >= max_attempts OR p_retry_in_seconds IS NULL
                           THEN now() END,
        last_error = left(p_error, 2000), locked_by = NULL, updated_at = now()
      WHERE id = p_id AND status = 'running'
      RETURNING status
    $$;

    -- Jobs whose worker died mid-run go back to the queue (at-least-once delivery).
    CREATE FUNCTION requeue_stale_jobs(p_older_than_seconds int) RETURNS int
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      WITH r AS (
        UPDATE jobs SET status = 'pending', locked_by = NULL, updated_at = now(),
                        last_error = 'requeued after worker timeout'
        WHERE status = 'running'
          AND locked_at < now() - make_interval(secs => p_older_than_seconds)
        RETURNING 1)
      SELECT count(*)::int FROM r
    $$;

    -- Scheduler tick: enqueue one run per due active project and push next_run_at forward.
    -- Idempotent under concurrency thanks to SKIP LOCKED.
    CREATE FUNCTION schedule_due_monitoring() RETURNS int
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      WITH due AS (
        SELECT p.id, p.tenant_id, p.frequency_minutes FROM monitoring_projects p
        WHERE p.status = 'active' AND p.next_run_at IS NOT NULL AND p.next_run_at <= now()
        FOR UPDATE SKIP LOCKED),
      upd AS (
        UPDATE monitoring_projects p
        SET next_run_at = now() + make_interval(mins => due.frequency_minutes)
        FROM due WHERE p.id = due.id
        RETURNING p.id, p.tenant_id),
      ins AS (
        INSERT INTO jobs (id, tenant_id, kind, payload, max_attempts)
        SELECT gen_random_uuid(), upd.tenant_id, 'monitoring.run',
               jsonb_build_object('project_id', upd.id, 'trigger', 'schedule'), 3
        FROM upd RETURNING 1)
      SELECT count(*)::int FROM ins
    $$;

    -- Read-only queue statistics for health checks / admin.
    CREATE FUNCTION job_stats()
      RETURNS TABLE (status varchar, count bigint)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT status, count(*) FROM jobs GROUP BY status
    $$;
    """)
    fns = ["enqueue_job(text, jsonb, uuid, timestamptz, int, boolean)",
           "claim_jobs(text, int)", "complete_job(uuid)", "fail_job(uuid, text, int)",
           "requeue_stale_jobs(int)", "schedule_due_monitoring()", "job_stats()"]
    for f in fns:
        x(f"REVOKE ALL ON FUNCTION {f} FROM PUBLIC; GRANT EXECUTE ON FUNCTION {f} TO {APP_ROLE};")

    # ------------------------------------------------------------ grants & permissions
    x(f"""
    GRANT SELECT ON plans, plan_entitlements TO {APP_ROLE};
    GRANT SELECT, INSERT ON subscriptions TO {APP_ROLE};  -- plan changes are admin-only
    GRANT SELECT, INSERT, UPDATE, DELETE ON products, competitors, brand_keywords,
      monitoring_projects, monitoring_runs TO {APP_ROLE};
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
    DROP FUNCTION IF EXISTS job_stats(), schedule_due_monitoring(), requeue_stale_jobs(int),
      fail_job(uuid, text, int), complete_job(uuid), claim_jobs(text, int),
      enqueue_job(text, jsonb, uuid, timestamptz, int, boolean);
    DROP TABLE IF EXISTS jobs, monitoring_runs, monitoring_projects, brand_keywords,
      competitors, products, subscriptions, plan_entitlements, plans;
    """)
