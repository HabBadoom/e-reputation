-- Migration 0008: AI tasks isolation, approval-required actions, approval rules.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set uv '''aaaaaaaa-0000-0000-0000-0000000000a2'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''
\set task '''aaaaaaaa-0000-0000-0000-00000000d001'''
\set act '''aaaaaaaa-0000-0000-0000-00000000d101'''
\set act2 '''aaaaaaaa-0000-0000-0000-00000000d102'''

SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :uv, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:uv, 'v@a.dz', 'x', 'Viewer A');
SELECT set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'Owner A');
INSERT INTO tenants (id, name, slug) VALUES (:ta, 'Tenant A', 'tenant-a');
INSERT INTO memberships (id, tenant_id, user_id, role_id, status) VALUES
  (gen_random_uuid(), :ta, :ua, (SELECT id FROM roles WHERE key = 'owner'), 'active'),
  (gen_random_uuid(), :ta, :uv, (SELECT id FROM roles WHERE key = 'viewer'), 'active');
INSERT INTO ai_tasks (id, tenant_id, kind, created_by) VALUES (:task, :ta, 'brand_health', :ua);
INSERT INTO ai_actions (id, tenant_id, task_id, kind, risk, requires_approval, status)
  VALUES (:act, :ta, :task, 'use_response', 'high', true, 'pending_approval'),
         (:act2, :ta, :task, 'run_monitoring', 'low', false, 'proposed');
INSERT INTO approval_requests (id, tenant_id, action_id, requested_by, expires_at)
  VALUES (gen_random_uuid(), :ta, :act, :ua, now() + interval '1 day');

SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'B');
INSERT INTO tenants (id, name, slug) VALUES (:tb, 'Tenant B', 'tenant-b');
SELECT 'PASS seed';

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM ai_tasks) = 0, 'B sees A tasks';
  ASSERT (SELECT count(*) FROM ai_actions) = 0, 'B sees A actions';
  ASSERT (SELECT count(*) FROM approval_requests) = 0, 'B sees A approvals';
  ASSERT (SELECT count(*) FROM agent_runs) = 0, 'B sees A runs';
  UPDATE approval_requests SET status = 'approved';   -- invisible: no row touched
  BEGIN
    INSERT INTO ai_actions (id, tenant_id, task_id, kind, risk, requires_approval, status)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001',
              'aaaaaaaa-0000-0000-0000-00000000d001', 'run_monitoring', 'low', false, 'proposed');
    RAISE EXCEPTION 'action attached to another tenant task';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
END $$;
SELECT 'PASS 1: tasks, runs, actions, approvals isolated';

SELECT set_config('app.tenant_id', :ta, true);
DO $$ BEGIN
  ASSERT (SELECT status FROM approval_requests) = 'pending', 'B approved A request';
  BEGIN
    UPDATE ai_actions SET status = 'executed' WHERE kind = 'use_response';
    RAISE EXCEPTION 'approval-required action executed without approval';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    UPDATE ai_actions SET requires_approval = false WHERE kind = 'use_response';
    RAISE EXCEPTION 'approval requirement removed';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    UPDATE approval_requests SET status = 'approved', decided_at = now(),
      decided_by = 'aaaaaaaa-0000-0000-0000-0000000000a2';
    RAISE EXCEPTION 'viewer approved';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    UPDATE approval_requests SET status = 'approved';
    RAISE EXCEPTION 'approval without decider';
  EXCEPTION WHEN check_violation THEN NULL; END;
  UPDATE ai_actions SET status = 'executed' WHERE kind = 'run_monitoring';
  UPDATE approval_requests SET status = 'approved', decided_at = now(),
    decided_by = 'aaaaaaaa-0000-0000-0000-0000000000a1';
  UPDATE ai_actions SET status = 'executed' WHERE kind = 'use_response';
  BEGIN
    UPDATE approval_requests SET status = 'rejected';
    RAISE EXCEPTION 'decided approval changed';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    UPDATE ai_actions SET status = 'failed' WHERE kind = 'use_response';
    RAISE EXCEPTION 'executed action reopened';
  EXCEPTION WHEN check_violation THEN NULL; END;
  ASSERT ai_tasks_today() = 1, 'quota count';
END $$;
SELECT 'PASS 2: sensitive actions need a recorded approval by an allowed member, decided once';

ROLLBACK;
SELECT 'ALL AGENT RLS TESTS PASSED';
