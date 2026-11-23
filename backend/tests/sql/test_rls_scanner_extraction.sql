-- Scanner extraction (migration 0014): extractions and extracted entries isolated by Scanner
-- account; entries never linked (parent, duplicate) or attached across accounts; the API role
-- may only correct author and text. Run as the API role; rolls back everything.

\set QUIET on
\pset tuples_only on
BEGIN;

\set sa '''5ca00000-0000-0000-0000-0000000000a1'''
\set sb '''5cb00000-0000-0000-0000-0000000000b1'''
\set sess_a '''5ca00000-0000-0000-0000-00000000a5e5'''
\set sess_b '''5cb00000-0000-0000-0000-00000000b5e5'''
\set item_a '''5ca00000-0000-0000-0000-00000000a17e'''
\set item_b '''5cb00000-0000-0000-0000-00000000b17e'''
\set ent_a '''5ca00000-0000-0000-0000-0000000e0e0a'''

SELECT set_config('app.tenant_id', '', true), set_config('app.user_id', '', true),
       set_config('app.scanner_account_id', :sa, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name) VALUES (:sa, 'sa@scan.dz', 'x', 'A');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_a, :sa);
INSERT INTO scanner_collection_items (id, account_id, session_id, source_type, position)
  VALUES (:item_a, :sa, :sess_a, 'screenshot', 0);
INSERT INTO scanner_extractions (item_id, account_id, session_id, status) VALUES (:item_a, :sa, :sess_a, 'done');
INSERT INTO scanner_extracted_entries (id, account_id, session_id, item_id, position, kind,
                                       author_name, text, dedup_key)
  VALUES (:ent_a, :sa, :sess_a, :item_a, 0, 'comment', 'Nour', 'salam', repeat('a', 64));
SELECT set_config('app.scanner_account_id', :sb, true);
INSERT INTO scanner_accounts (id, email, password_hash, full_name) VALUES (:sb, 'sb@scan.dz', 'x', 'B');
INSERT INTO scanner_collection_sessions (id, account_id) VALUES (:sess_b, :sb);
INSERT INTO scanner_collection_items (id, account_id, session_id, source_type, position)
  VALUES (:item_b, :sb, :sess_b, 'screenshot', 0);
SELECT 'PASS seed';

DO $$
DECLARE n int;
BEGIN
  ASSERT (SELECT count(*) FROM scanner_extractions) = 0, 'B sees A extraction';
  ASSERT (SELECT count(*) FROM scanner_extracted_entries) = 0, 'B sees A entries';
  UPDATE scanner_extracted_entries SET text = 'x';
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B edited A entry';
  DELETE FROM scanner_extracted_entries;
  GET DIAGNOSTICS n = ROW_COUNT; ASSERT n = 0, 'B deleted A entry';
  BEGIN   -- B's entry on A's item
    INSERT INTO scanner_extracted_entries (id, account_id, session_id, item_id, position, kind, dedup_key)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '5cb00000-0000-0000-0000-00000000b5e5', '5ca00000-0000-0000-0000-00000000a17e',
              7, 'comment', repeat('b', 64));
    RAISE EXCEPTION 'entry attached to another account''s item';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN   -- B's reply whose parent is A's comment
    INSERT INTO scanner_extracted_entries (id, account_id, session_id, item_id, position, kind,
                                           parent_id, dedup_key)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '5cb00000-0000-0000-0000-00000000b5e5', '5cb00000-0000-0000-0000-00000000b17e',
              0, 'reply', '5ca00000-0000-0000-0000-0000000e0e0a', repeat('b', 64));
    RAISE EXCEPTION 'reply linked to another account''s comment';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN   -- B's entry marked duplicate of A's
    INSERT INTO scanner_extracted_entries (id, account_id, session_id, item_id, position, kind,
                                           duplicate_of, dedup_key)
      VALUES (gen_random_uuid(), '5cb00000-0000-0000-0000-0000000000b1',
              '5cb00000-0000-0000-0000-00000000b5e5', '5cb00000-0000-0000-0000-00000000b17e',
              0, 'comment', '5ca00000-0000-0000-0000-0000000e0e0a', repeat('b', 64));
    RAISE EXCEPTION 'duplicate link crossed accounts';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN   -- an extraction planted for A
    INSERT INTO scanner_extractions (item_id, account_id, session_id)
      VALUES ('5ca00000-0000-0000-0000-00000000a17e', '5ca00000-0000-0000-0000-0000000000a1',
              '5ca00000-0000-0000-0000-00000000a5e5');
    RAISE EXCEPTION 'extraction planted for another account';
  EXCEPTION WHEN insufficient_privilege OR unique_violation THEN NULL; END;
END $$;
SELECT 'PASS 1: extractions and entries are private to their account';

SELECT set_config('app.scanner_account_id', :sa, true);
DO $$
BEGIN
  UPDATE scanner_extracted_entries SET text = 'salam alikoum', author_name = 'Nour C.',
                                       edited_by_agent = true;
  ASSERT (SELECT text FROM scanner_extracted_entries) = 'salam alikoum', 'own correction lost';
  BEGIN
    UPDATE scanner_extracted_entries SET kind = 'post';
    RAISE EXCEPTION 'API role changed the entry kind';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    UPDATE scanner_extracted_entries SET published_at = now();
    RAISE EXCEPTION 'API role changed the extracted date';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 2: only author and text are correctable';

ROLLBACK;
SELECT 'ALL SCANNER EXTRACTION RLS TESTS PASSED';
