BEGIN;

ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS platform text;
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS downloaded_bytes bigint;
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS total_bytes bigint;
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS speed_bps double precision;
ALTER TABLE import_jobs ADD COLUMN IF NOT EXISTS eta_seconds integer;

INSERT INTO schema_migrations(version) VALUES (4) ON CONFLICT DO NOTHING;

COMMIT;
