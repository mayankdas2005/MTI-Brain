-- One-time bootstrap for Langfuse's database on mti-brain's shared Postgres.
--
-- The postgres data directory is already initialized, so scripts in
-- /docker-entrypoint-initdb.d/ will NOT fire. Run this file manually:
--
--   docker exec -i postgres psql -U mtibrain -d mtibrain \
--     -v langfuse_password="$LANGFUSE_DB_PASSWORD" \
--     < langfuse/init/01-create-langfuse-db.sql
--
-- After this runs, Langfuse connects via:
--   postgresql://langfuse:<pwd>@postgres:5432/langfuse
-- directly (not through pgbouncer — transaction pooling breaks Langfuse
-- migration advisory locks).

CREATE USER langfuse WITH PASSWORD :'langfuse_password';
CREATE DATABASE langfuse OWNER langfuse;
GRANT ALL PRIVILEGES ON DATABASE langfuse TO langfuse;
