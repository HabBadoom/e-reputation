-- Scanner tables (migration 0010): isolation by Scanner account, and separation from AnyPulse
-- contexts. Run as the API role; rolls back everything.

\set QUIET on
\pset tuples_only on
BEGIN;

\set sa '''5ca00000-0000-0000-0000-0000000000a1'''
\set sb '''5cb00000-0000-0000-0000-0000000000b1'''
\set sess_a '''5ca00000-0000-0000-0000-00000000a5e5'''
\set sess_b '''5cb00000-0000-0000-0000-00000000b5e5'''

-- seed: each account writes its own rows under its own context
SELECT set_config('app.scanner_account_id', :sa, true), set_config('app.tenant_id', '', true),
       set_config('app.user_id', '', true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name)
  VALUES (:sa, 'a@scanner.dz', 'x', 'A');
INSERT INTO scanner_refresh_tokens (id, account_id, token_hash, family_id, expires_at)
  VALUES (gen_random_uuid(), :sa, repeat('a', 64), gen_random_uuid(), now() + interval '1 day');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_a, :sa);
INSERT INTO scanner_collection_items (id, account_id, session_id, source_type, position, source_url,
                                      url_key)
  VALUES (gen_random_uuid(), :sa, :sess_a, 'url', 0, 'https://www.facebook.com/share/p/x/',
          repeat('a', 64));
INSERT INTO scanner_audit_logs (id, account_id, action) VALUES (gen_random_uuid(), :sa, 'auth.login');

SELECT set_config('app.scanner_account_id', :sb, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name)
  VALUES (:sb, 'b@scanner.dz', 'x', 'B');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_b, :sb);
SELECT 'PASS seed: rows inserted under each account''s own context';

-- 1. no Scanner context → nothing visible, even with an AnyPulse tenant/user context set
SELECT set_config('app.scanner_account_id', '', true),
       set_config('app.tenant_id', '11111111-1111-1111-1111-111111111111', true),
       set_config('app.user_id', '5ca00000-0000-0000-0000-0000000000a1', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM scanner_accounts) = 0, 'accounts visible without Scanner context';
  ASSERT (SELECT count(*) FROM scanner_refresh_tokens) = 0, 'tokens visible without context';
  ASSERT (SELECT count(*) FROM scanner_collection_sessions) = 0, 'sessions visible';
  ASSERT (SELECT count(*) FROM scanner_collection_items) = 0, 'items visible';
  ASSERT (SELECT count(*) FROM scanner_audit_logs) = 0, 'audit visible';
END $$;
SELECT 'PASS 1: Scanner tables fail closed; an AnyPulse context (even a matching uuid) sees nothing';

-- 2. a Scanner context sees nothing of AnyPulse's tenant tables
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sa, true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM tenants) = 0, 'Scanner context sees tenants';
  ASSERT (SELECT count(*) FROM users) = 0, 'Scanner context sees users';
  ASSERT (SELECT count(*) FROM mentions) = 0, 'Scanner context sees mentions';
END $$;
SELECT 'PASS 2: a Scanner context cannot read AnyPulse tables';

-- 3. B sees only B, and cannot read, change or delete A's rows
SELECT set_config('app.scanner_account_id', :sb, true);
DO $$
DECLARE n int;
BEGIN
  ASSERT (SELECT count(*) FROM scanner_accounts) = 1, 'B sees other accounts';
  ASSERT (SELECT count(*) FROM scanner_collection_sessions) = 1, 'B sees A sessions';
  ASSERT (SELECT count(*) FROM scanner_collection_items) = 0, 'B sees A items';
  ASSERT (SELECT count(*) FROM scanner_refresh_tokens) = 0, 'B sees A tokens';
  ASSERT (SELECT count(*) FROM scanner_audit_logs) = 0, 'B sees A audit';
  UPDATE scanner_accounts SET status = 'disabled' WHERE email = 'a@scanner.dz';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B disabled A';
  UPDATE scanner_collection_sessions SET status = 'cancelled'
    WHERE id = '5ca00000-0000-0000-0000-00000000a5e5';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B cancelled A session';
  DELETE FROM scanner_collection_items;
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B deleted A items';
  UPDATE scanner_refresh_tokens SET revoked_at = now();
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B revoked A tokens';
END $$;
SELECT 'PASS 3: another account cannot read, change or delete A''s rows';

-- 4. B cannot plant rows for A, nor attach an item to A's session
DO $$ BEGIN
  BEGIN
    INSERT INTO scanner_collection_sessions (id, account_id)
      VALUES (gen_random_uuid(), '5ca00000-0000-0000-0000-0000000000a1');
    RAISE EXCEPTION 'planted a session for A';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    INSERT INTO scanner_collection_items (id, account_id, session_id, source_type, position)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '5ca00000-0000-0000-0000-00000000a5e5', 'screenshot', 0);
    RAISE EXCEPTION 'attached an item to A session';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN
    INSERT INTO scanner_refresh_tokens (id, account_id, token_hash, family_id, expires_at)
      VALUES (gen_random_uuid(), '5ca00000-0000-0000-0000-0000000000a1', repeat('9', 64),
              gen_random_uuid(), now() + interval '1 day');
    RAISE EXCEPTION 'planted a refresh token for A';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 4: cannot create rows for another account or reuse its session';

-- 5. one open session per account; audit log is append-only
DO $$ BEGIN
  BEGIN
    INSERT INTO scanner_collection_sessions (id, account_id, status)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1', 'collecting');
    RAISE EXCEPTION 'second open session accepted';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  INSERT INTO scanner_audit_logs (id, account_id, action)
    VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1', 'session.created');
  BEGIN
    UPDATE scanner_audit_logs SET action = 'x';
    RAISE EXCEPTION 'audit log updated';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    DELETE FROM scanner_audit_logs;
    RAISE EXCEPTION 'audit log deleted';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 5: one open session per account; audit log is append-only';

-- 6. pre-context lookups return only what login/refresh need and do not open the tables
SELECT set_config('app.scanner_account_id', '', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM scanner_auth_find_account_by_email('A@Scanner.dz')) = 1,
         'email lookup failed';
  ASSERT (SELECT count(*) FROM scanner_auth_find_refresh_token(repeat('a', 64))) = 1,
         'token lookup failed';
  ASSERT (SELECT count(*) FROM scanner_accounts) = 0, 'lookup opened the table';
  -- AnyPulse's own lookups do not see Scanner accounts
  ASSERT (SELECT count(*) FROM auth_find_user_by_email('a@scanner.dz')) = 0,
         'Scanner account usable as an AnyPulse login';
END $$;
SELECT 'PASS 6: lookups are narrow, and a Scanner account is not an AnyPulse login';

ROLLBACK;
SELECT 'ALL SCANNER RLS TESTS PASSED';
