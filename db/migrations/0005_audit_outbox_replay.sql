-- R-CAO migration 0005: versioned Audit/Outbox records and replay metadata.
-- Applied migrations must never be edited; add a new forward migration instead.

ALTER TABLE mvp_audit_logs
  ADD COLUMN IF NOT EXISTS event_version INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS event_type TEXT NOT NULL DEFAULT 'STATE_CHANGE',
  ADD COLUMN IF NOT EXISTS transaction_id TEXT,
  ADD COLUMN IF NOT EXISTS task_id TEXT,
  ADD COLUMN IF NOT EXISTS run_id TEXT,
  ADD COLUMN IF NOT EXISTS message_id TEXT,
  ADD COLUMN IF NOT EXISTS payment_id TEXT,
  ADD COLUMN IF NOT EXISTS ledger_entry_id TEXT,
  ADD COLUMN IF NOT EXISTS evidence_hash TEXT,
  ADD COLUMN IF NOT EXISTS event_hash TEXT,
  ADD COLUMN IF NOT EXISTS previous_event_hash TEXT;

ALTER TABLE mvp_outbox_events
  ADD COLUMN IF NOT EXISTS event_version INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS transaction_id TEXT,
  ADD COLUMN IF NOT EXISTS delivery_status TEXT NOT NULL DEFAULT 'PENDING',
  ADD COLUMN IF NOT EXISTS delivery_attempts INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_error TEXT,
  ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ NOT NULL DEFAULT now();

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_audit_event_version_positive'
  ) THEN
    ALTER TABLE mvp_audit_logs
      ADD CONSTRAINT mvp_audit_event_version_positive
      CHECK (event_version > 0);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_outbox_event_version_positive'
  ) THEN
    ALTER TABLE mvp_outbox_events
      ADD CONSTRAINT mvp_outbox_event_version_positive
      CHECK (event_version > 0);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_outbox_delivery_status_valid'
  ) THEN
    ALTER TABLE mvp_outbox_events
      ADD CONSTRAINT mvp_outbox_delivery_status_valid
      CHECK (delivery_status IN ('PENDING', 'IN_FLIGHT', 'PUBLISHED', 'FAILED'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_outbox_delivery_attempts_nonnegative'
  ) THEN
    ALTER TABLE mvp_outbox_events
      ADD CONSTRAINT mvp_outbox_delivery_attempts_nonnegative
      CHECK (delivery_attempts >= 0);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_audit_event_hash_length'
  ) THEN
    ALTER TABLE mvp_audit_logs
      ADD CONSTRAINT mvp_audit_event_hash_length
      CHECK (event_hash IS NULL OR length(event_hash) = 64);
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS mvp_audit_task_created_idx
  ON mvp_audit_logs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_audit_target_created_idx
  ON mvp_audit_logs(target_type, target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_audit_event_type_idx
  ON mvp_audit_logs(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_outbox_delivery_idx
  ON mvp_outbox_events(delivery_status, available_at, created_at);
