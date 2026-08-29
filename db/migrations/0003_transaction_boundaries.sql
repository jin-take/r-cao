-- R-CAO migration 0003: transactional command boundaries
-- Adds optimistic locking, command idempotency, and an MVP-compatible outbox.
-- Applied migrations must never be edited; add a new forward migration instead.

ALTER TABLE mvp_tasks
  ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

-- MVP audit IDs are application correlation IDs (for example,
-- audit-transition-001), not PostgreSQL UUIDs. Existing UUID values remain
-- losslessly representable as text during this forward migration.
ALTER TABLE mvp_audit_logs
  ALTER COLUMN id TYPE TEXT USING id::text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'mvp_tasks_version_positive'
  ) THEN
    ALTER TABLE mvp_tasks
      ADD CONSTRAINT mvp_tasks_version_positive CHECK (version > 0);
  END IF;
END
$$;

CREATE TABLE IF NOT EXISTS mvp_command_idempotency (
  idempotency_key TEXT PRIMARY KEY,
  command_name TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  response JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  CHECK ((response IS NULL) = (completed_at IS NULL))
);

CREATE TABLE IF NOT EXISTS mvp_outbox_events (
  id TEXT PRIMARY KEY,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mvp_outbox_pending_idx ON mvp_outbox_events(created_at)
  WHERE published_at IS NULL;
