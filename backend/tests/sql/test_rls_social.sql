-- Migration 0019: social sources, provider syncs and threaded mentions stay inside the tenant.

\set QUIET on
\pset tuples_only on
BEGIN;

\set ta '''aaaaaaaa-0000-0000-0000-000000000001'''
\set tb '''bbbbbbbb-0000-0000-0000-000000000001'''
\set ua '''aaaaaaaa-0000-0000-0000-0000000000a1'''
\set ub '''bbbbbbbb-0000-0000-0000-0000000000b1'''
\set ba '''aaaaaaaa-0000-0000-0000-0000000000ba'''
\set bb '''bbbbbbbb-0000-0000-0000-0000000000bb'''
\set pa '''aaaaaaaa-0000-0000-0000-0000000000d1'''
\set sa '''aaaaaaaa-0000-0000-0000-0000000000c1'''
\set ma '''aaaaaaaa-0000-0000-0000-0000000000e1'''

SELECT set_config('app.tenant_id', :ta, true), set_config('app.user_id', :ua, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ua, 'a@a.dz', 'x', 'A');
INSERT INTO tenants (id, name, slug) VALUES (:ta, 'Tenant A', 'tenant-a');
INSERT INTO subscriptions (id, tenant_id, plan_id, status)
  SELECT gen_random_uuid(), :ta, id, 'trialing' FROM plans WHERE key = 'trial';
INSERT INTO brands (id, tenant_id, name) VALUES (:ba, :ta, 'Brand A');
INSERT INTO monitoring_projects (id, tenant_id, brand_id, name, frequency_minutes)
  VALUES (:pa, :ta, :ba, 'Veille A', 1440);
INSERT INTO sources (id, tenant_id, kind, url, domain, connector, project_id, next_sync_at)
  VALUES (:sa, :ta, 'facebook_group', 'https://www.facebook.com/groups/123', 'facebook.com',
          'apify', :pa, now() - interval '1 minute');
INSERT INTO source_syncs (tenant_id, source_id, provider) VALUES (:ta, :sa, 'apify');
INSERT INTO mentions (id, tenant_id, brand_id, project_id, source_id, entity_type, entity_name,
                      url, canonical_url, domain, collection_method, connector,
                      connector_version, verification_status, content_hash, platform,
                      external_id, content_type)
  VALUES (:ma, :ta, :ba, :pa, :sa, 'brand', 'Brand A', 'https://www.facebook.com/groups/123/posts/9',
          'https://www.facebook.com/groups/123/posts/9', 'facebook.com', 'third_party_provider',
          'apify', '1', 'provider_record', repeat('0', 64), 'facebook', '9', 'post');
SELECT 'PASS seed';

DO $$ BEGIN
  -- A social source needs a project; a website must not have one.
  BEGIN
    INSERT INTO sources (id, tenant_id, kind, url, domain, connector)
      VALUES (gen_random_uuid(), 'aaaaaaaa-0000-0000-0000-000000000001', 'tiktok_hashtag',
              'https://www.tiktok.com/tag/x', 'tiktok.com', 'apify');
    RAISE EXCEPTION 'social source without project accepted';
  EXCEPTION WHEN check_violation THEN NULL; END;
  -- The same external item cannot be stored twice for the same brand.
  BEGIN
    INSERT INTO mentions (id, tenant_id, brand_id, entity_type, entity_name, url, canonical_url,
                          domain, collection_method, connector, connector_version,
                          verification_status, content_hash, platform, external_id)
      VALUES (gen_random_uuid(), 'aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000ba',
              'brand', 'Brand A', 'https://fb.example/other', 'https://fb.example/other',
              'facebook.com', 'third_party_provider', 'apify', '1', 'provider_record',
              repeat('1', 64), 'facebook', '9');
    RAISE EXCEPTION 'duplicate external item accepted';
  EXCEPTION WHEN unique_violation THEN NULL; END;
END $$;
SELECT 'PASS 1: social sources need a project; external items are stored once per brand';

SELECT set_config('app.tenant_id', :tb, true), set_config('app.user_id', :ub, true);
INSERT INTO users (id, email, password_hash, full_name) VALUES (:ub, 'b@b.dz', 'x', 'B');
INSERT INTO tenants (id, name, slug) VALUES (:tb, 'Tenant B', 'tenant-b');
INSERT INTO brands (id, tenant_id, name) VALUES (:bb, :tb, 'Brand B');

DO $$ BEGIN
  ASSERT (SELECT count(*) FROM sources) = 0, 'B sees A social sources';
  ASSERT (SELECT count(*) FROM source_syncs) = 0, 'B sees A syncs';
  ASSERT (SELECT count(*) FROM mentions) = 0, 'B sees A mentions';
  ASSERT (SELECT count(*) FROM connector_policies WHERE key IN ('apify', 'youtube')
          AND review_status = 'pending_legal_review') = 2, 'policies missing or pre-approved';
  BEGIN
    INSERT INTO source_syncs (tenant_id, source_id, provider)
      VALUES ('bbbbbbbb-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000c1',
              'apify');
    RAISE EXCEPTION 'B attached a sync to A source';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN
    INSERT INTO mentions (id, tenant_id, brand_id, entity_type, entity_name, url, canonical_url,
                          domain, collection_method, connector, connector_version,
                          verification_status, content_hash, parent_mention_id)
      VALUES (gen_random_uuid(), 'bbbbbbbb-0000-0000-0000-000000000001', 'bbbbbbbb-0000-0000-0000-0000000000bb',
              'brand', 'Brand B', 'https://x.dz/c', 'https://x.dz/c', 'x.dz',
              'third_party_provider', 'apify', '1', 'provider_record', repeat('2', 64),
              'aaaaaaaa-0000-0000-0000-0000000000e1');
    RAISE EXCEPTION 'B threaded its comment under A post';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;
  BEGIN
    INSERT INTO source_syncs (tenant_id, source_id, provider)
      VALUES ('aaaaaaaa-0000-0000-0000-000000000001', 'aaaaaaaa-0000-0000-0000-0000000000c1',
              'apify');
    RAISE EXCEPTION 'B wrote a sync for A';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SELECT 'PASS 2: syncs and threads cannot cross tenants';

DO $$ BEGIN
  -- Scheduling is cross-tenant (definer) and enqueues each due source once.
  PERFORM set_config('app.tenant_id', '', true);
  ASSERT schedule_social_syncs(1440) = 1, 'due source not scheduled';
  ASSERT schedule_social_syncs(1440) = 0, 'source scheduled twice';
END $$;
SELECT 'PASS 3: due social sources are scheduled once';
ROLLBACK;
SELECT 'ALL SOCIAL RLS TESTS PASSED';
