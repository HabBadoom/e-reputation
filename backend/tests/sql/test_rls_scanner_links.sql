-- Scanner ↔ AnyPulse links (migration 0011): consent by a member holding scanner:link, links
-- visible to both sides only, revocation one-way, collection destination checked in the DB.
-- Run as the API role; rolls back everything.

\set QUIET on
\pset tuples_only on
BEGIN;

\set t1 '''71000000-0000-0000-0000-000000000001'''
\set t2 '''72000000-0000-0000-0000-000000000002'''
\set u1 '''a1000000-0000-0000-0000-000000000001'''
\set u2 '''a2000000-0000-0000-0000-000000000002'''
\set u3 '''a3000000-0000-0000-0000-000000000003'''
\set b1 '''b1000000-0000-0000-0000-000000000001'''
\set b2 '''b2000000-0000-0000-0000-000000000002'''
\set sa '''5ca00000-0000-0000-0000-0000000000a1'''
\set sb '''5cb00000-0000-0000-0000-0000000000b1'''
\set sess_a '''5ca00000-0000-0000-0000-00000000a5e5'''
\set sess_b '''5cb00000-0000-0000-0000-00000000b5e5'''

-- AnyPulse seed: T1 (U1 owner, U2 viewer, brand B1), T2 (U3 owner, brand B2)
SELECT set_config('app.scanner_account_id', '', true);
SELECT set_config('app.tenant_id', :t1, true), set_config('app.user_id', :u1, true);
INSERT INTO tenants (id, name, slug) VALUES (:t1, 'Ooredoo DZ', 'ooredoo-dz');
INSERT INTO users (id, email, password_hash, full_name) VALUES (:u1, 'u1@t1.dz', 'x', 'U1');
INSERT INTO memberships (id, tenant_id, user_id, role_id)
  SELECT gen_random_uuid(), :t1, :u1, id FROM roles WHERE key = 'owner';
INSERT INTO brands (id, tenant_id, name) VALUES (:b1, :t1, 'Ooredoo');
SELECT set_config('app.user_id', :u2, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:u2, 'u2@t1.dz', 'x', 'U2');
INSERT INTO memberships (id, tenant_id, user_id, role_id)
  SELECT gen_random_uuid(), :t1, :u2, id FROM roles WHERE key = 'viewer';
SELECT set_config('app.tenant_id', :t2, true), set_config('app.user_id', :u3, true);
INSERT INTO tenants (id, name, slug) VALUES (:t2, 'Mobilis', 'mobilis-x');
INSERT INTO users (id, email, password_hash, full_name) VALUES (:u3, 'u3@t2.dz', 'x', 'U3');
INSERT INTO memberships (id, tenant_id, user_id, role_id)
  SELECT gen_random_uuid(), :t2, :u3, id FROM roles WHERE key = 'owner';
INSERT INTO brands (id, tenant_id, name) VALUES (:b2, :t2, 'Mobilis');

-- Scanner seed: accounts SA, SB, their sessions and link requests
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sa, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name) VALUES (:sa, 'sa@scan.dz', 'x', 'Kenza');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_a, :sa);
INSERT INTO scanner_link_requests (id, account_id, token_hash, expires_at) VALUES
  (gen_random_uuid(), :sa, repeat('a', 64), now() + interval '15 minutes'),
  (gen_random_uuid(), :sa, repeat('c', 64), now() + interval '15 minutes'),
  (gen_random_uuid(), :sa, repeat('e', 64), now() - interval '1 minute');
SELECT set_config('app.scanner_account_id', :sb, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name) VALUES (:sb, 'sb@scan.dz', 'x', 'Amel');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_b, :sb);
INSERT INTO scanner_link_requests (id, account_id, token_hash, expires_at) VALUES
  (gen_random_uuid(), :sb, repeat('b', 64), now() + interval '15 minutes');
SELECT 'PASS seed';

-- 1. nobody inserts a link directly; requests stay private to their account
DO $$ BEGIN
  BEGIN
    INSERT INTO scanner_links (id, account_id, tenant_id, account_email, account_name, tenant_name)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '71000000-0000-0000-0000-000000000001', 'x', 'x', 'x');
    RAISE EXCEPTION 'Scanner context inserted a link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  ASSERT (SELECT count(*) FROM scanner_link_requests) = 1, 'SB sees SA requests';
