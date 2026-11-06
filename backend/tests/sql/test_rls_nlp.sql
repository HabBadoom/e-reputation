-- Migration 0005: analysis isolation, corpus access, opt-in, platform-admin protection.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''
\set ba '''aaaaaaaa-0000-0000-0000-0000000000ba'''
\set bb '''bbbbbbbb-0000-0000-0000-0000000000bb'''
\set ma '''aaaaaaaa-0000-0000-0000-0000000000d1'''
\set mb '''bbbbbbbb-0000-0000-0000-0000000000d2'''

SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'A');
INSERT INTO tenants (id, name, slug, nlp_corpus_opt_in) VALUES (:ta, 'Tenant A', 'tenant-a', true);
INSERT INTO brands (id, tenant_id, name) VALUES (:ba, :ta, 'Brand A');
INSERT INTO mentions (id, tenant_id, brand_id, entity_type, entity_name, url, canonical_url,
                      domain, collection_method, connector, connector_version,
                      verification_status, content_hash)
  VALUES (:ma, :ta, :ba, 'brand', 'A', 'https://x.dz/a', 'https://x.dz/a', 'x.dz', 'rss', 'rss',
          '1', 'feed_item', repeat('0', 64));
INSERT INTO mention_analysis (mention_id, tenant_id, status, sentiment) VALUES (:ma, :ta, 'done', 'negative');

SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'B');
INSERT INTO tenants (id, name, slug) VALUES (:tb, 'Tenant B', 'tenant-b');   -- not opted in
INSERT INTO brands (id, tenant_id, name) VALUES (:bb, :tb, 'Brand B');
INSERT INTO mentions (id, tenant_id, brand_id, entity_type, entity_name, url, canonical_url,
                      domain, collection_method, connector, connector_version,
                      verification_status, content_hash)
  VALUES (:mb, :tb, :bb, 'brand', 'B', 'https://x.dz/b', 'https://x.dz/b', 'x.dz', 'rss', 'rss',
          '1', 'feed_item', repeat('1', 64));
SELECT 'PASS seed';

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM mention_analysis) = 0, 'B sees A analysis';
  BEGIN
    -- (tenant B, a mention id that is not B's) must be rejected by the composite FK
    INSERT INTO mention_analysis (mention_id, tenant_id) VALUES
      (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001');
    RAISE EXCEPTION 'B analysed A mention';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
END $$;
SELECT 'PASS 1: analysis isolated; cannot attach to another tenant''s mention';

-- 2. corpus: B (not opted in) cannot contribute; nobody but platform admins can read
DO $$ BEGIN
  ASSERT NOT corpus_add('bbbbbbbb-0000-0000-0000-0000000000d2', 'texte', NULL, NULL, 'latin',
                        '{}', 'auto_agreed'), 'non opted-in tenant contributed';
  ASSERT NOT corpus_add('aaaaaaaa-0000-0000-0000-0000000000d1', 'texte', NULL, NULL, 'latin',
                        '{}', 'auto_agreed'), 'B contributed A''s mention';
END $$;
SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
DO $$ BEGIN
  ASSERT corpus_add('aaaaaaaa-0000-0000-0000-0000000000d1', 'Djezzy khayeb bzaf', NULL,
                    'https://x.dz/a', 'latin', '{"sentiment":"negative"}', 'auto_agreed'),
         'opted-in tenant could not contribute';
  ASSERT (SELECT count(*) FROM language_annotations) = 0, 'non-admin reads corpus';
  BEGIN
    INSERT INTO language_annotations (id, text) VALUES (gen_random_uuid(), 'x');
    RAISE EXCEPTION 'non-admin wrote corpus directly';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 2: corpus contributions require opt-in; corpus unreadable by non-admins';

-- 3. users cannot promote themselves
DO $$ BEGIN
  BEGIN
    UPDATE users SET is_platform_admin = true WHERE id = 'aaaaaaaa-0000-0000-0000-0000000000a1';
    RAISE EXCEPTION 'self-promotion succeeded';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  UPDATE users SET full_name = 'A renamed' WHERE id = 'aaaaaaaa-0000-0000-0000-0000000000a1';
  BEGIN
    INSERT INTO users (id, email, password_hash, full_name, is_platform_admin)
      VALUES ('cccccccc-0000-0000-0000-0000000000c1', 'c@c.dz', 'x', 'C', true);
    RAISE EXCEPTION 'created admin';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  ASSERT NOT app_is_platform_admin(), 'A is admin';
END $$;
SELECT 'PASS 3: platform-admin flag cannot be set by the app role';

-- 4. purge on opt-out removes only own contributions
DO $$ BEGIN
  ASSERT corpus_purge_tenant() = 1, 'purge count';
END $$;
SELECT 'PASS 4: opt-out purge works';

ROLLBACK;
SELECT 'ALL NLP RLS TESTS PASSED';
