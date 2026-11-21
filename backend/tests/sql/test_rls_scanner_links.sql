-- Scanner ↔ AnyPulse links (migration 0011): created only in the provisioning context for a
-- brand-new organization, one per account and per organization, visible to both parties only,
-- status changed only by platform administrators, collection destination checked in the DB.
-- Run as the API role; rolls back everything.

\set QUIET on
\pset tuples_only on
BEGIN;

\set t1 '''71000000-0000-0000-0000-000000000001'''
\set tn '''7a000000-0000-0000-0000-00000000000a'''
\set tm '''7b000000-0000-0000-0000-00000000000b'''
\set u1 '''a1000000-0000-0000-0000-000000000001'''
\set b1 '''b1000000-0000-0000-0000-000000000001'''
\set bn '''ba000000-0000-0000-0000-00000000000a'''
\set sa '''5ca00000-0000-0000-0000-0000000000a1'''
\set sb '''5cb00000-0000-0000-0000-0000000000b1'''
\set la '''1a000000-0000-0000-0000-0000000000a1'''
\set sess_a '''5ca00000-0000-0000-0000-00000000a5e5'''
\set sess_b '''5cb00000-0000-0000-0000-00000000b5e5'''

-- An existing customer organization T1 (owner U1, brand B1)
SELECT set_config('app.scanner_account_id', '', true);
SELECT set_config('app.tenant_id', :t1, true), set_config('app.user_id', :u1, true);
INSERT INTO tenants (id, name, slug) VALUES (:t1, 'Client existant', 'client-existant');
INSERT INTO users (id, email, password_hash, full_name) VALUES (:u1, 'u1@t1.dz', 'x', 'U1');
INSERT INTO memberships (id, tenant_id, user_id, role_id)
  SELECT gen_random_uuid(), :t1, :u1, id FROM roles WHERE key = 'owner';
INSERT INTO brands (id, tenant_id, name) VALUES (:b1, :t1, 'Ooredoo');

-- Scanner accounts SA and SB with their sessions
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sa, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name) VALUES (:sa, 'sa@scan.dz', 'x', 'Kenza');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_a, :sa);
SELECT set_config('app.scanner_account_id', :sb, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name) VALUES (:sb, 'sb@scan.dz', 'x', 'Amel');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_b, :sb);
SELECT 'PASS seed';

-- 1. provisioning: new organization TN created, then the link, with both contexts set
SELECT set_config('app.scanner_account_id', '', true), set_config('app.tenant_id', :tn, true);
INSERT INTO tenants (id, name, slug) VALUES (:tn, 'Kenza', 'kenza-abc123');
INSERT INTO brands (id, tenant_id, name) VALUES (:bn, :tn, 'Djezzy');
SELECT set_config('app.scanner_account_id', :sa, true);
INSERT INTO scanner_links (id, account_id, tenant_id, tenant_name) VALUES (:la, :sa, :tn, 'Kenza');
DO $$ BEGIN
  ASSERT (SELECT status FROM scanner_links) = 'active', 'link not active at creation';
END $$;
SELECT 'PASS 1: link created in the provisioning context for the new organization';

-- 2. never to an existing customer organization, even with both contexts set
SELECT set_config('app.scanner_account_id', :sb, true), set_config('app.tenant_id', :t1, true);
DO $$ BEGIN
  BEGIN
    INSERT INTO scanner_links (id, account_id, tenant_id, tenant_name)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '71000000-0000-0000-0000-000000000001', 'Client existant');
    RAISE EXCEPTION 'Scanner account linked to an existing organization';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
-- nor with only one of the two contexts
SELECT set_config('app.tenant_id', '', true);
DO $$ BEGIN
  BEGIN
    INSERT INTO scanner_links (id, account_id, tenant_id, tenant_name)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '7a000000-0000-0000-0000-00000000000a', 'Kenza');
    RAISE EXCEPTION 'link inserted from a Scanner-only context';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
-- one organization per account, one account per organization
SELECT set_config('app.scanner_account_id', '', true), set_config('app.tenant_id', :tm, true);
INSERT INTO tenants (id, name, slug) VALUES (:tm, 'Second', 'second-ghi789');
SELECT set_config('app.scanner_account_id', :sa, true);
DO $$ BEGIN
  BEGIN
    INSERT INTO scanner_links (id, account_id, tenant_id, tenant_name)
      VALUES (gen_random_uuid(), '5ca00000-0000-0000-0000-0000000000a1',
              '7b000000-0000-0000-0000-00000000000b', 'Second');
    RAISE EXCEPTION 'second organization for one account';
  EXCEPTION WHEN unique_violation THEN NULL; END;