END $$;
SELECT set_config('app.scanner_account_id', '', true),
       set_config('app.tenant_id', :t1, true), set_config('app.user_id', :u1, true);
DO $$ BEGIN
  BEGIN
    INSERT INTO scanner_links (id, account_id, tenant_id, account_email, account_name, tenant_name)
      VALUES (gen_random_uuid(), '5ca00000-0000-0000-0000-0000000000a1',
              '71000000-0000-0000-0000-000000000001', 'x', 'x', 'x');
    RAISE EXCEPTION 'AnyPulse context inserted a link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  ASSERT (SELECT count(*) FROM scanner_link_requests) = 0, 'AnyPulse context reads requests';
END $$;
SELECT 'PASS 1: links cannot be inserted directly; requests are private';

-- 2. peek: needs an AnyPulse context; shows who asks
DO $$ BEGIN
  ASSERT (SELECT account_email FROM scanner_link_request_peek(repeat('a', 64))) = 'sa@scan.dz',
         'peek failed';
END $$;
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM scanner_link_request_peek(repeat('a', 64))) = 0,
         'peek works without an AnyPulse context';
END $$;
SELECT set_config('app.scanner_account_id', :sb, true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM scanner_link_request_peek(repeat('a', 64))) = 0,
         'a Scanner context can peek at requests';
  BEGIN
    PERFORM scanner_link_approve(repeat('b', 64));
    RAISE EXCEPTION 'a Scanner account approved its own link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 2: requests are read and decided only from an AnyPulse context';

-- 3. a viewer cannot approve or decline
SELECT set_config('app.scanner_account_id', '', true),
       set_config('app.tenant_id', :t1, true), set_config('app.user_id', :u2, true);
DO $$ BEGIN
  BEGIN
    PERFORM scanner_link_approve(repeat('a', 64));
    RAISE EXCEPTION 'viewer approved a link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM scanner_link_decline(repeat('a', 64));
    RAISE EXCEPTION 'viewer declined a link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 3: scanner:link is required to decide';

-- 4. the owner approves into the *current* organization; tokens are single-use; a second
--    approval for the same account and organization reuses the active link; expired refused
SELECT set_config('app.user_id', :u1, true);
SELECT set_config('test.link', scanner_link_approve(repeat('a', 64))::text, true);
DO $$
DECLARE l uuid := current_setting('test.link')::uuid;
BEGIN
  ASSERT (SELECT tenant_id FROM scanner_links WHERE id = l) = '71000000-0000-0000-0000-000000000001',
         'link not in the current organization';
  ASSERT (SELECT tenant_name FROM scanner_links WHERE id = l) = 'Ooredoo DZ', 'no tenant snapshot';
  ASSERT (SELECT account_email FROM scanner_links WHERE id = l) = 'sa@scan.dz', 'no account snapshot';
  BEGIN
    PERFORM scanner_link_approve(repeat('a', 64));
    RAISE EXCEPTION 'token approved twice';
  EXCEPTION WHEN no_data_found THEN NULL; END;
  ASSERT scanner_link_approve(repeat('c', 64)) = l, 'second approval created a duplicate link';
  BEGIN
    PERFORM scanner_link_approve(repeat('e', 64));
    RAISE EXCEPTION 'expired request approved';
  EXCEPTION WHEN no_data_found THEN NULL; END;
  ASSERT scanner_link_decline(repeat('b', 64)), 'decline failed';
  ASSERT NOT scanner_link_decline(repeat('b', 64)), 'declined twice';
  BEGIN
    PERFORM scanner_link_approve(repeat('b', 64));
    RAISE EXCEPTION 'declined request approved';
  EXCEPTION WHEN no_data_found THEN NULL; END;
END $$;
SELECT 'PASS 4: single-use, current organization only, idempotent per pair, expiry and decline';

-- 5. visibility: SA and T1 see the link; SB and T2 do not, and cannot revoke it
SELECT set_config('app.tenant_id', :t2, true), set_config('app.user_id', :u3, true);
DO $$
DECLARE n int;
BEGIN
  ASSERT (SELECT count(*) FROM scanner_links) = 0, 'T2 sees T1 link';
  UPDATE scanner_links SET status = 'revoked', revoked_by = 'anypulse';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'T2 revoked T1 link';
