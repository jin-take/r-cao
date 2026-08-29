-- R-CAO migration 0006: persistent Agent Registry, membership, and delegation.
-- Applied migrations must never be edited; add a new forward migration instead.

ALTER TABLE mvp_agents
  ADD COLUMN IF NOT EXISTS identity_id TEXT,
  ADD COLUMN IF NOT EXISTS organization_layer TEXT NOT NULL DEFAULT 'VALUE_CREATION',
  ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'TEST',
  ADD COLUMN IF NOT EXISTS prompt_version TEXT NOT NULL DEFAULT 'unversioned',
  ADD COLUMN IF NOT EXISTS allowed_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS network_scope JSONB NOT NULL DEFAULT '["OFFCHAIN"]'::jsonb,
  ADD COLUMN IF NOT EXISTS budget_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS risk_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

UPDATE mvp_agents
SET identity_id = id
WHERE identity_id IS NULL;

UPDATE mvp_agents
SET organization_layer = CASE
  WHEN agent_type = 'AUDIT'::mvp_agent_type THEN 'VALUE_PROTECTION'
  ELSE 'VALUE_CREATION'
END;

ALTER TABLE mvp_agents
  ALTER COLUMN identity_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS mvp_agents_identity_idx
  ON mvp_agents(identity_id);

CREATE TABLE IF NOT EXISTS mvp_agent_memberships (
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  membership_role TEXT NOT NULL,
  assigned_by TEXT NOT NULL REFERENCES owners(id),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (task_id, agent_id),
  CHECK (membership_role <> '')
);

CREATE TABLE IF NOT EXISTS mvp_agent_delegations (
  id TEXT PRIMARY KEY,
  parent_agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  child_agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  task_id TEXT REFERENCES mvp_tasks(id) ON DELETE CASCADE,
  allowed_scope JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(allowed_scope) = 'array'),
  budget_limit_lamports BIGINT NOT NULL DEFAULT 0
    CHECK (budget_limit_lamports >= 0),
  risk_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE', 'SUSPENDED', 'EXPIRED', 'REVOKED')),
  expires_at TIMESTAMPTZ NOT NULL,
  created_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (parent_agent_id <> child_agent_id)
);

CREATE TABLE IF NOT EXISTS mvp_agent_evaluation_history (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  task_id TEXT REFERENCES mvp_tasks(id) ON DELETE SET NULL,
  score SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
  reputation_before NUMERIC(5,2),
  reputation_after NUMERIC(5,2),
  comment TEXT NOT NULL DEFAULT '',
  evaluated_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mvp_agent_payment_profiles (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL UNIQUE REFERENCES mvp_agents(id),
  network TEXT NOT NULL,
  token_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(token_allowlist) = 'array'),
  recipient_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(recipient_allowlist) = 'array'),
  per_payment_limit_lamports BIGINT NOT NULL DEFAULT 0
    CHECK (per_payment_limit_lamports >= 0),
  daily_limit_lamports BIGINT NOT NULL DEFAULT 0
    CHECK (daily_limit_lamports >= 0),
  status TEXT NOT NULL DEFAULT 'DISABLED'
    CHECK (status IN ('DISABLED', 'ACTIVE', 'SUSPENDED', 'EXPIRED')),
  expires_at TIMESTAMPTZ,
  created_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mvp_agent_change_history (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  change_type TEXT NOT NULL,
  before_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  changed_by TEXT NOT NULL REFERENCES owners(id),
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agents_layer_valid'
  ) THEN
    ALTER TABLE mvp_agents
      ADD CONSTRAINT mvp_agents_layer_valid
      CHECK (organization_layer IN ('VALUE_CREATION', 'VALUE_PROTECTION', 'VALUE_EVOLUTION'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agents_tools_array'
  ) THEN
    ALTER TABLE mvp_agents
      ADD CONSTRAINT mvp_agents_tools_array
      CHECK (jsonb_typeof(allowed_tools) = 'array');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agents_network_scope_array'
  ) THEN
    ALTER TABLE mvp_agents
      ADD CONSTRAINT mvp_agents_network_scope_array
      CHECK (jsonb_typeof(network_scope) = 'array');
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS mvp_agent_memberships_agent_idx
  ON mvp_agent_memberships(agent_id, active, expires_at);
CREATE INDEX IF NOT EXISTS mvp_agent_delegations_parent_idx
  ON mvp_agent_delegations(parent_agent_id, status, expires_at);
CREATE INDEX IF NOT EXISTS mvp_agent_delegations_child_idx
  ON mvp_agent_delegations(child_agent_id, task_id, status);
CREATE INDEX IF NOT EXISTS mvp_agent_evaluation_agent_idx
  ON mvp_agent_evaluation_history(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_agent_changes_agent_idx
  ON mvp_agent_change_history(agent_id, created_at DESC);
