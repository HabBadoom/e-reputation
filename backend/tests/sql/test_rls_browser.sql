-- Migration 0007: browser fleet isolation, mission state machine, scheduling and recovery.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''
\set sa '''aaaaaaaa-0000-0000-0000-0000000000c1'''
\set w1 '''eeeeeeee-0000-0000-0000-000000000001'''

SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'A');
INSERT INTO tenants (id, name, slug) VALUES (:ta, 'Tenant A', 'tenant-a');
INSERT INTO sources (id, tenant_id, kind, url, domain, name, connector)
  VALUES (:sa, :ta, 'website', 'https://d1.dz/', 'd1.dz', 'd1', 'web');
INSERT INTO browser_missions (id, tenant_id, kind, url, domain, source_id, priority)
  VALUES ('aaaaaaaa-0000-0000-0000-00000000f001', :ta, 'check_source', 'https://d1.dz/', 'd1.dz', :sa, 5),
         ('aaaaaaaa-0000-0000-0000-00000000f002', :ta, 'check_source', 'https://d1.dz/x', 'd1.dz', :sa, 9),
         ('aaaaaaaa-0000-0000-0000-00000000f003', :ta, 'check_source', 'https://d2.dz/', 'd2.dz', NULL, 1),
         ('aaaaaaaa-0000-0000-0000-00000000f004', :ta, 'check_source', 'https://d3.dz/', 'd3.dz', NULL, 1);
UPDATE browser_missions SET status = 'VALIDATING';
UPDATE browser_missions SET status = 'QUEUED';
INSERT INTO captcha_events (id, tenant_id, domain, url, via, kind, confidence)
  VALUES (gen_random_uuid(), :ta, 'd3.dz', 'https://d3.dz/', 'http', 'turnstile', 0.95);

SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'B');
INSERT INTO tenants (id, name, slug) VALUES (:tb, 'Tenant B', 'tenant-b');
SELECT 'PASS seed';

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM browser_missions) = 0, 'B sees A missions';
  ASSERT (SELECT count(*) FROM browser_session_events) = 0, 'B sees A events';
  ASSERT (SELECT count(*) FROM captcha_events) = 0, 'B sees A challenges';
  BEGIN
    INSERT INTO browser_missions (id, tenant_id, kind, url, domain, source_id)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'check_source',
              'https://d1.dz/', 'd1.dz', 'aaaaaaaa-0000-0000-0000-0000000000c1');
    RAISE EXCEPTION 'mission bound to another tenant source';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
END $$;
SELECT 'PASS 1: missions, events and challenges isolated; no cross-tenant source';

DO $$ BEGIN
  BEGIN
    INSERT INTO browser_missions (id, tenant_id, kind, url, domain, status)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'render_page',
              'https://x.dz/', 'x.dz', 'QUEUED');
    RAISE EXCEPTION 'mission inserted directly as QUEUED';
  EXCEPTION WHEN check_violation THEN NULL; END;
  INSERT INTO browser_missions (id, tenant_id, kind, url, domain)
    VALUES ('bbbbbbbb-0000-0000-0000-00000000f001', 'bbbbbbbb-0000-0000-0000-000000000001',
            'render_page', 'https://x.dz/', 'x.dz');
  BEGIN
    UPDATE browser_missions SET status = 'RUNNING';
    RAISE EXCEPTION 'CREATED -> RUNNING accepted (validation skipped)';
  EXCEPTION WHEN check_violation THEN NULL; END;
  UPDATE browser_missions SET status = 'CANCELLED';
  BEGIN
    UPDATE browser_missions SET status = 'QUEUED';
    RAISE EXCEPTION 'terminal mission revived';
  EXCEPTION WHEN check_violation THEN NULL; END;
  ASSERT (SELECT array_agg(to_status ORDER BY id) FROM browser_session_events)
         = ARRAY['CREATED','CANCELLED']::varchar[], 'transitions not audited';
  ASSERT (SELECT finished_at IS NOT NULL FROM browser_missions), 'finished_at not set';