END $$;
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sb, true);
DO $$
DECLARE n int;
BEGIN
  ASSERT (SELECT count(*) FROM scanner_links) = 0, 'SB sees SA link';
  UPDATE scanner_links SET status = 'revoked', revoked_by = 'scanner';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'SB revoked SA link';
END $$;
SELECT set_config('app.scanner_account_id', :sa, true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM scanner_links) = 1, 'SA does not see its link';
  ASSERT (SELECT count(*) FROM tenants) = 0, 'link opened tenants to the Scanner account';
  ASSERT (SELECT count(*) FROM brands) = 0, 'link opened brands to the Scanner account';
  BEGIN
    UPDATE scanner_links SET tenant_id = '72000000-0000-0000-0000-000000000002';
    RAISE EXCEPTION 'link moved to another organization';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 5: a link is visible to its two parties only and cannot be moved';

-- 6. collection destination is checked in the database
DO $$
DECLARE l uuid := current_setting('test.link')::uuid;
BEGIN
  UPDATE scanner_collection_sessions SET link_id = l,
         anypulse_brand_id = 'b1000000-0000-0000-0000-000000000001', anypulse_brand_name = 'Ooredoo'
   WHERE id = '5ca00000-0000-0000-0000-00000000a5e5';
  BEGIN
    UPDATE scanner_collection_sessions SET anypulse_brand_id = 'b2000000-0000-0000-0000-000000000002'
     WHERE id = '5ca00000-0000-0000-0000-00000000a5e5';
    RAISE EXCEPTION 'brand of another organization accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    UPDATE scanner_collection_sessions SET link_id = NULL
     WHERE id = '5ca00000-0000-0000-0000-00000000a5e5';
    RAISE EXCEPTION 'brand without a link accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
SELECT set_config('app.scanner_account_id', :sb, true);
DO $$
DECLARE l uuid := current_setting('test.link')::uuid;
BEGIN
  BEGIN
    UPDATE scanner_collection_sessions SET link_id = l
     WHERE id = '5cb00000-0000-0000-0000-00000000b5e5';
    RAISE EXCEPTION 'another account''s link accepted';
  EXCEPTION WHEN check_violation OR foreign_key_violation THEN NULL; END;
END $$;
SELECT 'PASS 6: destination must be an active own link and a brand of its organization';

-- 7. revocation from AnyPulse: one-way, timestamped; a revoked link is no destination
SELECT set_config('app.scanner_account_id', '', true),
       set_config('app.tenant_id', :t1, true), set_config('app.user_id', :u1, true);
UPDATE scanner_links SET status = 'revoked', revoked_by = 'anypulse';
DO $$ BEGIN
  ASSERT (SELECT revoked_at IS NOT NULL FROM scanner_links), 'revocation not timestamped';
  BEGIN
    UPDATE scanner_links SET status = 'active', revoked_at = NULL, revoked_by = NULL;
    RAISE EXCEPTION 'revoked link re-activated';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sa, true);
DO $$
DECLARE l uuid := current_setting('test.link')::uuid;
BEGIN
  -- the existing destination stays readable (history), but cannot be chosen again
  UPDATE scanner_collection_sessions SET link_id = NULL, anypulse_brand_id = NULL
   WHERE id = '5ca00000-0000-0000-0000-00000000a5e5';
  BEGIN
    UPDATE scanner_collection_sessions SET link_id = l
     WHERE id = '5ca00000-0000-0000-0000-00000000a5e5';
    RAISE EXCEPTION 'revoked link accepted as destination';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
SELECT 'PASS 7: revocation is final and closes the destination';

-- 8. the integration lookup returns the link's organization and owner only
DO $$
DECLARE l uuid := current_setting('test.link')::uuid;
BEGIN
  ASSERT (SELECT tenant_id FROM integration_scanner_link(l)) = '71000000-0000-0000-0000-000000000001',
         'integration lookup failed';
  ASSERT (SELECT status FROM integration_scanner_link(l)) = 'revoked', 'status not reported';
END $$;
SELECT 'PASS 8: integration lookup is narrow';

ROLLBACK;
SELECT 'ALL SCANNER LINK RLS TESTS PASSED';