END $$;
SELECT set_config('app.scanner_account_id', :sb, true), set_config('app.tenant_id', :tn, true);
DO $$ BEGIN
  BEGIN
    INSERT INTO scanner_links (id, account_id, tenant_id, tenant_name)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '7a000000-0000-0000-0000-00000000000a', 'Kenza');
    RAISE EXCEPTION 'second account for one organization';
  EXCEPTION WHEN unique_violation THEN NULL; END;
END $$;
SELECT 'PASS 2: only the brand-new organization, one per account and per organization';

-- 3. visibility: SA and TN see the link; SB and T1 do not
SELECT set_config('app.tenant_id', '', true);
DO $$ BEGIN ASSERT (SELECT count(*) FROM scanner_links) = 0, 'SB sees SA link'; END $$;
SELECT set_config('app.scanner_account_id', '', true), set_config('app.tenant_id', :t1, true),
       set_config('app.user_id', :u1, true);
DO $$ BEGIN ASSERT (SELECT count(*) FROM scanner_links) = 0, 'T1 sees SA link'; END $$;
SELECT set_config('app.tenant_id', :tn, true), set_config('app.user_id', '', true);
DO $$ BEGIN ASSERT (SELECT count(*) FROM scanner_links) = 1, 'TN does not see its link'; END $$;
SELECT set_config('app.tenant_id', '', true), set_config('app.scanner_account_id', :sa, true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM scanner_links) = 1, 'SA does not see its link';
  ASSERT (SELECT count(*) FROM tenants) = 0, 'link opened tenants to the Scanner account';
  ASSERT (SELECT count(*) FROM brands) = 0, 'link opened brands to the Scanner account';
END $$;
SELECT 'PASS 3: a link is visible to its two parties only and opens no AnyPulse data';

-- 4. nobody but a platform administrator changes a link
DO $$ BEGIN
  BEGIN
    UPDATE scanner_links SET status = 'revoked', revoked_at = now();
    RAISE EXCEPTION 'Scanner account changed its link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    DELETE FROM scanner_links;
    RAISE EXCEPTION 'Scanner account deleted its link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM admin_scanner_set_access('5ca00000-0000-0000-0000-0000000000a1', false, 'x');
    RAISE EXCEPTION 'non-admin revoked a link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM count(*) FROM admin_scanner_accounts();
    RAISE EXCEPTION 'non-admin listed Scanner accounts';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT set_config('app.scanner_account_id', '', true), set_config('app.tenant_id', :t1, true),
       set_config('app.user_id', :u1, true);
DO $$ BEGIN
  BEGIN
    PERFORM admin_scanner_set_access('5ca00000-0000-0000-0000-0000000000a1', false, 'x');
    RAISE EXCEPTION 'organization owner (not platform admin) revoked a link';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 4: only platform administrators change a link';

-- 5. collection destination is checked in the database
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sa, true);
UPDATE scanner_collection_sessions SET link_id = :la, anypulse_brand_id = :bn,
       anypulse_brand_name = 'Djezzy' WHERE id = :sess_a;
DO $$ BEGIN
  BEGIN
    UPDATE scanner_collection_sessions SET anypulse_brand_id = 'b1000000-0000-0000-0000-000000000001'
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
DO $$ BEGIN
  BEGIN
    UPDATE scanner_collection_sessions SET link_id = '1a000000-0000-0000-0000-0000000000a1'
     WHERE id = '5cb00000-0000-0000-0000-00000000b5e5';
    RAISE EXCEPTION 'another account''s link accepted';
  EXCEPTION WHEN check_violation OR foreign_key_violation THEN NULL; END;
END $$;
SELECT 'PASS 5: destination = own active link and a brand of its organization';

-- 6. integration lookups are narrow
DO $$ BEGIN
  ASSERT (SELECT tenant_id FROM integration_scanner_link('1a000000-0000-0000-0000-0000000000a1'))
         = '7a000000-0000-0000-0000-00000000000a', 'link lookup failed';
  ASSERT (SELECT link_id FROM integration_scanner_account('5ca00000-0000-0000-0000-0000000000a1'))
         = '1a000000-0000-0000-0000-0000000000a1', 'account lookup failed';
  ASSERT (SELECT link_id FROM integration_scanner_account('5cb00000-0000-0000-0000-0000000000b1'))
         IS NULL, 'unlinked account reported a link';
END $$;
SELECT 'PASS 6: integration lookups';

ROLLBACK;
SELECT 'ALL SCANNER LINK RLS TESTS PASSED';
