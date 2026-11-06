-- Migration 0004: sources and mentions are tenant-isolated; connector policies read-only.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''
\set ba '''aaaaaaaa-0000-0000-0000-0000000000ba'''
\set bb '''bbbbbbbb-0000-0000-0000-0000000000bb'''
\set sa '''aaaaaaaa-0000-0000-0000-0000000000c1'''

SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'A');
INSERT INTO tenants (id, name, slug) VALUES (:ta, 'Tenant A', 'tenant-a');
INSERT INTO brands (id, tenant_id, name) VALUES (:ba, :ta, 'Brand A');
INSERT INTO sources (id, tenant_id, kind, url, domain, connector)
  VALUES (:sa, :ta, 'rss', 'https://news.example.dz/feed', 'news.example.dz', 'rss');
INSERT INTO mentions (id, tenant_id, brand_id, source_id, entity_type, entity_name, url,
                      canonical_url, domain, collection_method, connector, connector_version,
                      verification_status, content_hash)
  VALUES (gen_random_uuid(), :ta, :ba, :sa, 'brand', 'Brand A', 'https://news.example.dz/a',
          'https://news.example.dz/a', 'news.example.dz', 'rss', 'rss', '1', 'feed_item',
          repeat('0', 64));

SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'B');
INSERT INTO tenants (id, name, slug) VALUES (:tb, 'Tenant B', 'tenant-b');
INSERT INTO brands (id, tenant_id, name) VALUES (:bb, :tb, 'Brand B');
SELECT 'PASS seed';

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM sources) = 0, 'B sees A sources';
  ASSERT (SELECT count(*) FROM mentions) = 0, 'B sees A mentions';
  ASSERT (SELECT count(*) FROM connector_policies) = 3, 'policies not readable';
END $$;
SELECT 'PASS 1: sources and mentions isolated; connector policies readable';

DO $$ BEGIN
  BEGIN
    -- B attaches its mention to A's source: composite FK must fail
    INSERT INTO mentions (id, tenant_id, brand_id, source_id, entity_type, entity_name, url,
                          canonical_url, domain, collection_method, connector, connector_version,
                          verification_status, content_hash)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001',
              'bbbbbbbb-0000-0000-0000-0000000000bb', 'aaaaaaaa-0000-0000-0000-0000000000c1',
              'brand', 'B', 'https://x.dz', 'https://x.dz', 'x.dz', 'rss', 'rss', '1',
              'feed_item', repeat('1', 64));
    RAISE EXCEPTION 'cross-tenant source link';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN
    UPDATE connector_policies SET review_status = 'approved';
    RAISE EXCEPTION 'tenant approved a connector';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 2: no cross-tenant links; tenants cannot approve connectors';

ROLLBACK;
SELECT 'ALL COLLECTION RLS TESTS PASSED';
