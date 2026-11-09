"""Milestone 6: browser fleet — workers, missions (state machine), sessions, challenge events.

Revision ID: 0007_browser_fleet
Revises: 0006_intelligence
Create Date: 2026-11-08

Anti-bot challenges (CAPTCHA, JavaScript challenges, login walls, rate limiting) are detected
automatically and answered autonomously WITHOUT solving or bypassing them: the mission stops,
the domain cools down, the source is marked restricted after repeated challenges and collection
continues through other legal channels. See docs/CAPTCHA_INTERVENTIONS.md.
"""

import os
import uuid

from alembic import op

revision = "0007_browser_fleet"
down_revision = "0006_intelligence"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("REPINTEL_DB_APP_ROLE", "repintel_app")

STATES = ("CREATED", "VALIDATING", "QUEUED", "ASSIGNED", "RUNNING", "PAUSED",
          "WAITING_FOR_HUMAN", "COMPLETED", "FAILED", "CANCELLED", "EXPIRED")
# Mirrored in app/browser/missions.py (TRANSITIONS); a test checks they are identical.
TRANSITIONS = {
    "CREATED": ("VALIDATING", "CANCELLED"),
    "VALIDATING": ("QUEUED", "FAILED", "CANCELLED"),
    "QUEUED": ("ASSIGNED", "CANCELLED", "EXPIRED"),
    "ASSIGNED": ("RUNNING", "QUEUED", "FAILED", "CANCELLED"),
    "RUNNING": ("COMPLETED", "FAILED", "PAUSED", "QUEUED", "CANCELLED"),
    "PAUSED": ("QUEUED", "WAITING_FOR_HUMAN", "CANCELLED", "EXPIRED"),
    "WAITING_FOR_HUMAN": ("QUEUED", "CANCELLED", "EXPIRED"),
}
CHALLENGE_KINDS = ("recaptcha", "hcaptcha", "turnstile", "cloudflare_challenge", "datadome",
                   "perimeterx", "akamai", "aws_waf", "generic_captcha", "login_wall",
                   "rate_limited", "access_denied")
CHALLENGE_STATES = ("DETECTED", "PAUSED", "FALLBACK", "SOURCE_RESTRICTED", "CANCELLED",
                    "EXPIRED")
VERIFICATION = ("verified_on_page", "snippet_only", "not_found_on_page", "page_unavailable",
                "blocked_by_robots", "feed_item", "pending_render", "blocked_by_challenge")
OLD_VERIFICATION = VERIFICATION[:6]

NEW_PERMISSIONS = {
    "browser:read": ("View browser missions and detected challenges",
                     ["owner", "admin", "analyst", "viewer"]),
    "browser:run": ("Start or cancel browser missions", ["owner", "admin", "analyst"]),
}
ENTITLEMENT = "max_browser_missions_per_day"
PLAN_LIMITS = {"trial": 200, "unlimited": None}


