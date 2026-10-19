-- PostgreSQL-oriented production baseline. Apply with your deployment migration tool.
-- The local application remains SQLite-first until DATABASE_URL integration is enabled.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version bigint PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

-- Account/workspace ownership must be created before copying local video records.
-- Media binaries belong in object storage; `path` becomes an object-storage key.
-- IDs remain text-compatible to make local-to-production migration reversible.
INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING;
