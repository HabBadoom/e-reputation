-- Scanner uploaded files (migration 0012): isolated by Scanner account, cannot be attached to
-- another account's item or collection, exact duplicates refused within a collection, metadata
-- never rewritten by the API role. Run as the API role; rolls back everything.

\set QUIET on
\pset tuples_only on
BEGIN;

\set sa '''5ca00000-0000-0000-0000-0000000000a1'''
\set sb '''5cb00000-0000-0000-0000-0000000000b1'''
\set sess_a '''5ca00000-0000-0000-0000-00000000a5e5'''
\set sess_b '''5cb00000-0000-0000-0000-00000000b5e5'''
\set item_a '''5ca00000-0000-0000-0000-00000000a17e'''
\set item_b '''5cb00000-0000-0000-0000-00000000b17e'''

SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sa, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name) VALUES (:sa, 'sa@scan.dz', 'x', 'A');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_a, :sa);
INSERT INTO scanner_collection_items (id, account_id, session_id, source_type, position)
  VALUES (:item_a, :sa, :sess_a, 'screenshot', 0);
INSERT INTO scanner_uploaded_files (id, account_id, session_id, item_id, storage_key, mime_type,
                                    size_bytes, width, height, sha256, dhash)
  VALUES (gen_random_uuid(), :sa, :sess_a, :item_a, 'scanner/a/1.png', 'image/png', 10, 1, 1,
          repeat('a', 64), 42);
SELECT set_config('app.scanner_account_id', :sb, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name) VALUES (:sb, 'sb@scan.dz', 'x', 'B');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_b, :sb);
INSERT INTO scanner_collection_items (id, account_id, session_id, source_type, position)
  VALUES (:item_b, :sb, :sess_b, 'screenshot', 0);
SELECT 'PASS seed';

DO $$
DECLARE n int;
BEGIN
  ASSERT (SELECT count(*) FROM scanner_uploaded_files) = 0, 'B sees A files';
  DELETE FROM scanner_uploaded_files;
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B deleted A files';
  BEGIN   -- B's file on A's item
    INSERT INTO scanner_uploaded_files (id, account_id, session_id, item_id, storage_key,
        mime_type, size_bytes, width, height, sha256, dhash)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '5cb00000-0000-0000-0000-00000000b5e5', '5ca00000-0000-0000-0000-00000000a17e',
              'scanner/b/x.png', 'image/png', 10, 1, 1, repeat('b', 64), 1);
    RAISE EXCEPTION 'file attached to another account''s item';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN   -- a file planted for A
    INSERT INTO scanner_uploaded_files (id, account_id, session_id, item_id, storage_key,
        mime_type, size_bytes, width, height, sha256, dhash)
      VALUES (gen_random_uuid(), '5ca00000-0000-0000-0000-0000000000a1',
              '5ca00000-0000-0000-0000-00000000a5e5', '5ca00000-0000-0000-0000-00000000a17e',
              'scanner/a/2.png', 'image/png', 10, 1, 1, repeat('c', 64), 1);
    RAISE EXCEPTION 'file planted for another account';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN   -- duplicate flag pointing at another account's item
    UPDATE scanner_collection_items SET possible_duplicate_of = '5ca00000-0000-0000-0000-00000000a17e'
     WHERE id = '5cb00000-0000-0000-0000-00000000b17e';
    RAISE EXCEPTION 'duplicate flag crossed accounts';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
END $$;
SELECT 'PASS 1: files are isolated and cannot cross accounts';

SELECT set_config('app.scanner_account_id', :sa, true);
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM scanner_uploaded_files) = 1, 'A does not see its file';
  BEGIN
    INSERT INTO scanner_collection_items (id, account_id, session_id, source_type, position)
      VALUES ('5ca00000-0000-0000-0000-00000000a17f', '5ca00000-0000-0000-0000-0000000000a1',
              '5ca00000-0000-0000-0000-00000000a5e5', 'screenshot', 1);
    INSERT INTO scanner_uploaded_files (id, account_id, session_id, item_id, storage_key,
        mime_type, size_bytes, width, height, sha256, dhash)
      VALUES (gen_random_uuid(), '5ca00000-0000-0000-0000-0000000000a1',
              '5ca00000-0000-0000-0000-00000000a5e5', '5ca00000-0000-0000-0000-00000000a17f',
              'scanner/a/3.png', 'image/png', 10, 1, 1, repeat('a', 64), 42);
    RAISE EXCEPTION 'exact duplicate accepted in the same collection';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  BEGIN
    UPDATE scanner_uploaded_files SET storage_key = 'scanner/b/stolen.png';
    RAISE EXCEPTION 'file metadata rewritten';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    INSERT INTO scanner_uploaded_files (id, account_id, session_id, item_id, storage_key,
        mime_type, size_bytes, width, height, sha256, dhash)
      VALUES (gen_random_uuid(), '5ca00000-0000-0000-0000-0000000000a1',
              '5ca00000-0000-0000-0000-00000000a5e5', '5ca00000-0000-0000-0000-00000000a17e',
              'scanner/a/4.svg', 'image/svg+xml', 10, 1, 1, repeat('d', 64), 1);
    RAISE EXCEPTION 'SVG accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
SELECT 'PASS 2: exact duplicates refused per collection; metadata immutable; images only';

ROLLBACK;
SELECT 'ALL SCANNER UPLOAD RLS TESTS PASSED';