CLAIM_FN = """
    CREATE OR REPLACE FUNCTION browser_claim_missions(p_worker uuid, p_limit int, p_tenant_concurrency int)
      RETURNS TABLE (mission_id uuid, mission_tenant uuid)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      DECLARE
        cap int; r record; pol record; st record; dom_running int; ten_running int;
      BEGIN
        SELECT greatest(0, least(p_limit, w.max_contexts - (
                 SELECT count(*) FROM browser_missions m WHERE m.worker_id = w.id
                   AND m.status IN ('ASSIGNED','RUNNING'))))
          INTO cap FROM browser_workers w WHERE w.id = p_worker AND w.status = 'online';
        IF cap IS NULL OR cap <= 0 THEN RETURN; END IF;
        FOR r IN SELECT m.id, m.tenant_id, m.domain FROM browser_missions m
                 WHERE m.status = 'QUEUED' AND m.run_after <= now() AND m.deadline_at > now()
                 ORDER BY m.priority DESC, m.created_at LIMIT 200
                 FOR UPDATE SKIP LOCKED LOOP
          EXIT WHEN cap <= 0;
          SELECT * INTO pol FROM browser_domain_policies
            WHERE domain IN (r.domain, '*') ORDER BY (domain = '*') LIMIT 1;
          IF NOT pol.allowed THEN CONTINUE; END IF;
          SELECT * INTO st FROM browser_domain_state WHERE domain = r.domain;
          IF st.cooldown_until IS NOT NULL AND st.cooldown_until > now() THEN CONTINUE; END IF;
          IF st.last_visit_at IS NOT NULL
             AND st.last_visit_at > now() - make_interval(secs => pol.min_interval_seconds)
          THEN CONTINUE; END IF;
          SELECT count(*) INTO dom_running FROM browser_missions
            WHERE domain = r.domain AND status IN ('ASSIGNED','RUNNING');
          IF dom_running >= pol.max_concurrency THEN CONTINUE; END IF;
          SELECT count(*) INTO ten_running FROM browser_missions
            WHERE tenant_id = r.tenant_id AND status IN ('ASSIGNED','RUNNING');
          IF ten_running >= {tenant_limit} THEN CONTINUE; END IF;
          UPDATE browser_missions SET status = 'ASSIGNED', worker_id = p_worker,
                 assigned_at = now(), heartbeat_at = now(), attempts = attempts + 1
            WHERE id = r.id;
          INSERT INTO browser_domain_state (domain, last_visit_at) VALUES (r.domain, now())
            ON CONFLICT (domain) DO UPDATE SET last_visit_at = now();
          cap := cap - 1;
          mission_id := r.id; mission_tenant := r.tenant_id;
          RETURN NEXT;
        END LOOP;
      END $$;
"""


def claim_function(tenant_limit: str = "p_tenant_concurrency") -> str:
    """Shared with later migrations that change how the per-tenant limit is computed."""
    return CLAIM_FN.replace("{tenant_limit}", tenant_limit).strip()


