"""Milestone 8: commercialization — plan tiers, prices, usage metering, billing accounts,
manual invoices, plan-change requests, admin console access, trial expiry, retention.

Revision ID: 0009_billing
Revises: 0008_agents
Create Date: 2026-11-09

No price or limit is decided here: the tiers are created *inactive* with the trial's limits as
a starting point and no price; the platform administrator sets limits and prices in the admin
console before activating them. Invoicing is manual (bank transfer, CCP, cheque…) behind a
provider setting, ready for a payment provider later.
"""

import importlib.util
import os
import uuid
from pathlib import Path

from alembic import op

revision = "0009_billing"
down_revision = "0008_agents"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

TIERS = [("starter", "Starter", 10), ("professional", "Professional", 20),
         ("business", "Business", 30), ("agency", "Agency", 40),
         ("enterprise", "Enterprise", 50)]
# New entitlements; trial values are placeholders (NULL = unlimited).
NEW_ENTITLEMENTS = {"max_mentions_per_month": 5000, "max_ai_calls_per_month": 3000,
                    "max_reports_per_month": 20, "max_webhooks": 1,
                    "max_concurrent_browser_missions": 1, "max_retention_days": 180}
METRICS = ("mentions_collected", "search_queries", "ai_calls", "ai_tokens", "browser_missions",
           "reports_generated", "agent_tasks", "emails_sent")
# billing:manage (owner) exists since migration 0001.
NEW_PERMISSIONS = {
    "billing:read": ("View plan, usage and invoices", ["owner", "admin"]),
}
TENANT_TABLES = ["billing_accounts", "invoices", "invoice_lines", "usage_events",
                 "plan_change_requests"]


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:{kind}:{key}")


