-- R-CAO migration 0008: Virtual Reward Ledger and Treasury invariants.
-- Virtual entries are accounting facts only; they never represent on-chain
-- SOL, SPL tokens, MPP service payments, or customer assets.

CREATE TABLE IF NOT EXISTS mvp_treasury_accounts (
  id TEXT PRIMARY KEY,
  asset_type TEXT NOT NULL,
  currency TEXT NOT NULL,
  funded_lamports BIGINT NOT NULL DEFAULT 0 CHECK (funded_lamports >= 0),
  available_lamports BIGINT NOT NULL DEFAULT 0 CHECK (available_lamports >= 0),
  reserved_lamports BIGINT NOT NULL DEFAULT 0 CHECK (reserved_lamports >= 0),
  paid_lamports BIGINT NOT NULL DEFAULT 0 CHECK (paid_lamports >= 0),
  retained_lamports BIGINT NOT NULL DEFAULT 0 CHECK (retained_lamports >= 0),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (asset_type = 'VIRTUAL_REWARD'),
  CHECK (currency = 'VIRTUAL'),
  CHECK (available_lamports + reserved_lamports + paid_lamports + retained_lamports = funded_lamports)
);

CREATE TABLE IF NOT EXISTS mvp_virtual_ledger_entries (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES mvp_treasury_accounts(id),
  entry_type TEXT NOT NULL CHECK (entry_type IN (
    'TREASURY_FUNDING', 'REWARD_RESERVE', 'REWARD_RELEASED',
    'REWARD_CANCELLED', 'REWARD_PAYMENT', 'TREASURY_RETENTION'
  )),
  status mvp_reward_status NOT NULL,
  amount_lamports BIGINT NOT NULL CHECK (amount_lamports >= 0),
  asset_type TEXT NOT NULL CHECK (asset_type = 'VIRTUAL_REWARD'),
  currency TEXT NOT NULL CHECK (currency = 'VIRTUAL'),
  task_id TEXT REFERENCES mvp_tasks(id),
  allocation_id UUID REFERENCES reward_allocations(id),
  agent_id TEXT REFERENCES mvp_agents(id),
  calculation_version TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  recorded_by TEXT NOT NULL REFERENCES owners(id),
  correlation_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mvp_virtual_ledger_account_idx
  ON mvp_virtual_ledger_entries(account_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_virtual_ledger_task_idx
  ON mvp_virtual_ledger_entries(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_virtual_ledger_allocation_idx
  ON mvp_virtual_ledger_entries(allocation_id, created_at DESC);