def _id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"repintel:{kind}:{key}")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    x = op.execute
    pairs = ", ".join(f"('{a}','{b}')" for a, bs in TRANSITIONS.items() for b in bs)
    x(f"""
    -- ------------------------------------------------------------------ platform level
    CREATE TABLE browser_workers (
      id uuid PRIMARY KEY,
      name varchar(100) NOT NULL,
      hostname varchar(200) NOT NULL DEFAULT '',
      version varchar(40) NOT NULL DEFAULT '',
      status varchar(20) NOT NULL DEFAULT 'online'
        CHECK (status IN ('online','draining','offline')),
      max_contexts integer NOT NULL CHECK (max_contexts BETWEEN 0 AND 64),
      active_contexts integer NOT NULL DEFAULT 0,
      metrics jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      missions_done integer NOT NULL DEFAULT 0,
      started_at timestamptz NOT NULL DEFAULT now(),
      last_heartbeat_at timestamptz NOT NULL DEFAULT now()
    );

    -- Per-domain politeness rules; '*' is the default. Read-only for the application.
    CREATE TABLE browser_domain_policies (
      domain varchar(255) PRIMARY KEY,
      allowed boolean NOT NULL DEFAULT true,
      max_concurrency integer NOT NULL DEFAULT 1 CHECK (max_concurrency BETWEEN 1 AND 20),
      min_interval_seconds integer NOT NULL DEFAULT 10 CHECK (min_interval_seconds >= 0),
      timeout_seconds integer NOT NULL DEFAULT 45 CHECK (timeout_seconds BETWEEN 5 AND 300),
      block_resources text[] NOT NULL DEFAULT '{{image,media,font}}',
      notes text NOT NULL DEFAULT ''
    );
    INSERT INTO browser_domain_policies (domain, notes)
      VALUES ('*', 'Default: one page at a time per domain, 10 s apart.');

    -- Shared across tenants on purpose: a challenge is a response to OUR crawler, so every
    -- tenant must back off from that domain. Contains no tenant data.
    CREATE TABLE browser_domain_state (
      domain varchar(255) PRIMARY KEY,
      cooldown_until timestamptz,
      consecutive_challenges integer NOT NULL DEFAULT 0,
      last_challenge_kind varchar(30),
      last_challenge_at timestamptz,
      last_visit_at timestamptz
    );

    -- ------------------------------------------------------------------ tenant level
    ALTER TABLE sources
      ADD COLUMN render_mode varchar(10) NOT NULL DEFAULT 'auto'
        CHECK (render_mode IN ('auto','static','browser')),
      ADD COLUMN challenge_count integer NOT NULL DEFAULT 0,
      ADD COLUMN last_challenge_kind varchar(30),
      ADD COLUMN last_challenge_at timestamptz;

    ALTER TABLE mentions DROP CONSTRAINT mentions_verification_status_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_verification_status_check
      CHECK (verification_status IN ({_in(VERIFICATION)}));
    ALTER TABLE mentions ADD COLUMN verified_with varchar(10)
      CHECK (verified_with IN ('http','browser'));

    CREATE TABLE browser_missions (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      kind varchar(20) NOT NULL CHECK (kind IN ('render_page','check_source')),
      url varchar(2000) NOT NULL,
      domain varchar(255) NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'CREATED' CHECK (status IN ({_in(STATES)})),
      priority smallint NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
      mention_id uuid,
      source_id uuid,
      worker_id uuid REFERENCES browser_workers(id) ON DELETE SET NULL,
      attempts integer NOT NULL DEFAULT 0,
      max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
      timeout_seconds integer NOT NULL DEFAULT 45 CHECK (timeout_seconds BETWEEN 5 AND 300),
      run_after timestamptz NOT NULL DEFAULT now(),
      deadline_at timestamptz NOT NULL DEFAULT now() + interval '24 hours',
      params jsonb NOT NULL DEFAULT '{{}}'::jsonb,     -- e.g. terms to verify on the page
      authorization_record jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      result jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      error varchar(300),
      created_by uuid REFERENCES users(id) ON DELETE SET NULL,
      assigned_at timestamptz,
      started_at timestamptz,
      heartbeat_at timestamptz,
      finished_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE (tenant_id, id),
      FOREIGN KEY (tenant_id, mention_id) REFERENCES mentions (tenant_id, id)
        ON DELETE SET NULL (mention_id),
      FOREIGN KEY (tenant_id, source_id) REFERENCES sources (tenant_id, id) ON DELETE CASCADE
    );
    CREATE INDEX browser_missions_queue_idx ON browser_missions (status, priority DESC, created_at)
      WHERE status IN ('QUEUED','ASSIGNED','RUNNING','PAUSED');
    CREATE INDEX browser_missions_tenant_idx ON browser_missions (tenant_id, created_at DESC);
    CREATE INDEX browser_missions_domain_idx ON browser_missions (domain)
      WHERE status IN ('ASSIGNED','RUNNING');

    -- One row per browser context (opened for exactly one mission, then destroyed).
    CREATE TABLE browser_sessions (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL,
      mission_id uuid NOT NULL,
      worker_id uuid REFERENCES browser_workers(id) ON DELETE SET NULL,
      isolation jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      opened_at timestamptz NOT NULL DEFAULT now(),
      closed_at timestamptz,
      cleanup_verified boolean NOT NULL DEFAULT false,
      requests_blocked integer NOT NULL DEFAULT 0,
      FOREIGN KEY (tenant_id, mission_id) REFERENCES browser_missions (tenant_id, id)
        ON DELETE CASCADE
    );

    -- Audit trail: every state transition (written by trigger), navigation and decision.
    CREATE TABLE browser_session_events (
      id bigserial PRIMARY KEY,
      tenant_id uuid NOT NULL,
      mission_id uuid NOT NULL,
      kind varchar(20) NOT NULL CHECK (kind IN ('transition','validation','navigation',
        'challenge','blocked_request','cleanup','note')),
      from_status varchar(20),
      to_status varchar(20),
      detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      worker_id uuid,
      created_at timestamptz NOT NULL DEFAULT now(),
      FOREIGN KEY (tenant_id, mission_id) REFERENCES browser_missions (tenant_id, id)
        ON DELETE CASCADE
    );
    CREATE INDEX browser_session_events_mission_idx ON browser_session_events (mission_id, id);

    CREATE TABLE captcha_events (
      id uuid PRIMARY KEY,
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
      mission_id uuid,
      source_id uuid,
      mention_id uuid,
      domain varchar(255) NOT NULL,
      url varchar(2000) NOT NULL,
      via varchar(10) NOT NULL CHECK (via IN ('http','browser')),
      kind varchar(30) NOT NULL CHECK (kind IN ({_in(CHALLENGE_KINDS)})),
      confidence numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
      signals jsonb NOT NULL DEFAULT '[]'::jsonb,
      state varchar(20) NOT NULL DEFAULT 'DETECTED' CHECK (state IN ({_in(CHALLENGE_STATES)})),
      history jsonb NOT NULL DEFAULT '[]'::jsonb,
      response jsonb NOT NULL DEFAULT '{{}}'::jsonb,
      detected_at timestamptz NOT NULL DEFAULT now(),
      resolved_at timestamptz,
      FOREIGN KEY (tenant_id, mission_id) REFERENCES browser_missions (tenant_id, id)
        ON DELETE SET NULL (mission_id),
      FOREIGN KEY (tenant_id, source_id) REFERENCES sources (tenant_id, id)
        ON DELETE SET NULL (source_id),
      FOREIGN KEY (tenant_id, mention_id) REFERENCES mentions (tenant_id, id)
        ON DELETE SET NULL (mention_id)
    );
    CREATE INDEX captcha_events_tenant_idx ON captcha_events (tenant_id, detected_at DESC);
    """)

    for t in ("browser_missions", "browser_sessions", "browser_session_events",
              "captcha_events"):
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {t}
          USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
        """)
    # Fleet tables: visible to platform admins only, written only through the functions below.
    for t in ("browser_workers", "browser_domain_state"):
        x(f"""
        ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
        CREATE POLICY platform_admin_read ON {t} FOR SELECT USING (app_is_platform_admin());
        """)

    x(f"""
    -- State machine, enforced in the database whatever the caller.
    CREATE FUNCTION browser_mission_guard() RETURNS trigger
      LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
      BEGIN
        IF TG_OP = 'INSERT' THEN
          IF NEW.status <> 'CREATED' THEN
            RAISE EXCEPTION 'browser mission must start in CREATED' USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END IF;
        IF NEW.tenant_id <> OLD.tenant_id OR NEW.id <> OLD.id THEN
          RAISE EXCEPTION 'browser mission identity is immutable' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status THEN
          IF (OLD.status, NEW.status) NOT IN ({pairs}) THEN
            RAISE EXCEPTION 'illegal browser mission transition % -> %', OLD.status, NEW.status
              USING ERRCODE = 'check_violation';
          END IF;
          NEW.updated_at := now();
          IF NEW.status IN ('COMPLETED','FAILED','CANCELLED','EXPIRED') THEN
            NEW.finished_at := coalesce(NEW.finished_at, now());
          END IF;
        END IF;
        RETURN NEW;
      END $$;
    CREATE TRIGGER browser_mission_guard BEFORE INSERT OR UPDATE ON browser_missions
      FOR EACH ROW EXECUTE FUNCTION browser_mission_guard();

    -- Audit of every transition, whoever makes it (API, worker or recovery function).
    CREATE FUNCTION browser_mission_audit() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      BEGIN
        IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
          INSERT INTO browser_session_events (tenant_id, mission_id, kind, from_status,
                                              to_status, worker_id, detail)
            VALUES (NEW.tenant_id, NEW.id, 'transition',
                    CASE WHEN TG_OP = 'UPDATE' THEN OLD.status END, NEW.status, NEW.worker_id,
                    CASE WHEN NEW.error IS NOT NULL
                          AND (TG_OP = 'INSERT' OR NEW.error IS DISTINCT FROM OLD.error)
                         THEN jsonb_build_object('error', NEW.error) ELSE '{{}}'::jsonb END);
        END IF;
        RETURN NULL;
      END $$;
    CREATE TRIGGER browser_mission_audit AFTER INSERT OR UPDATE ON browser_missions
      FOR EACH ROW EXECUTE FUNCTION browser_mission_audit();

    -- Workers announce themselves and their capacity.
    CREATE FUNCTION browser_worker_heartbeat(p_id uuid, p_name text, p_host text,
        p_version text, p_max int, p_active int, p_metrics jsonb, p_status text)
      RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      INSERT INTO browser_workers AS w (id, name, hostname, version, status, max_contexts,
                                       active_contexts, metrics)
      VALUES (p_id, left(p_name, 100), left(p_host, 200), left(p_version, 40), p_status,
              p_max, p_active, coalesce(p_metrics, '{{}}'::jsonb))
      ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,
        max_contexts = EXCLUDED.max_contexts, active_contexts = EXCLUDED.active_contexts,
        metrics = EXCLUDED.metrics, last_heartbeat_at = now()
    $$;

    -- Missions a worker is still processing stay alive.
    CREATE FUNCTION browser_mission_heartbeat(p_worker uuid, p_ids uuid[]) RETURNS int
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      WITH u AS (UPDATE browser_missions SET heartbeat_at = now()
                 WHERE worker_id = p_worker AND id = ANY(p_ids)
                   AND status IN ('ASSIGNED','RUNNING') RETURNING 1)
      SELECT count(*)::int FROM u
    $$;

    -- Resource-aware assignment: worker free slots, per-domain concurrency and spacing,
    -- domain cooldown after challenges, per-tenant concurrency, priority then age.
    {claim_function()}

    -- Failure recovery: silent workers go offline, their missions are retried or failed,
    -- queued/paused missions past their deadline expire.
    CREATE FUNCTION browser_recover(p_stale_seconds int)
      RETURNS TABLE (requeued int, failed int, expired int, workers_offline int)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      BEGIN
        UPDATE browser_workers SET status = 'offline', active_contexts = 0
          WHERE status <> 'offline'
            AND last_heartbeat_at < now() - make_interval(secs => p_stale_seconds);
        GET DIAGNOSTICS workers_offline = ROW_COUNT;
        UPDATE browser_missions SET status = 'QUEUED', worker_id = NULL,
               run_after = now() + interval '30 seconds', error = 'worker_lost'
          WHERE status IN ('ASSIGNED','RUNNING') AND attempts < max_attempts
            AND coalesce(heartbeat_at, assigned_at) < now() - make_interval(secs => p_stale_seconds);
        GET DIAGNOSTICS requeued = ROW_COUNT;
        UPDATE browser_missions SET status = 'FAILED', error = 'worker_lost'
          WHERE status IN ('ASSIGNED','RUNNING') AND attempts >= max_attempts
            AND coalesce(heartbeat_at, assigned_at) < now() - make_interval(secs => p_stale_seconds);
        GET DIAGNOSTICS failed = ROW_COUNT;
        UPDATE browser_missions SET status = 'EXPIRED', error = 'deadline_passed'
          WHERE status IN ('QUEUED','PAUSED','WAITING_FOR_HUMAN') AND deadline_at <= now();
        GET DIAGNOSTICS expired = ROW_COUNT;
        -- Mentions whose rendering will not happen go back to an honest status.
        UPDATE mentions mn SET verification_status = 'page_unavailable'
          FROM browser_missions bm
          WHERE mn.verification_status = 'pending_render' AND bm.mention_id = mn.id
            AND bm.tenant_id = mn.tenant_id AND bm.status IN ('FAILED','EXPIRED','CANCELLED')
            AND NOT EXISTS (SELECT 1 FROM browser_missions b2 WHERE b2.mention_id = mn.id
                            AND b2.status NOT IN ('FAILED','EXPIRED','CANCELLED','COMPLETED'));
        RETURN NEXT;
      END $$;

    -- Back off from a domain that challenged our crawler: 1 h, 4 h, 16 h, then 72 h.
    CREATE FUNCTION browser_note_challenge(p_domain text, p_kind text) RETURNS timestamptz
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      INSERT INTO browser_domain_state AS d (domain, consecutive_challenges, last_challenge_kind,
                                             last_challenge_at, cooldown_until)
      VALUES (p_domain, 1, p_kind, now(), now() + interval '1 hour')
      ON CONFLICT (domain) DO UPDATE SET
        consecutive_challenges = d.consecutive_challenges + 1,
        last_challenge_kind = p_kind, last_challenge_at = now(),
        cooldown_until = now() + least(interval '72 hours',
          interval '1 hour' * power(4, d.consecutive_challenges))
      RETURNING cooldown_until
    $$;

    CREATE FUNCTION browser_note_success(p_domain text) RETURNS void
      LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
      UPDATE browser_domain_state SET consecutive_challenges = 0, cooldown_until = NULL
        WHERE domain = p_domain AND consecutive_challenges > 0
    $$;

    CREATE FUNCTION browser_domain_cooldown(p_domain text) RETURNS timestamptz
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT cooldown_until FROM browser_domain_state
        WHERE domain = p_domain AND cooldown_until > now()
    $$;

    -- Today's missions of the caller's tenant (plan quota).
    CREATE FUNCTION browser_missions_today() RETURNS int
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
      SELECT count(*)::int FROM browser_missions
        WHERE tenant_id = app_current_tenant() AND created_at > now() - interval '24 hours'
    $$;
    """)

    fns = ["browser_worker_heartbeat(uuid, text, text, text, int, int, jsonb, text)",
           "browser_mission_heartbeat(uuid, uuid[])", "browser_claim_missions(uuid, int, int)",
           "browser_recover(int)", "browser_note_challenge(text, text)",
           "browser_note_success(text)", "browser_domain_cooldown(text)",
           "browser_missions_today()"]
    for f in fns:
        x(f"REVOKE ALL ON FUNCTION {f} FROM PUBLIC; GRANT EXECUTE ON FUNCTION {f} TO {APP_ROLE};")
    x(f"""
    GRANT SELECT ON browser_workers, browser_domain_state, browser_domain_policies TO {APP_ROLE};
    GRANT SELECT, INSERT, UPDATE, DELETE ON browser_missions, browser_sessions, captcha_events
      TO {APP_ROLE};
    GRANT SELECT, INSERT ON browser_session_events TO {APP_ROLE};
    GRANT USAGE ON SEQUENCE browser_session_events_id_seq TO {APP_ROLE};
    INSERT INTO connector_policies (key, name, access_method, legal_basis, data_categories,
                                    restrictions, retention_days)
    VALUES ('browser', 'Headless browser rendering of public pages', 'public_web_page_rendered',
      'Publicly accessible pages rendered like a visitor would, robots.txt honoured, honest '
      'user agent; no login, paywall, CAPTCHA or anti-bot bypass: challenges stop the visit.',
      ARRAY['url','title','text excerpt around the brand mention','publication date'],
      'Only an excerpt around the match is stored; pages, cookies and storage are discarded '
      'after each visit.', 365);
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
    DELETE FROM connector_policies WHERE key = 'browser';
    DROP TABLE IF EXISTS captcha_events, browser_session_events, browser_sessions,
      browser_missions, browser_domain_state, browser_domain_policies, browser_workers;
    DROP FUNCTION IF EXISTS browser_mission_guard(), browser_mission_audit(),
      browser_missions_today(),
      browser_worker_heartbeat(uuid, text, text, text, int, int, jsonb, text),
      browser_mission_heartbeat(uuid, uuid[]), browser_claim_missions(uuid, int, int),
      browser_recover(int), browser_note_challenge(text, text), browser_note_success(text),
      browser_domain_cooldown(text);
    UPDATE mentions SET verification_status = 'page_unavailable'
      WHERE verification_status IN ('pending_render','blocked_by_challenge');
    ALTER TABLE mentions DROP COLUMN IF EXISTS verified_with;
    ALTER TABLE mentions DROP CONSTRAINT mentions_verification_status_check;
    ALTER TABLE mentions ADD CONSTRAINT mentions_verification_status_check
      CHECK (verification_status IN ({_in(OLD_VERIFICATION)}));
    ALTER TABLE sources DROP COLUMN IF EXISTS render_mode,
      DROP COLUMN IF EXISTS challenge_count, DROP COLUMN IF EXISTS last_challenge_kind,
      DROP COLUMN IF EXISTS last_challenge_at;
    """)
