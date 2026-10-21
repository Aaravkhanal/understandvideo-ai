BEGIN;

CREATE TABLE IF NOT EXISTS workspace_invitations (
  id text PRIMARY KEY,
  workspace_id text NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  email text NOT NULL,
  role text NOT NULL CHECK (role IN ('viewer', 'editor')),
  token_hash text NOT NULL UNIQUE,
  invited_by text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  created_at bigint NOT NULL,
  expires_at bigint NOT NULL,
  accepted_at bigint
);

CREATE INDEX IF NOT EXISTS idx_workspace_invitations_workspace_created
  ON workspace_invitations(workspace_id, created_at DESC);

INSERT INTO schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING;

COMMIT;
