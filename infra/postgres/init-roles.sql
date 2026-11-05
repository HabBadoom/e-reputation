-- Run once per cluster as a superuser (Docker runs it automatically on first start).
-- Passwords here are LOCAL DEVELOPMENT ONLY. In staging/production create these roles
-- with secrets from your secret manager; never reuse these values.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'repintel_owner') THEN
    CREATE ROLE repintel_owner LOGIN BYPASSRLS PASSWORD 'repintel_owner';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'repintel_app') THEN
    -- The API role: cannot bypass RLS, cannot create objects.
    CREATE ROLE repintel_app LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'repintel_app';
  END IF;
END $$;
