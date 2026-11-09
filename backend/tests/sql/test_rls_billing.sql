-- Migration 0009: billing isolation, admin-only writes, invoice immutability, numbering.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set adm '''cccccccc-0000-0000-0000-0000000000c1'''
\set inv '''aaaaaaaa-0000-0000-0000-00000000e001'''

-- Platform administrator (flag set by the owner role, as an operator would).
SELECT set_config('app.user_id', :adm, true), set_config('app.tenant_id', :ta, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:adm, 'ops@x.dz', 'x', 'Ops');
SELECT set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'A');
INSERT INTO tenants (id, name, slug) VALUES (:ta, 'Tenant A', 'tenant-a');
INSERT INTO subscriptions (id, tenant_id, plan_id, status)
  SELECT gen_random_uuid(), :ta, id, 'trialing' FROM plans WHERE key = 'trial';
INSERT INTO billing_accounts (tenant_id, legal_name, nif) VALUES (:ta, 'SARL A', '0001');
INSERT INTO usage_events (tenant_id, metric, quantity) VALUES (:ta, 'mentions_collected', 7);
SELECT 'PASS seed';

DO $$ DECLARE n int; BEGIN
  ASSERT usage_this_month('mentions_collected') = 7, 'usage sum';
  -- A member cannot write invoices, change the plan, edit plans or prices.
  BEGIN
    INSERT INTO invoices (id, tenant_id, currency) VALUES (gen_random_uuid(),
      'aaaaaaaa-0000-0000-0000-000000000001', 'DZD');
    RAISE EXCEPTION 'member created an invoice';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    UPDATE subscriptions SET status = 'active';
    RAISE EXCEPTION 'member activated its subscription';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  UPDATE plans SET is_active = true WHERE key = 'starter';
  ASSERT NOT (SELECT is_active FROM plans WHERE key = 'starter'), 'member activated a plan';
  BEGIN
    INSERT INTO plan_prices (id, plan_id, currency, billing_interval, amount_minor)
      SELECT gen_random_uuid(), id, 'DZD', 'month', 1 FROM plans WHERE key = 'starter';
    RAISE EXCEPTION 'member set a price';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM admin_tenants();
    RAISE EXCEPTION 'member listed tenants';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM next_invoice_number('FAC');
    RAISE EXCEPTION 'member took an invoice number';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 1: members cannot bill themselves, change plans or prices';
ROLLBACK;

-- Administrator flows (subscriptions, invoices, immutability, numbering) need a platform
-- administrator, whose flag only the owner role can set: they are covered by test_billing.py.
BEGIN;
SELECT set_config('app.user_id', 'aaaaaaaa-0000-0000-0000-0000000000a1', true),
       set_config('app.tenant_id', 'aaaaaaaa-0000-0000-0000-000000000001', true);
INSERT INTO users (id, email, password_hash, full_name)
  VALUES ('aaaaaaaa-0000-0000-0000-0000000000a1', 'a@a.dz', 'x', 'A');
INSERT INTO tenants (id, name, slug) VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'Tenant A', 'tenant-a');
INSERT INTO subscriptions (id, tenant_id, plan_id, status, trial_ends_at)
  SELECT gen_random_uuid(), 'aaaaaaaa-0000-0000-0000-000000000001', id, 'trialing',
         now() - interval '1 day' FROM plans WHERE key = 'trial';
INSERT INTO billing_accounts (tenant_id, legal_name) VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001', 'SARL A');
INSERT INTO plan_change_requests (id, tenant_id, plan_id, billing_interval)
  SELECT gen_random_uuid(), 'aaaaaaaa-0000-0000-0000-000000000001', id, 'month'
  FROM plans WHERE key = 'starter';
SELECT set_config('app.user_id', 'bbbbbbbb-0000-0000-0000-0000000000b1', true),
       set_config('app.tenant_id', 'bbbbbbbb-0000-0000-0000-000000000001', true);
INSERT INTO users (id, email, password_hash, full_name)
  VALUES ('bbbbbbbb-0000-0000-0000-0000000000b1', 'b@b.dz', 'x', 'B');
INSERT INTO tenants (id, name, slug) VALUES ('bbbbbbbb-0000-0000-0000-000000000001', 'Tenant B', 'tenant-b');

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM billing_accounts) = 0, 'B sees A billing account';
  ASSERT (SELECT count(*) FROM plan_change_requests) = 0, 'B sees A requests';
  ASSERT (SELECT count(*) FROM subscriptions) = 0, 'B sees A subscription';
  ASSERT usage_this_month('mentions_collected') = 0, 'B counts A usage';
  BEGIN
    INSERT INTO usage_events (tenant_id, metric, quantity)
      VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'ai_calls', 1);
    RAISE EXCEPTION 'B wrote usage for A';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  PERFORM set_config('app.tenant_id', '', true);
  ASSERT (SELECT count(*) FROM expire_trials()) = 1, 'expired trial not detected';
END $$;
SELECT 'PASS 2: billing data isolated per tenant; expired trials detected';
ROLLBACK;
SELECT 'ALL BILLING RLS TESTS PASSED';
