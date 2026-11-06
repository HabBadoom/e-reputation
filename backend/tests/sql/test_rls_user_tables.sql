-- User-scoped tables (migration 0002): a user only ever sees and writes their own
-- tokens, MFA secret and recovery codes. Run as the API role; rolls back everything.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''

SELECT set_config('app.user_id', :ua, true), set_config('app.tenant_id', '', true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'A');
INSERT INTO user_tokens (id, user_id, purpose, token_hash, expires_at)
  VALUES (gen_random_uuid(), :ua, 'password_reset', repeat('a', 64), now() + interval '1 hour');
INSERT INTO user_mfa (user_id, secret_enc, enabled_at) VALUES (:ua, 'enc-a', now());
INSERT INTO mfa_recovery_codes (id, user_id, code_hash) VALUES (gen_random_uuid(), :ua, repeat('1', 64));

SELECT set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'B');
INSERT INTO user_tokens (id, user_id, purpose, token_hash, expires_at)
  VALUES (gen_random_uuid(), :ub, 'email_verify', repeat('b', 64), now() + interval '1 day');
SELECT 'PASS seed: rows inserted under each user''s own context';

-- 1. no context → nothing
SELECT set_config('app.user_id', '', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM user_tokens) = 0, 'tokens visible without context';
  ASSERT (SELECT count(*) FROM user_mfa) = 0, 'mfa visible without context';
  ASSERT (SELECT count(*) FROM mfa_recovery_codes) = 0, 'codes visible without context';
END $$;
SELECT 'PASS 1: user tables fail closed without context';

-- 2. B sees only B's rows and cannot read or change A's MFA secret / codes / tokens
SELECT set_config('app.user_id', :ub, true);
DO $$
DECLARE n int;
BEGIN
  ASSERT (SELECT count(*) FROM user_tokens) = 1, 'B sees A tokens';
  ASSERT (SELECT count(*) FROM user_mfa) = 0, 'B sees A MFA secret';
  ASSERT (SELECT count(*) FROM mfa_recovery_codes) = 0, 'B sees A recovery codes';
  UPDATE user_mfa SET secret_enc = NULL, enabled_at = NULL
    WHERE user_id = 'aaaaaaaa-0000-0000-0000-0000000000a1';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B disabled A MFA';
  DELETE FROM mfa_recovery_codes;
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B deleted A recovery codes';
  UPDATE user_tokens SET used_at = now() WHERE purpose = 'password_reset';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B consumed A reset token';
END $$;
SELECT 'PASS 2: another user cannot read, disable or consume A''s MFA or tokens';

-- 3. B cannot plant rows for A (e.g. a known recovery code or MFA secret)
DO $$ BEGIN
  BEGIN
    INSERT INTO mfa_recovery_codes (id, user_id, code_hash)
      VALUES (gen_random_uuid(), 'aaaaaaaa-0000-0000-0000-0000000000a1', repeat('9', 64));
    RAISE EXCEPTION 'planted recovery code for A';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    INSERT INTO user_tokens (id, user_id, purpose, token_hash, expires_at)
      VALUES (gen_random_uuid(), 'aaaaaaaa-0000-0000-0000-0000000000a1', 'password_reset',
              repeat('9', 64), now() + interval '1 hour');
    RAISE EXCEPTION 'planted reset token for A';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 3: cannot create tokens or recovery codes for another user';

-- 4. token lookup function: purpose-bound and narrow
SELECT set_config('app.user_id', '', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM auth_find_user_token(repeat('a', 64), 'password_reset')) = 1,
         'lookup failed';
  ASSERT (SELECT count(*) FROM auth_find_user_token(repeat('a', 64), 'email_verify')) = 0,
         'reset token accepted as email-verify token';
  ASSERT (SELECT count(*) FROM user_tokens) = 0, 'lookup opened the table';
END $$;
SELECT 'PASS 4: token lookup is bound to its purpose and does not open the table';

ROLLBACK;
SELECT 'ALL USER-TABLE RLS TESTS PASSED';
