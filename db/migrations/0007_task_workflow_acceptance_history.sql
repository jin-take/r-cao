-- R-CAO migration 0007: persistent Task command workflow metadata.
-- Applied migrations must never be edited; add a new forward migration instead.

CREATE TABLE IF NOT EXISTS mvp_task_acceptance_history (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE CASCADE,
  acceptance_criteria JSONB NOT NULL
    CHECK (jsonb_typeof(acceptance_criteria) = 'array'),
  changed_by TEXT NOT NULL REFERENCES owners(id),
  change_type TEXT NOT NULL CHECK (change_type IN ('INITIAL', 'AMENDMENT')),
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mvp_task_acceptance_history_task_idx
  ON mvp_task_acceptance_history(task_id, created_at DESC);
