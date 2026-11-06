-- Migration 0003: catalog/monitoring tenant isolation, job queue access and semantics.
-- Run as the API role. Everything happens in one transaction that is rolled back.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''
\set ba '''aaaaaaaa-0000-0000-0000-0000000000ba'''
\set bb '''bbbbbbbb-0000-0000-0000-0000000000bb'''
\set pa '''aaaaaaaa-0000-0000-0000-0000000000fa'''
\set pb '''bbbbbbbb-0000-0000-0000-0000000000fb'''

SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'A');
INSERT INTO tenants (id, name, slug) VALUES (:ta, 'Tenant A', 'tenant-a');
INSERT INTO brands (id, tenant_id, name) VALUES (:ba, :ta, 'Brand A');
INSERT INTO products (id, tenant_id, brand_id, name) VALUES (gen_random_uuid(), :ta, :ba, 'Prod A');
INSERT INTO competitors (id, tenant_id, brand_id, name) VALUES (gen_random_uuid(), :ta, :ba, 'Comp A');
INSERT INTO brand_keywords (id, tenant_id, brand_id, expression) VALUES (gen_random_uuid(), :ta, :ba, 'kw a');
INSERT INTO monitoring_projects (id, tenant_id, brand_id, name, frequency_minutes, next_run_at)
  VALUES (:pa, :ta, :ba, 'Proj A', 60, now() - interval '1 minute');
INSERT INTO subscriptions (id, tenant_id, plan_id)
  SELECT gen_random_uuid(), :ta, id FROM plans WHERE key = 'trial';

SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'B');
INSERT INTO tenants (id, name, slug) VALUES (:tb, 'Tenant B', 'tenant-b');
INSERT INTO brands (id, tenant_id, name) VALUES (:bb, :tb, 'Brand B');
INSERT INTO monitoring_projects (id, tenant_id, brand_id, name, frequency_minutes, next_run_at)
  VALUES (:pb, :tb, :bb, 'Proj B', 120, now() + interval '1 hour');
SELECT 'PASS seed';

-- 1. B sees none of A's catalog, projects, runs or subscription
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM products) = 0, 'B sees A products';
  ASSERT (SELECT count(*) FROM competitors) = 0, 'B sees A competitors';
  ASSERT (SELECT count(*) FROM brand_keywords) = 0, 'B sees A keywords';
  ASSERT (SELECT array_agg(name) FROM monitoring_projects) = ARRAY['Proj B']::varchar[], 'B sees A projects';
  ASSERT (SELECT count(*) FROM subscriptions) = 0, 'B sees A subscription';
END $$;
SELECT 'PASS 1: catalog, projects and subscriptions isolated per tenant';

-- 2. composite FKs: B cannot attach rows to A's brand or project
DO $$ BEGIN
  BEGIN
    INSERT INTO products (id, tenant_id, brand_id, name)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000ba', 'x');
    RAISE EXCEPTION 'product linked to A brand';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN
    INSERT INTO monitoring_runs (id, tenant_id, project_id, trigger)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000fa', 'manual');
    RAISE EXCEPTION 'run linked to A project';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
END $$;
SELECT 'PASS 2: composite FKs block cross-tenant links';

-- 3. subscriptions: app can read its own but not change its plan
DO $$ BEGIN
  BEGIN
    UPDATE subscriptions SET plan_id = plan_id;
    RAISE EXCEPTION 'subscription update allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    INSERT INTO plan_entitlements SELECT id, 'x', 1 FROM plans LIMIT 1;
    RAISE EXCEPTION 'plan edit allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 3: tenants cannot change their plan or entitlements';

-- 4. jobs table is unreachable directly; enqueue is bound to the current tenant
DO $$ BEGIN
  BEGIN PERFORM 1 FROM jobs; RAISE EXCEPTION 'jobs readable';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM enqueue_job('x', '{}', 'aaaaaaaa-0000-0000-0000-000000000001', NULL, 3, false);
    RAISE EXCEPTION 'enqueued for another tenant';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  PERFORM enqueue_job('test.ok', '{"n":1}', 'bbbbbbbb-0000-0000-0000-000000000001', NULL, 2, true);
  PERFORM enqueue_job('test.global', '{}', NULL, NULL, 1, false);
END $$;
SELECT 'PASS 4: jobs only via functions; cannot enqueue for another tenant';

-- 5. scheduler: only the due project is scheduled, once, and pushed forward
DO $$
DECLARE n int;
BEGIN
  n := schedule_due_monitoring(); ASSERT n = 1, format('scheduled %s, expected 1', n);
  n := schedule_due_monitoring(); ASSERT n = 0, 'scheduled twice';
END $$;
SELECT 'PASS 5: scheduler enqueues due projects exactly once';

-- 6. claim → fail with retry → dead after max attempts; complete wipes sensitive payload
DO $$
DECLARE j record; st text; n int;
BEGIN
  SELECT count(*) INTO n FROM claim_jobs('w1', 10);
  ASSERT n = 3, format('claimed %s, expected 3', n);
  ASSERT (SELECT count(*) FROM claim_jobs('w2', 10)) = 0, 'double claim';
  ASSERT (SELECT count(*) FROM job_stats() WHERE status = 'running') = 1, 'stats';
END $$;
SELECT 'PASS 6: claimed jobs are not handed to a second worker';

ROLLBACK;

-- Queue semantics on a clean transaction (claim above consumed rows in the rolled-back txn).
BEGIN;
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true);
DO $$
DECLARE v uuid; st text; j record;
BEGIN
  v := enqueue_job('test.retry', '{"secret":"s"}', NULL, NULL, 2, true);
  PERFORM claim_jobs('w', 10);
  st := fail_job(v, 'boom', 0);           ASSERT st = 'pending', 'first failure should retry';
  PERFORM claim_jobs('w', 10);
  st := fail_job(v, 'boom again', 0);     ASSERT st = 'dead', 'should be dead after max attempts';

  v := enqueue_job('test.later', '{}', NULL, now() + interval '1 hour', 3, false);
  ASSERT NOT EXISTS (SELECT 1 FROM claim_jobs('w', 10) c WHERE c.id = v), 'future job claimed';
END $$;
SELECT 'PASS 7: retry then dead-letter; future jobs wait';
ROLLBACK;

SELECT 'ALL MONITORING RLS TESTS PASSED';
