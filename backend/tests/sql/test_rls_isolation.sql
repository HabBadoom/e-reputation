-- Database-level tenant isolation tests. Run as the API role (repintel_app) against a freshly
-- migrated database:  psql "$APP_URL" -v ON_ERROR_STOP=1 -f tests/sql/test_rls_isolation.sql
-- Every check raises on failure; the script prints PASS lines and ends with ALL RLS TESTS PASSED.
-- Everything runs inside one transaction that is rolled back, so it leaves no data behind.

\set QUIET on
\pset tuples_only on
BEGIN;

-- Fixed IDs for readability.
\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''
\set ba '''aaaaaaaa-0000-0000-0000-0000000000ba'''
\set bb '''bbbbbbbb-0000-0000-0000-0000000000bb'''

-- ---------------------------------------------------------------- seed via the normal path
-- Registration of tenant A (exactly what the API does: context first, then inserts).
SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'User A');
INSERT INTO tenants (id, name, slug) VALUES (:ta, 'Tenant A', 'tenant-a');
INSERT INTO memberships (id, tenant_id, user_id, role_id)
  SELECT gen_random_uuid(), :ta, :ua, id FROM roles WHERE key = 'owner';
INSERT INTO brands (id, tenant_id, name) VALUES (:ba, :ta, 'Brand A');
INSERT INTO audit_logs (id, tenant_id, actor_user_id, action) VALUES (gen_random_uuid(), :ta, :ua, 'seed');

SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'User B');
INSERT INTO tenants (id, name, slug) VALUES (:tb, 'Tenant B', 'tenant-b');
INSERT INTO memberships (id, tenant_id, user_id, role_id)
  SELECT gen_random_uuid(), :tb, :ub, id FROM roles WHERE key = 'owner';
INSERT INTO brands (id, tenant_id, name) VALUES (:bb, :tb, 'Brand B');
SELECT 'PASS seed: both tenants created through RLS-checked inserts';

-- ---------------------------------------------------------------- 1. no context → nothing
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM brands) = 0, 'brands visible without context';
  ASSERT (SELECT count(*) FROM tenants) = 0, 'tenants visible without context';
  ASSERT (SELECT count(*) FROM users) = 0, 'users visible without context';
  ASSERT (SELECT count(*) FROM memberships) = 0, 'memberships visible without context';
  ASSERT (SELECT count(*) FROM audit_logs) = 0, 'audit visible without context';
END $$;
SELECT 'PASS 1: fail-closed with no tenant context';

-- ---------------------------------------------------------------- 2. A sees only A
SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
DO $$ BEGIN
  ASSERT (SELECT array_agg(name) FROM brands) = ARRAY['Brand A']::varchar[], 'A sees wrong brands';
  ASSERT (SELECT array_agg(name) FROM tenants) = ARRAY['Tenant A']::varchar[], 'A sees wrong tenants';
  ASSERT (SELECT array_agg(email) FROM users) = ARRAY['a@a.dz']::varchar[], 'A sees user B';
  ASSERT (SELECT count(*) FROM memberships) = 1, 'A sees B memberships';
  ASSERT (SELECT count(*) FROM brands WHERE id = 'bbbbbbbb-0000-0000-0000-0000000000bb') = 0,
         'A can read B brand by id';
END $$;
SELECT 'PASS 2: tenant A reads only its own rows (incl. direct id lookup)';

-- ---------------------------------------------------------------- 3. A cannot write into B
DO $$ BEGIN
  BEGIN
    INSERT INTO brands (id, tenant_id, name) VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'evil');
    RAISE EXCEPTION 'insert into B succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL; -- new row violates row-level security policy
  END;
  BEGIN
    UPDATE brands SET tenant_id = 'bbbbbbbb-0000-0000-0000-000000000001' WHERE id = 'aaaaaaaa-0000-0000-0000-0000000000ba';
    RAISE EXCEPTION 'moving A brand into B succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  BEGIN
    INSERT INTO memberships (id, tenant_id, user_id, role_id)
      SELECT gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000a1', id FROM roles WHERE key='owner';
    RAISE EXCEPTION 'A joined tenant B';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
SELECT 'PASS 3: cross-tenant INSERT / UPDATE-move / self-join to B all rejected';

