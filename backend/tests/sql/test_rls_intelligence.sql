-- Migration 0006: alerts, rules, webhooks, crises, trends and reports are tenant-isolated.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''
\set ba '''aaaaaaaa-0000-0000-0000-0000000000ba'''
\set bb '''bbbbbbbb-0000-0000-0000-0000000000bb'''
\set wa '''aaaaaaaa-0000-0000-0000-0000000000e1'''

SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'A');
INSERT INTO tenants (id, name, slug) VALUES (:ta, 'Tenant A', 'tenant-a');
INSERT INTO brands (id, tenant_id, name) VALUES (:ba, :ta, 'Brand A');
INSERT INTO webhooks (id, tenant_id, name, url, secret_enc) VALUES (:wa, :ta, 'hook', 'https://hooks.example.dz/a', 'enc');
INSERT INTO alert_rules (id, tenant_id, name, kind, webhook_id) VALUES (gen_random_uuid(), :ta, 'r', 'crisis', :wa);
INSERT INTO alerts (id, tenant_id, kind, severity, title, dedup_key) VALUES (gen_random_uuid(), :ta, 'crisis', 'HIGH', 't', 'k1');
INSERT INTO crisis_events (id, tenant_id, brand_id, severity) VALUES (gen_random_uuid(), :ta, :ba, 'HIGH');
INSERT INTO reports (id, tenant_id, kind, period_start, period_end, format)
  VALUES (gen_random_uuid(), :ta, 'weekly', now() - interval '7 days', now(), 'pdf');

SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'B');
INSERT INTO tenants (id, name, slug) VALUES (:tb, 'Tenant B', 'tenant-b');
INSERT INTO brands (id, tenant_id, name) VALUES (:bb, :tb, 'Brand B');
SELECT 'PASS seed';

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM webhooks) = 0, 'B sees A webhooks';
  ASSERT (SELECT count(*) FROM alert_rules) = 0, 'B sees A rules';
  ASSERT (SELECT count(*) FROM alerts) = 0, 'B sees A alerts';
  ASSERT (SELECT count(*) FROM crisis_events) = 0, 'B sees A crises';
  ASSERT (SELECT count(*) FROM reports) = 0, 'B sees A reports';
END $$;
SELECT 'PASS 1: intelligence tables isolated';

DO $$ BEGIN
  BEGIN
    INSERT INTO alert_rules (id, tenant_id, name, kind, webhook_id)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'steal', 'crisis',
              'aaaaaaaa-0000-0000-0000-0000000000e1');
    RAISE EXCEPTION 'rule wired to another tenant webhook';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN
    INSERT INTO webhooks (id, tenant_id, name, url, secret_enc)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'h', 'http://plain.dz', 'e');
    RAISE EXCEPTION 'non-https webhook accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
  INSERT INTO alerts (id, tenant_id, kind, severity, title, dedup_key)
    VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'crisis', 'LOW', 't', 'k1');
  BEGIN
    INSERT INTO alerts (id, tenant_id, kind, severity, title, dedup_key)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'crisis', 'LOW', 't', 'k1');
    RAISE EXCEPTION 'duplicate alert accepted';
  EXCEPTION WHEN unique_violation THEN NULL; END;
END $$;
SELECT 'PASS 2: no cross-tenant webhook use; https only; alerts de-duplicated per tenant';

DO $$ BEGIN
  ASSERT schedule_alert_evaluations(15) = 1, 'tenant A not scheduled';
  ASSERT schedule_alert_evaluations(15) = 0, 'scheduled twice';
END $$;
SELECT 'PASS 3: alert evaluation scheduled once per tenant';

DO $$ BEGIN
  ASSERT request_alert_evaluation() = true, 'tenant B evaluation not requested';
  ASSERT request_alert_evaluation() = false, 'tenant B evaluation requested twice';
  PERFORM set_config('app.tenant_id', '', true);
  ASSERT request_alert_evaluation() = false, 'evaluation requested without tenant';
END $$;
SELECT 'PASS 4: on-demand evaluation uses the caller tenant, once while pending';

ROLLBACK;
SELECT 'ALL INTELLIGENCE RLS TESTS PASSED';
