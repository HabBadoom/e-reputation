-- Scanner AynPulse access (migration 0011): granted at creation whatever the INSERT says,
-- never changed by the API role, changed only through admin_scanner_set_access() by a
-- platform administrator. Run as the API role; rolls back everything.

\set QUIET on
\pset tuples_only on
BEGIN;

\set t1 '''71000000-0000-0000-0000-000000000001'''
\set u1 '''a1000000-0000-0000-0000-000000000001'''
\set sa '''5ca00000-0000-0000-0000-0000000000a1'''

SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sa, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name, anypulse_access,
                              access_revoked_at, access_revoke_reason)
  VALUES (:sa, 'sa@scan.dz', 'x', 'Kenza', 'revoked', now(), 'planted');
DO $$ BEGIN
  ASSERT (SELECT anypulse_access FROM scanner_accounts) = 'active', 'INSERT chose its access';
  ASSERT (SELECT access_revoke_reason FROM scanner_accounts) = '', 'INSERT planted a reason';
END $$;
SELECT 'PASS 1: a new account always starts with access granted';

DO $$ BEGIN
  UPDATE scanner_accounts SET full_name = 'Kenza B.';   -- ordinary profile columns: allowed
  BEGIN
    UPDATE scanner_accounts SET anypulse_access = 'revoked', access_revoked_at = now();
    RAISE EXCEPTION 'API role changed the access';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    UPDATE scanner_accounts SET access_revoke_reason = 'x';
    RAISE EXCEPTION 'API role changed the reason';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM admin_scanner_set_access('5ca00000-0000-0000-0000-0000000000a1', false, 'x');
    RAISE EXCEPTION 'a Scanner account revoked access';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM count(*) FROM admin_scanner_accounts();
    RAISE EXCEPTION 'a Scanner account listed accounts';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 2: the API role and Scanner accounts cannot change access';

-- An AynPulse organization owner (not a platform administrator) cannot either.
SELECT set_config('app.scanner_account_id', '', true),
       set_config('app.tenant_id', :t1, true), set_config('app.user_id', :u1, true);
INSERT INTO tenants (id, name, slug) VALUES (:t1, 'Client', 'client-x');
INSERT INTO users (id, email, password_hash, full_name) VALUES (:u1, 'u1@t1.dz', 'x', 'U1');
INSERT INTO memberships (id, tenant_id, user_id, role_id)
  SELECT gen_random_uuid(), :t1, :u1, id FROM roles WHERE key = 'owner';
DO $$ BEGIN
  BEGIN
    PERFORM admin_scanner_set_access('5ca00000-0000-0000-0000-0000000000a1', false, 'x');
    RAISE EXCEPTION 'organization owner revoked a Scanner account';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  ASSERT (SELECT count(*) FROM scanner_accounts) = 0, 'AynPulse context reads Scanner accounts';
END $$;
SELECT 'PASS 3: only platform administrators change access';

ROLLBACK;
SELECT 'ALL SCANNER ACCESS RLS TESTS PASSED';