def _claim_function(tenant_limit: str) -> str:
    path = Path(__file__).with_name("0007_browser_fleet.py")
    spec = importlib.util.spec_from_file_location("m0007", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.claim_function(tenant_limit)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    x = op.execute
    trial = _id("plan", "trial")
    x(f"""
    -- --------------------------------------------------------------------- plans
    ALTER TABLE plans
      ADD COLUMN description text NOT NULL DEFAULT '',
      ADD COLUMN is_public boolean NOT NULL DEFAULT false,
      ADD COLUMN sort_order integer NOT NULL DEFAULT 0,
      ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
    UPDATE plans SET sort_order = 0 WHERE key = 'trial';

    CREATE TABLE plan_prices (
      id uuid PRIMARY KEY,
      plan_id uuid NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
      currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
      billing_interval varchar(10) NOT NULL CHECK (billing_interval IN ('month','year')),
      amount_minor bigint NOT NULL CHECK (amount_minor >= 0),
      is_active boolean NOT NULL DEFAULT true,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX plan_prices_active_uq ON plan_prices (plan_id, currency, billing_interval)
      WHERE is_active;

    -- ------------------------------------------------------------- subscriptions
    ALTER TABLE subscriptions DROP CONSTRAINT subscriptions_status_check;
    ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_status_check
      CHECK (status IN ('trialing','active','past_due','canceled','expired'));
    ALTER TABLE subscriptions
      ADD COLUMN billing_interval varchar(10) CHECK (billing_interval IN ('month','year')),
      ADD COLUMN currency char(3),
      ADD COLUMN current_period_start timestamptz,
      ADD COLUMN canceled_at timestamptz,
      ADD COLUMN notes text NOT NULL DEFAULT '';

    -- -------------------------------------------------------------- billing
    CREATE TABLE billing_accounts (
      tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
      legal_name varchar(200) NOT NULL DEFAULT '',
      address text NOT NULL DEFAULT '',
      city varchar(100) NOT NULL DEFAULT '',
      postal_code varchar(20) NOT NULL DEFAULT '',
      country char(2) NOT NULL DEFAULT 'DZ' CHECK (country ~ '^[A-Z]{{2}}$'),
      email varchar(320) NOT NULL DEFAULT '',
      phone varchar(40) NOT NULL DEFAULT '',
      -- Algerian identifiers (registre du commerce, NIF, NIS, article d'imposition)
      rc varchar(40) NOT NULL DEFAULT '',
      nif varchar(40) NOT NULL DEFAULT '',
      nis varchar(40) NOT NULL DEFAULT '',
      article_imposition varchar(40) NOT NULL DEFAULT '',
      vat_number varchar(40) NOT NULL DEFAULT '',          -- non-Algerian customers
      currency char(3) NOT NULL DEFAULT 'DZD' CHECK (currency ~ '^[A-Z]{{3}}$'),
      updated_at timestamptz NOT NULL DEFAULT now()
    );

    CREATE TABLE invoices (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
      number varchar(40) UNIQUE,                            -- assigned when issued (gapless)
      status varchar(10) NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','issued','paid','void')),
      currency char(3) NOT NULL CHECK (currency ~ '^[A-Z]{{3}}$'),
      locale varchar(5) NOT NULL DEFAULT 'fr' CHECK (locale IN ('fr','ar','en')),
      period_start date,
      period_end date,
      issue_date date,
      due_date date,
      subtotal_minor bigint NOT NULL DEFAULT 0,
      tax_rate numeric(5,2) NOT NULL DEFAULT 0 CHECK (tax_rate BETWEEN 0 AND 100),
      tax_minor bigint NOT NULL DEFAULT 0,
      stamp_duty_minor bigint NOT NULL DEFAULT 0 CHECK (stamp_duty_minor >= 0),
      total_minor bigint NOT NULL DEFAULT 0,
      amount_in_words text NOT NULL DEFAULT '',
      seller jsonb NOT NULL DEFAULT '{{}}'::jsonb,          -- snapshot at issue
      buyer jsonb NOT NULL DEFAULT '{{}}'::jsonb,           -- snapshot at issue
      notes text NOT NULL DEFAULT '',
      payment_method varchar(30) CHECK (payment_method IN
        ('bank_transfer','ccp','cheque','cash','card','other')),
      payment_reference varchar(200) NOT NULL DEFAULT '',
      paid_at timestamptz,
      void_reason varchar(300) NOT NULL DEFAULT '',
      voided_at timestamptz,
      created_by uuid REFERENCES users(id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      CHECK (status = 'draft' OR (number IS NOT NULL AND issue_date IS NOT NULL))
    );
    CREATE INDEX invoices_tenant_idx ON invoices (tenant_id, created_at DESC);

    CREATE TABLE invoice_lines (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      invoice_id uuid NOT NULL,
      position smallint NOT NULL,
      description varchar(500) NOT NULL CHECK (length(trim(description)) > 0),
      quantity numeric(12,2) NOT NULL CHECK (quantity > 0),
      unit_minor bigint NOT NULL CHECK (unit_minor >= 0),
      amount_minor bigint NOT NULL,
      FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) ON DELETE CASCADE
    );

    -- Gapless numbering per year, independent of tenants.
    CREATE TABLE invoice_counters (year integer PRIMARY KEY, last integer NOT NULL);

    CREATE TABLE usage_events (
      id bigserial PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      metric varchar(30) NOT NULL CHECK (metric IN ({_in(METRICS)})),
      quantity bigint NOT NULL CHECK (quantity >= 0),
      ref jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      occurred_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX usage_events_tenant_metric_idx ON usage_events (tenant_id, metric, occurred_at);

    CREATE TABLE plan_change_requests (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      plan_id uuid NOT NULL REFERENCES plans(id),
      billing_interval varchar(10) NOT NULL CHECK (billing_interval IN ('month','year')),
      message varchar(1000) NOT NULL DEFAULT '',
      status varchar(10) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','done','declined','canceled')),
      requested_by uuid REFERENCES users(id) ON DELETE SET NULL,
      handled_by uuid REFERENCES users(id) ON DELETE SET NULL,
      handled_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE UNIQUE INDEX plan_change_one_pending ON plan_change_requests (tenant_id)
      WHERE status = 'pending';

    ALTER TABLE connector_policies ADD COLUMN review_notes text NOT NULL DEFAULT '';
    """)

    for t in TENANT_TABLES:
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
        """)
    # Global configuration: everyone reads, only platform administrators write.
    for t in ("plans", "plan_entitlements", "plan_prices", "connector_policies"):
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY read_all ON {t} FOR SELECT USING (true);
        CREATE POLICY admin_insert ON {t} FOR INSERT WITH CHECK (app_is_platform_admin());
        CREATE POLICY admin_update ON {t} FOR UPDATE USING (app_is_platform_admin())
          WITH CHECK (app_is_platform_admin());
        CREATE POLICY admin_delete ON {t} FOR DELETE USING (app_is_platform_admin());
        """)

    x(f"""
    -- Subscriptions change only through a platform administrator (or the owner role in
    -- maintenance functions). Members of the tenant cannot upgrade themselves.
    CREATE FUNCTION subscription_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
      BEGIN
        IF current_user = '{APP_ROLE}' AND NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'subscriptions are changed by platform administrators'
            USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF NEW.tenant_id <> OLD.tenant_id THEN
          RAISE EXCEPTION 'subscription tenant is immutable' USING ERRCODE = 'check_violation';
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
      END $$;
    CREATE TRIGGER subscription_guard BEFORE UPDATE ON subscriptions
      FOR EACH ROW EXECUTE FUNCTION subscription_guard();

    -- Invoices: drafts are editable; once issued only payment / void fields move, and only
    -- a platform administrator can write invoices at all.
    CREATE FUNCTION invoice_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
      BEGIN
        IF current_user = '{APP_ROLE}' AND NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'invoices are written by platform administrators'
            USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF TG_OP = 'DELETE' THEN
          IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'issued invoices cannot be deleted (void them)'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN OLD;
        END IF;
        IF TG_OP = 'UPDATE' AND OLD.status <> 'draft' THEN
          IF (NEW.number, NEW.currency, NEW.issue_date, NEW.due_date, NEW.subtotal_minor,
              NEW.tax_rate, NEW.tax_minor, NEW.stamp_duty_minor, NEW.total_minor, NEW.seller,
              NEW.buyer, NEW.tenant_id, NEW.period_start, NEW.period_end)
             IS DISTINCT FROM
             (OLD.number, OLD.currency, OLD.issue_date, OLD.due_date, OLD.subtotal_minor,
              OLD.tax_rate, OLD.tax_minor, OLD.stamp_duty_minor, OLD.total_minor, OLD.seller,
              OLD.buyer, OLD.tenant_id, OLD.period_start, OLD.period_end) THEN
            RAISE EXCEPTION 'issued invoice content is immutable' USING ERRCODE = 'check_violation';
          END IF;
          IF NOT ((OLD.status, NEW.status) IN (('issued','issued'), ('issued','paid'),
                  ('issued','void'), ('paid','paid'), ('paid','void'), ('void','void'))) THEN
            RAISE EXCEPTION 'illegal invoice transition % -> %', OLD.status, NEW.status
              USING ERRCODE = 'check_violation';
          END IF;
          IF OLD.status = 'void' AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'void invoices are final' USING ERRCODE = 'check_violation';
          END IF;
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
      END $$;
    CREATE TRIGGER invoice_guard BEFORE INSERT OR UPDATE OR DELETE ON invoices
      FOR EACH ROW EXECUTE FUNCTION invoice_guard();

    CREATE FUNCTION invoice_line_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
      DECLARE st text;
      BEGIN
        SELECT status INTO st FROM invoices
          WHERE id = coalesce(NEW.invoice_id, OLD.invoice_id);
        IF st IS DISTINCT FROM 'draft' AND st IS NOT NULL THEN
          RAISE EXCEPTION 'lines of an issued invoice are immutable'
            USING ERRCODE = 'check_violation';
        END IF;
        IF current_user = '{APP_ROLE}' AND NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'invoices are written by platform administrators'
            USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN coalesce(NEW, OLD);
      END $$;
    CREATE TRIGGER invoice_line_guard BEFORE INSERT OR UPDATE OR DELETE ON invoice_lines
      FOR EACH ROW EXECUTE FUNCTION invoice_line_guard();

    CREATE FUNCTION next_invoice_number(p_prefix text) RETURNS text
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      DECLARE y int := extract(year FROM now() AT TIME ZONE 'Africa/Algiers')::int; n int;
      BEGIN
        IF NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'platform administrators only' USING ERRCODE = 'insufficient_privilege';
        END IF;
        INSERT INTO invoice_counters AS c (year, last) VALUES (y, 1)
          ON CONFLICT (year) DO UPDATE SET last = c.last + 1
          RETURNING last INTO n;
        RETURN format('%s-%s-%s', p_prefix, y, lpad(n::text, 5, '0'));
      END $$;

    -- Usage of the current tenant since the start of the month (Algiers time).
    CREATE FUNCTION usage_this_month(p_metric text) RETURNS bigint
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT coalesce(sum(quantity), 0)::bigint FROM usage_events
        WHERE tenant_id = app_current_tenant() AND metric = p_metric
          AND occurred_at >= date_trunc('month', now() AT TIME ZONE 'Africa/Algiers')
                             AT TIME ZONE 'Africa/Algiers'
    $$;

    -- --------------------------------------------------------------- admin
    CREATE FUNCTION admin_tenants() RETURNS TABLE (
        tenant_id uuid, name text, slug text, created_at timestamptz, plan_key text,
        plan_name text, status text, trial_ends_at timestamptz, members bigint, brands bigint,
        mentions_month bigint, ai_calls_month bigint, open_invoices bigint,
        pending_request boolean)
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      BEGIN
        IF NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'platform administrators only' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN QUERY
        SELECT t.id, t.name::text, t.slug::text, t.created_at, p.key::text, p.name::text,
               s.status::text, s.trial_ends_at,
               (SELECT count(*) FROM memberships m WHERE m.tenant_id = t.id AND m.status = 'active'),
               (SELECT count(*) FROM brands b WHERE b.tenant_id = t.id),
               (SELECT coalesce(sum(u.quantity), 0)::bigint FROM usage_events u
                 WHERE u.tenant_id = t.id AND u.metric = 'mentions_collected'
                   AND u.occurred_at >= date_trunc('month', now())),
               (SELECT coalesce(sum(u.quantity), 0)::bigint FROM usage_events u
                 WHERE u.tenant_id = t.id AND u.metric = 'ai_calls'
                   AND u.occurred_at >= date_trunc('month', now())),
               (SELECT count(*) FROM invoices i WHERE i.tenant_id = t.id AND i.status = 'issued'),
               EXISTS (SELECT 1 FROM plan_change_requests r WHERE r.tenant_id = t.id
                         AND r.status = 'pending')
        FROM tenants t
        LEFT JOIN subscriptions s ON s.tenant_id = t.id
        LEFT JOIN plans p ON p.id = s.plan_id
        ORDER BY t.created_at DESC;
      END $$;

    CREATE FUNCTION admin_platform_stats() RETURNS jsonb
      LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      BEGIN
        IF NOT app_is_platform_admin() THEN
          RAISE EXCEPTION 'platform administrators only' USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN jsonb_build_object(
          'tenants', (SELECT count(*) FROM tenants),
          'users', (SELECT count(*) FROM users),
          'subscriptions', (SELECT jsonb_object_agg(status, n) FROM
              (SELECT status, count(*) n FROM subscriptions GROUP BY status) x),
          'pending_requests', (SELECT count(*) FROM plan_change_requests WHERE status = 'pending'),
          'invoices', (SELECT jsonb_object_agg(status, n) FROM
              (SELECT status, count(*) n FROM invoices GROUP BY status) x),
          'outstanding', (SELECT jsonb_object_agg(currency, total) FROM
              (SELECT currency, sum(total_minor) total FROM invoices WHERE status = 'issued'
               GROUP BY currency) x),
          'paid_last_30d', (SELECT jsonb_object_agg(currency, total) FROM
              (SELECT currency, sum(total_minor) total FROM invoices WHERE status = 'paid'
                 AND paid_at > now() - interval '30 days' GROUP BY currency) x),
          'usage_month', (SELECT jsonb_object_agg(metric, total) FROM
              (SELECT metric, sum(quantity) total FROM usage_events
                WHERE occurred_at >= date_trunc('month', now()) GROUP BY metric) x));
      END $$;

    -- Aggregates only (no tenant data), for the token-protected /metrics endpoint.
    CREATE FUNCTION platform_metrics() RETURNS jsonb
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT jsonb_build_object(
        'tenants', (SELECT count(*) FROM tenants),
        'subscriptions', (SELECT coalesce(jsonb_object_agg(status, n), '{{}}') FROM
            (SELECT status, count(*) n FROM subscriptions GROUP BY status) x),
        'jobs', (SELECT coalesce(jsonb_object_agg(status, n), '{{}}') FROM
            (SELECT status, count(*) n FROM jobs GROUP BY status) x),
        'jobs_oldest_pending_seconds', (SELECT coalesce(extract(epoch FROM now() - min(run_after)), 0)
            FROM jobs WHERE status = 'pending' AND run_after <= now()),
        'browser_workers_online', (SELECT count(*) FROM browser_workers WHERE status = 'online'),
        'browser_capacity', (SELECT coalesce(sum(max_contexts), 0) FROM browser_workers
            WHERE status = 'online'),
        'browser_missions', (SELECT coalesce(jsonb_object_agg(status, n), '{{}}') FROM
            (SELECT status, count(*) n FROM browser_missions
              WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED','EXPIRED') GROUP BY status) x),
        'ai_tasks_running', (SELECT count(*) FROM ai_tasks WHERE status IN ('queued','running')),
        'approvals_pending', (SELECT count(*) FROM approval_requests WHERE status = 'pending'),
        'invoices_outstanding', (SELECT count(*) FROM invoices WHERE status = 'issued'),
        'invoices_overdue', (SELECT count(*) FROM invoices WHERE status = 'issued'
            AND due_date < current_date))
    $$;

    -- -------------------------------------------------------------- maintenance
    CREATE FUNCTION maintenance_tenants() RETURNS SETOF uuid
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT id FROM tenants ORDER BY created_at
    $$;

    -- Trials past their end date expire (collection stops; data stays readable).
    CREATE FUNCTION expire_trials() RETURNS SETOF uuid
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      UPDATE subscriptions SET status = 'expired'
        WHERE status = 'trialing' AND trial_ends_at IS NOT NULL AND trial_ends_at < now()
      RETURNING tenant_id
    $$;

    CREATE FUNCTION schedule_daily_maintenance() RETURNS int
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      WITH ins AS (
        INSERT INTO jobs (id, tenant_id, kind, payload, max_attempts)
        SELECT gen_random_uuid(), NULL, 'platform.daily', '{{}}'::jsonb, 3
        WHERE NOT EXISTS (SELECT 1 FROM jobs WHERE kind = 'platform.daily'
                            AND created_at > now() - interval '23 hours')
        RETURNING 1)
      SELECT count(*)::int FROM ins
    $$;

    -- Browser fleet: per-tenant concurrency now comes from the plan when it sets one.
    {_claim_function(
        "coalesce((SELECT pe.value FROM subscriptions s JOIN plan_entitlements pe "
        "ON pe.plan_id = s.plan_id AND pe.key = 'max_concurrent_browser_missions' "
        "WHERE s.tenant_id = r.tenant_id), p_tenant_concurrency)")}
    """)

    fns = ["next_invoice_number(text)", "usage_this_month(text)", "admin_tenants()",
           "platform_metrics()",
           "admin_platform_stats()", "maintenance_tenants()", "expire_trials()",
           "schedule_daily_maintenance()"]
    for f in fns:
        x(f"REVOKE ALL ON FUNCTION {f} FROM PUBLIC; GRANT EXECUTE ON FUNCTION {f} TO {APP_ROLE};")
    x(f"""
    GRANT SELECT, INSERT, UPDATE, DELETE ON plans, plan_entitlements, plan_prices TO {APP_ROLE};
    GRANT UPDATE ON connector_policies TO {APP_ROLE};
    GRANT UPDATE ON subscriptions TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE, DELETE ON billing_accounts, invoices, invoice_lines,
      plan_change_requests TO {APP_ROLE};
    GRANT SELECT, INSERT, DELETE ON usage_events TO {APP_ROLE};
    GRANT USAGE ON SEQUENCE usage_events_id_seq TO {APP_ROLE};
    """)

    for key, name, order in TIERS:
        pid = _id("plan", key)
        x(f"INSERT INTO plans (id, key, name, is_default, is_active, is_public, sort_order) "
          f"VALUES ('{pid}', '{key}', '{name}', false, false, true, {order})")
        # Starting point only: copy of the trial limits, to be set by the administrator.
        x(f"INSERT INTO plan_entitlements SELECT '{pid}', key, value FROM plan_entitlements "
          f"WHERE plan_id = '{trial}'")
    for k, v in NEW_ENTITLEMENTS.items():
        for key, _, _ in [("trial", "", 0), *TIERS]:
            x(f"INSERT INTO plan_entitlements VALUES ('{_id('plan', key)}', '{k}', {v})")
    for key, (desc, roles) in NEW_PERMISSIONS.items():
        pid = _id("perm", key)
        x(f"INSERT INTO permissions (id, key, description) VALUES ('{pid}', '{key}', '{desc}')")
        for r in roles:
            x(f"INSERT INTO role_permissions VALUES ('{_id('role', r)}', '{pid}')")
    # Organizations that already exist get an (empty) billing account.
    x("INSERT INTO billing_accounts (tenant_id, legal_name) SELECT id, name FROM tenants")