END $$;
SELECT 'PASS 2: state machine enforced in the database, every transition audited';

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM browser_workers) = 0, 'workers visible to non-admin';
  BEGIN
    INSERT INTO browser_workers (id, name, max_contexts) VALUES (gen_random_uuid(), 'x', 1);
    RAISE EXCEPTION 'app wrote browser_workers directly';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    UPDATE browser_domain_policies SET allowed = true;
    RAISE EXCEPTION 'app changed domain policies';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    INSERT INTO browser_domain_state (domain) VALUES ('x.dz');
    RAISE EXCEPTION 'app wrote domain state directly';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 3: fleet tables readable by platform admins only, writable only via functions';

-- Scheduling runs as the worker (no tenant context).
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true);
SELECT browser_worker_heartbeat(:w1, 'w1', 'host', 't', 3, 0, '{}', 'online');
DO $$ DECLARE got uuid[]; BEGIN
  PERFORM browser_note_challenge('d3.dz', 'turnstile');
  SELECT array_agg(mission_id ORDER BY mission_id) INTO got
    FROM browser_claim_missions('eeeeeeee-0000-0000-0000-000000000001', 10, 5);
  -- d1: only the higher-priority mission (one per domain); d2 claimed; d3 cooling down.
  ASSERT got = ARRAY['aaaaaaaa-0000-0000-0000-00000000f002',
                     'aaaaaaaa-0000-0000-0000-00000000f003']::uuid[], format('claimed %s', got);
  ASSERT (SELECT count(*) FROM browser_claim_missions('eeeeeeee-0000-0000-0000-000000000001', 10, 5)) = 0,
    'd1 claimed again despite concurrency / spacing';
END $$;
SELECT 'PASS 4: resource-aware claim (priority, one per domain, cooldown after challenge)';

DO $$ DECLARE r record; BEGIN
  ASSERT browser_domain_cooldown('d3.dz') > now() + interval '50 minutes', 'first cooldown 1h';
  PERFORM browser_note_challenge('d3.dz', 'turnstile');
  ASSERT browser_domain_cooldown('d3.dz') > now() + interval '3 hours', 'second cooldown 4h';
  PERFORM browser_note_success('d3.dz');
  ASSERT browser_domain_cooldown('d3.dz') IS NULL, 'success did not clear cooldown';
END $$;
SELECT 'PASS 5: exponential domain back-off, cleared on success';

-- Worker silent: one mission retried, one exhausted, a queued one expires.
DO $$ DECLARE r record; BEGIN
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  UPDATE browser_missions SET status = 'RUNNING' WHERE id = 'aaaaaaaa-0000-0000-0000-00000000f002';
  UPDATE browser_missions SET heartbeat_at = now() - interval '10 minutes',
         max_attempts = CASE WHEN id = 'aaaaaaaa-0000-0000-0000-00000000f003' THEN 1 ELSE 3 END
    WHERE status IN ('ASSIGNED','RUNNING');
  UPDATE browser_missions SET deadline_at = now() - interval '1 second'
    WHERE id = 'aaaaaaaa-0000-0000-0000-00000000f004';
  PERFORM set_config('app.tenant_id', '', true);
  SELECT * INTO r FROM browser_recover(120);
  ASSERT r.requeued = 1 AND r.failed = 1 AND r.expired = 1, format('recover %s', r);
  PERFORM set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
  ASSERT (SELECT status FROM browser_missions WHERE id = 'aaaaaaaa-0000-0000-0000-00000000f002') = 'QUEUED';
  ASSERT (SELECT status FROM browser_missions WHERE id = 'aaaaaaaa-0000-0000-0000-00000000f003') = 'FAILED';
  ASSERT (SELECT status FROM browser_missions WHERE id = 'aaaaaaaa-0000-0000-0000-00000000f004') = 'EXPIRED';
  ASSERT browser_missions_today() = 4, 'quota count';
END $$;
SELECT 'PASS 6: recovery re-queues, fails after max attempts, expires past deadline';

ROLLBACK;
SELECT 'ALL BROWSER RLS TESTS PASSED';