-- ---------------------------------------------------------------- 4. A cannot modify B rows
DO $$
DECLARE n int;
BEGIN
  UPDATE brands SET name = 'pwned' WHERE id = 'bbbbbbbb-0000-0000-0000-0000000000bb';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'A updated B brand';
  DELETE FROM brands WHERE id = 'bbbbbbbb-0000-0000-0000-0000000000bb';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'A deleted B brand';
  UPDATE tenants SET name = 'pwned' WHERE id = 'bbbbbbbb-0000-0000-0000-000000000001';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'A renamed tenant B';
  UPDATE users SET full_name = 'pwned' WHERE id = 'bbbbbbbb-0000-0000-0000-0000000000b1';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'A edited user B';
END $$;
SELECT 'PASS 4: UPDATE/DELETE on B rows affect 0 rows';

-- ---------------------------------------------------------------- 5. composite FK blocks cross-tenant links
DO $$ BEGIN
  BEGIN
    -- Alias row claims tenant A (passes RLS) but points at B's brand → composite FK must fail.
    INSERT INTO brand_aliases (id, tenant_id, brand_id, alias, script)
      VALUES (gen_random_uuid(), 'aaaaaaaa-0000-0000-0000-000000000001', 'bbbbbbbb-0000-0000-0000-0000000000bb', 'x', 'latin');
    RAISE EXCEPTION 'cross-tenant alias link succeeded';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
END $$;
SELECT 'PASS 5: composite (tenant_id, brand_id) FK prevents cross-tenant references';

-- ---------------------------------------------------------------- 6. audit log is append-only
DO $$ BEGIN
  BEGIN UPDATE audit_logs SET action = 'tampered'; RAISE EXCEPTION 'audit update allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN DELETE FROM audit_logs; RAISE EXCEPTION 'audit delete allowed';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 6: audit_logs is append-only for the app role';

-- ---------------------------------------------------------------- 7. app role cannot escape RLS
DO $$ BEGIN
  BEGIN ALTER TABLE brands DISABLE ROW LEVEL SECURITY; RAISE EXCEPTION 'disabled RLS';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN DROP POLICY tenant_isolation ON brands; RAISE EXCEPTION 'dropped policy';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN CREATE TABLE sneaky (id int); RAISE EXCEPTION 'created table';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN INSERT INTO roles (id, key, name, rank) VALUES (gen_random_uuid(), 'god', 'God', 999);
    RAISE EXCEPTION 'created role';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN SET LOCAL row_security = off; PERFORM count(*) FROM brands; RAISE EXCEPTION 'row_security off worked';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 7: app role cannot disable RLS, drop policies, create objects, edit RBAC, or turn row_security off';

-- ---------------------------------------------------------------- 8. auth lookup functions are narrow
SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM auth_find_user_by_email('A@A.DZ')) = 1, 'login lookup failed (case-insensitive)';
  ASSERT (SELECT count(*) FROM auth_find_user_by_email('nobody@x.dz')) = 0, 'phantom user';
  ASSERT (SELECT count(*) FROM users) = 0, 'lookup function leaked table access';
END $$;
SELECT 'PASS 8: SECURITY DEFINER login lookup works pre-context without opening the users table';

-- ---------------------------------------------------------------- 9. user switching tenants
-- User A is invited into tenant B (insert done in B context, as the invitation-accept flow does).
SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO memberships (id, tenant_id, user_id, role_id)
  SELECT gen_random_uuid(), :tb, :ua, id FROM roles WHERE key = 'viewer';
SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
DO $$ BEGIN
  -- In A's context, A can list the tenants they belong to (switcher) ...
  ASSERT (SELECT count(*) FROM tenants) = 2, 'tenant switcher list wrong';
  -- ... but still cannot see B's brands or B's other members.
  ASSERT (SELECT count(*) FROM brands) = 1, 'membership in B leaked B brands into A context';
  ASSERT (SELECT count(*) FROM users WHERE email = 'b@b.dz') = 0, 'B members leaked into A context';
END $$;
SELECT 'PASS 9: multi-tenant user sees B in switcher but no B data while in A context';

ROLLBACK;

-- ---------------------------------------------------------------- 10. context is transaction-local
-- Same connection, new transaction: the previous tenant context must be gone.
DO $$ BEGIN
  ASSERT coalesce(current_setting('app.tenant_id', true), '') = '', 'tenant context leaked across transactions';
  ASSERT coalesce(current_setting('app.user_id', true), '') = '', 'user context leaked across transactions';
END $$;
SELECT 'PASS 10: context does not survive the transaction (safe on pooled connections)';

DO $$ BEGIN ASSERT (SELECT count(*) FROM brands) = 0; END $$;
SELECT 'ALL RLS TESTS PASSED';