def downgrade() -> None:
    perm_ids = ", ".join(f"'{_id('perm', k)}'" for k in NEW_PERMISSIONS)
    tier_ids = ", ".join(f"'{_id('plan', k)}'" for k, _, _ in TIERS)
    ent_keys = ", ".join(f"'{k}'" for k in NEW_ENTITLEMENTS)
    op.execute(f"""
    DELETE FROM role_permissions WHERE permission_id IN ({perm_ids});
    DELETE FROM permissions WHERE id IN ({perm_ids});
    {_claim_function("p_tenant_concurrency")}
    DROP FUNCTION IF EXISTS next_invoice_number(text), usage_this_month(text), admin_tenants(),
      platform_metrics(),
      admin_platform_stats(), maintenance_tenants(), expire_trials(),
      schedule_daily_maintenance();
    DROP TABLE IF EXISTS plan_change_requests, usage_events, invoice_counters, invoice_lines,
      invoices, billing_accounts, plan_prices;
    DROP TRIGGER IF EXISTS subscription_guard ON subscriptions;
    DROP FUNCTION IF EXISTS subscription_guard(), invoice_guard(), invoice_line_guard();
    REVOKE UPDATE ON subscriptions, connector_policies FROM {APP_ROLE};
    REVOKE INSERT, UPDATE, DELETE ON plans, plan_entitlements FROM {APP_ROLE};
    """)
    for t in ("plans", "plan_entitlements", "connector_policies"):
        op.execute(f"""
        DROP POLICY IF EXISTS read_all ON {t}; DROP POLICY IF EXISTS admin_insert ON {t};
        DROP POLICY IF EXISTS admin_update ON {t}; DROP POLICY IF EXISTS admin_delete ON {t};
        ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY; ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;
        """)
    op.execute(f"""
    UPDATE subscriptions SET status = 'canceled' WHERE status = 'expired';
    ALTER TABLE subscriptions DROP CONSTRAINT subscriptions_status_check;
    ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_status_check
      CHECK (status IN ('trialing','active','past_due','canceled'));
    ALTER TABLE subscriptions DROP COLUMN billing_interval, DROP COLUMN currency,
      DROP COLUMN current_period_start, DROP COLUMN canceled_at, DROP COLUMN notes;
    UPDATE subscriptions SET plan_id = '{_id("plan", "trial")}' WHERE plan_id IN ({tier_ids});
    DELETE FROM plan_entitlements WHERE key IN ({ent_keys});
    DELETE FROM plans WHERE id IN ({tier_ids});
    ALTER TABLE plans DROP COLUMN description, DROP COLUMN is_public, DROP COLUMN sort_order,
      DROP COLUMN updated_at;
    ALTER TABLE connector_policies DROP COLUMN review_notes;
    """)
