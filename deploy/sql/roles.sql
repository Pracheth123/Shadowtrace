-- Least-privilege roles for the report index. Run ONCE, from the app host, as
-- the RDS master user (credentials from the RDS-managed secret; never stored
-- on disk). Passwords are set interactively with \password, never in this file:
--
--   psql "host=<rds-endpoint> port=5432 dbname=shadowtrace user=st_admin \
--         sslmode=verify-full sslrootcert=/etc/shadowtrace/rds-ca.pem" -f deploy/sql/roles.sql
--   \password shadowtrace_migrator
--   \password shadowtrace_app
--
-- shadowtrace_migrator owns the schema objects and runs migrations, restores
-- (TRUNCATE + COPY) and imports. shadowtrace_app can only read and write rows.
\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shadowtrace_migrator') THEN
    CREATE ROLE shadowtrace_migrator LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shadowtrace_app') THEN
    CREATE ROLE shadowtrace_app LOGIN;
  END IF;
END
$$;

REVOKE ALL ON DATABASE shadowtrace FROM PUBLIC;
GRANT CONNECT ON DATABASE shadowtrace TO shadowtrace_migrator, shadowtrace_app;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO shadowtrace_migrator;
GRANT USAGE ON SCHEMA public TO shadowtrace_app;

-- Tables the migrator creates are usable (rows only) by the app role.
ALTER DEFAULT PRIVILEGES FOR ROLE shadowtrace_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO shadowtrace_app;

-- Bounded sessions for both roles.
ALTER ROLE shadowtrace_app SET statement_timeout = '5s';
ALTER ROLE shadowtrace_app SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE shadowtrace_migrator SET idle_in_transaction_session_timeout = '5min';
