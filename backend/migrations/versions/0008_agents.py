"""Milestone 7: multi-agent system — tasks, agent runs, actions, human approvals.

Revision ID: 0008_agents
Revises: 0007_browser_fleet
Create Date: 2026-11-09

Agents never touch the database directly: they call service functions through an allowlist.
Actions they propose go through the compliance policy; sensitive ones wait for a human
approval, and a trigger refuses to mark such an action executed without one.
"""

import os
import uuid

from alembic import op

revision = "0008_agents"
down_revision = "0007_browser_fleet"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")
TABLES = ["ai_tasks", "agent_runs", "ai_actions", "approval_requests"]
TASK_KINDS = ("brand_health", "crisis_response", "competitor_review", "source_discovery",
              "respond_to_mention", "report", "ask")
ACTION_KINDS = ("run_monitoring", "check_source", "browser_check_source", "create_report",
                "enable_source", "create_default_alert_rules", "use_response")
NEW_PERMISSIONS = {
    "ai:read": ("View AI tasks, agent runs and proposed actions",
                ["owner", "admin", "analyst", "viewer"]),
    "ai:run": ("Start AI tasks", ["owner", "admin", "analyst"]),
    "ai:approve": ("Approve or reject sensitive actions proposed by agents", ["owner", "admin"]),
}
ENTITLEMENT = "max_ai_tasks_per_day"
PLAN_LIMITS = {"trial": 20, "unlimited": None}


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:{kind}:{key}")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    x = op.execute
    x(f"""
    ALTER TABLE brands
      ADD COLUMN response_tone varchar(20) NOT NULL DEFAULT 'neutral'
        CHECK (response_tone IN ('neutral','formal','friendly')),
      ADD COLUMN response_guidelines text NOT NULL DEFAULT ''
        CHECK (char_length(response_guidelines) <= 2000);

    CREATE TABLE ai_tasks (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      kind varchar(30) NOT NULL CHECK (kind IN ({_in(TASK_KINDS)})),
      resolved_kind varchar(30) CHECK (resolved_kind IN ({_in(TASK_KINDS)})),
      goal text NOT NULL DEFAULT '' CHECK (char_length(goal) <= 1000),
      brand_id uuid,
      params jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      locale varchar(5) NOT NULL DEFAULT 'fr' CHECK (locale IN ('fr','ar','en')),
      status varchar(20) NOT NULL DEFAULT 'queued' CHECK (status IN
        ('queued','running','waiting_approval','completed','failed','cancelled')),
      plan jsonb NOT NULL DEFAULT '[]'::jsonb,
      result jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      error varchar(300),
      orchestrator_version varchar(20) NOT NULL DEFAULT '',
      created_by uuid REFERENCES users(id) ON DELETE SET NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      started_at timestamptz,
      finished_at timestamptz,
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, brand_id) REFERENCES brands (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX ai_tasks_tenant_idx ON ai_tasks (tenant_id, created_at DESC);

    CREATE TABLE agent_runs (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      task_id uuid NOT NULL,
      position smallint NOT NULL,
      agent varchar(30) NOT NULL,
      agent_version varchar(20) NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','completed','failed','timeout','skipped')),
      input jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      output jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      tools_used text[] NOT NULL DEFAULT '{{}}',
      llm_calls integer NOT NULL DEFAULT 0,
      llm_model varchar(100),
      prompt_version varchar(20),
      error varchar(300),
      started_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz,
      duration_ms integer,
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, task_id) REFERENCES ai_tasks (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX agent_runs_task_idx ON agent_runs (task_id, position);

    CREATE TABLE ai_actions (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      task_id uuid NOT NULL,
      agent_run_id uuid,
      kind varchar(40) NOT NULL CHECK (kind IN ({_in(ACTION_KINDS)})),
      params jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      rationale jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      risk varchar(10) NOT NULL CHECK (risk IN ('low','medium','high')),
      requires_approval boolean NOT NULL,
      status varchar(20) NOT NULL CHECK (status IN ('proposed','rejected_by_policy',
        'pending_approval','approved','rejected','executed','failed','expired')),
      compliance jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      result jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      error varchar(300),
      created_at timestamptz NOT NULL DEFAULT now(),
      executed_at timestamptz,
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, task_id) REFERENCES ai_tasks (tenant_id, id) ON DELETE CASCADE,
      FOREIGN KEY (tenant_id, agent_run_id) REFERENCES agent_runs (tenant_id, id)
        ON DELETE SET NULL (agent_run_id)
    );
    CREATE INDEX ai_actions_task_idx ON ai_actions (task_id);

    CREATE TABLE approval_requests (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      action_id uuid NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','expired')),
      requested_by uuid REFERENCES users(id) ON DELETE SET NULL,
      decided_by uuid REFERENCES users(id) ON DELETE SET NULL,
      decided_at timestamptz,
      comment varchar(500) NOT NULL DEFAULT '',
      created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      UNIQUE (tenant_id, action_id),
      CHECK (status IN ('pending','expired') OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
      FOREIGN KEY (tenant_id, action_id) REFERENCES ai_actions (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX approval_requests_pending_idx ON approval_requests (tenant_id, status);
    """)
    for t in TABLES:
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
        """)
    x(f"""
    -- Defence in depth: whatever the code does, an action that needs approval cannot be
    -- executed without a human decision recorded by someone else than the agent machinery.
    CREATE FUNCTION ai_action_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
      BEGIN
        IF NEW.tenant_id <> OLD.tenant_id OR NEW.kind <> OLD.kind
           OR NEW.requires_approval <> OLD.requires_approval THEN
          RAISE EXCEPTION 'ai action identity is immutable' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.requires_approval AND NEW.status IN ('approved','executed')
           AND NEW.status IS DISTINCT FROM OLD.status
           AND NOT EXISTS (SELECT 1 FROM approval_requests a WHERE a.action_id = NEW.id
                           AND a.tenant_id = NEW.tenant_id AND a.status = 'approved'
                           AND a.decided_by IS NOT NULL) THEN
          RAISE EXCEPTION 'action % requires an approved approval request', NEW.id
            USING ERRCODE = 'check_violation';
        END IF;
        IF OLD.status IN ('executed','rejected','rejected_by_policy','expired')
           AND NEW.status IS DISTINCT FROM OLD.status THEN
          RAISE EXCEPTION 'ai action already final' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
      END $$;
    CREATE TRIGGER ai_action_guard BEFORE UPDATE ON ai_actions
      FOR EACH ROW EXECUTE FUNCTION ai_action_guard();

    -- An approval is decided once, by a member allowed to approve.
    CREATE FUNCTION approval_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
      BEGIN
        IF OLD.status <> 'pending' AND NEW.status IS DISTINCT FROM OLD.status THEN
          RAISE EXCEPTION 'approval already decided' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status IN ('approved','rejected') AND NEW.status IS DISTINCT FROM OLD.status
           AND NOT EXISTS (
             SELECT 1 FROM memberships m JOIN role_permissions rp ON rp.role_id = m.role_id
             JOIN permissions p ON p.id = rp.permission_id
             WHERE m.tenant_id = NEW.tenant_id AND m.user_id = NEW.decided_by
               AND m.status = 'active' AND p.key = 'ai:approve') THEN
          RAISE EXCEPTION 'decider is not allowed to approve' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status = 'approved' AND NEW.expires_at < now() THEN
          RAISE EXCEPTION 'approval request expired' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
      END $$;
    CREATE TRIGGER approval_guard BEFORE UPDATE ON approval_requests
      FOR EACH ROW EXECUTE FUNCTION approval_guard();

    CREATE FUNCTION ai_tasks_today() RETURNS int
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT count(*)::int FROM ai_tasks
        WHERE tenant_id = app_current_tenant() AND created_at > now() - interval '24 hours'
    $$;
    REVOKE ALL ON FUNCTION ai_tasks_today() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION ai_tasks_today() TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE, DELETE ON {", ".join(TABLES)} TO {APP_ROLE};
    """)
    for key, (desc, roles) in NEW_PERMISSIONS.items():
        pid = _id("perm", key)
        x(f"INSERT INTO permissions (id, key, description) VALUES ('{pid}', '{key}', '{desc}')")
        for r in roles:
            x(f"INSERT INTO role_permissions VALUES ('{_id('role', r)}', '{pid}')")
    for plan, v in PLAN_LIMITS.items():
        x(f"INSERT INTO plan_entitlements VALUES ('{_id('plan', plan)}', '{ENTITLEMENT}', "
          f"{'NULL' if v is None else v})")


def downgrade() -> None:
    perm_ids = ", ".join(f"'{_id('perm', k)}'" for k in NEW_PERMISSIONS)
    op.execute(f"""
    DELETE FROM plan_entitlements WHERE key = '{ENTITLEMENT}';
    DELETE FROM role_permissions WHERE permission_id IN ({perm_ids});
    DELETE FROM permissions WHERE id IN ({perm_ids});
    DROP TABLE IF EXISTS approval_requests, ai_actions, agent_runs, ai_tasks;
    DROP FUNCTION IF EXISTS ai_action_guard(), approval_guard(), ai_tasks_today();
    ALTER TABLE brands DROP COLUMN IF EXISTS response_tone,
      DROP COLUMN IF EXISTS response_guidelines;
    """)
