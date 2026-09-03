-- R-CAO migration 0016: isolated local/devnet Signer boundary.
--
-- Wallet rows contain public identity only.  Encrypted key material is owned
-- by the isolated Signer process/key store and is never stored in this
-- control-plane database.  Requests, results, and receipts are correlated to
-- the Policy authorization and Service Payment for audit and idempotency.
-- Applied migrations are immutable; future changes require another migration.

ALTER TABLE mvp_mpp_signer_authorizations
  ADD COLUMN IF NOT EXISTS policy_version TEXT NOT NULL
    DEFAULT 'mpp-policy-engine-v1';

CREATE OR REPLACE FUNCTION reject_mvp_mpp_signer_authorization_identity_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.payment_id <> OLD.payment_id
     OR NEW.policy_decision_id <> OLD.policy_decision_id
     OR NEW.policy_version <> OLD.policy_version
     OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
     OR NEW.authorization_hash <> OLD.authorization_hash
     OR NEW.issued_by <> OLD.issued_by
     OR NEW.issued_at <> OLD.issued_at
     OR NEW.expires_at <> OLD.expires_at
  THEN
    RAISE EXCEPTION 'mvp_mpp_signer_authorizations identity is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS mvp_signer_wallets (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id) ON DELETE RESTRICT,
  network TEXT NOT NULL CHECK (network IN ('LOCAL', 'SOLANA_DEVNET')),
  cluster TEXT NOT NULL CHECK (
    (network = 'LOCAL' AND cluster = 'LOCAL')
    OR (network = 'SOLANA_DEVNET' AND cluster = 'DEVNET')
  ),
  public_key TEXT NOT NULL CHECK (length(public_key) > 0),
  rotation_version INTEGER NOT NULL DEFAULT 1 CHECK (rotation_version > 0),
  status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE', 'SUSPENDED', 'REVOKED')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ,
  CHECK (lower(id) NOT LIKE '%private%'),
  CHECK (lower(id) NOT LIKE '%secret%'),
  CHECK (lower(id) NOT LIKE '%seed%'),
  CHECK (lower(public_key) NOT LIKE '%private%'),
  CHECK (lower(public_key) NOT LIKE '%secret%'),
  CHECK (lower(public_key) NOT LIKE '%seed%'),
  CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS mvp_signer_wallets_agent_identity_idx
  ON mvp_signer_wallets(agent_id, network, public_key);
CREATE INDEX IF NOT EXISTS mvp_signer_wallets_agent_status_idx
  ON mvp_signer_wallets(agent_id, status, updated_at DESC);

-- This row is a public request snapshot.  It intentionally has no encrypted
-- key, seed phrase, raw signature, or private credential column.
CREATE TABLE IF NOT EXISTS mvp_signer_requests (
  id TEXT PRIMARY KEY,
  authorization_id TEXT NOT NULL
    REFERENCES mvp_mpp_signer_authorizations(id) ON DELETE RESTRICT,
  policy_version TEXT NOT NULL DEFAULT 'mpp-policy-engine-v1',
  payment_id TEXT NOT NULL
    REFERENCES mvp_service_payments(id) ON DELETE RESTRICT,
  challenge_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  nonce TEXT NOT NULL,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE RESTRICT,
  run_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id) ON DELETE RESTRICT,
  service_id TEXT NOT NULL,
  profile_id TEXT NOT NULL REFERENCES mvp_agent_payment_profiles(id) ON DELETE RESTRICT,
  profile_version INTEGER NOT NULL CHECK (profile_version > 0),
  wallet_id TEXT NOT NULL REFERENCES mvp_signer_wallets(id) ON DELETE RESTRICT,
  wallet_public_key TEXT NOT NULL,
  wallet_rotation_version INTEGER NOT NULL CHECK (wallet_rotation_version > 0),
  network TEXT NOT NULL CHECK (network IN ('LOCAL', 'SOLANA_DEVNET')),
  cluster TEXT NOT NULL CHECK (
    (network = 'LOCAL' AND cluster = 'LOCAL')
    OR (network = 'SOLANA_DEVNET' AND cluster = 'DEVNET')
  ),
  program_id TEXT NOT NULL,
  instruction TEXT NOT NULL,
  token TEXT NOT NULL,
  token_mint TEXT,
  recipient TEXT NOT NULL,
  source_token_account TEXT,
  recipient_token_account TEXT,
  recent_blockhash TEXT,
  amount_units BIGINT NOT NULL CHECK (amount_units > 0),
  purpose TEXT NOT NULL CHECK (purpose = 'SERVICE_PAYMENT'),
  per_payment_limit_units BIGINT NOT NULL CHECK (per_payment_limit_units > 0),
  per_task_limit_units BIGINT NOT NULL CHECK (per_task_limit_units > 0),
  daily_limit_units BIGINT NOT NULL CHECK (daily_limit_units > 0),
  task_spent_units BIGINT NOT NULL DEFAULT 0 CHECK (task_spent_units >= 0),
  daily_spent_units BIGINT NOT NULL DEFAULT 0 CHECK (daily_spent_units >= 0),
  token_allowlist JSONB NOT NULL CHECK (jsonb_typeof(token_allowlist) = 'array'),
  recipient_allowlist JSONB NOT NULL CHECK (jsonb_typeof(recipient_allowlist) = 'array'),
  program_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(program_allowlist) = 'array'),
  instruction_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(instruction_allowlist) = 'array'),
  mint_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(mint_allowlist) = 'array'),
  request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
  status TEXT NOT NULL DEFAULT 'REQUESTED' CHECK (
    status IN (
      'REQUESTED', 'SIGNED', 'SUBMITTED', 'CONFIRMED', 'FAILED',
      'REJECTED', 'STOPPED', 'EXPIRED', 'REVOKED'
    )
  ),
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (payment_id),
  UNIQUE (wallet_id, nonce),
  CHECK (wallet_public_key <> ''),
  CHECK (lower(recipient) <> lower(agent_id)),
  CHECK (lower(recipient) NOT LIKE 'agent:%'),
  CHECK (lower(recipient) NOT LIKE 'agent-%'),
  CHECK (lower(recipient) NOT LIKE 'owner:%'),
  CHECK (lower(recipient) NOT LIKE 'owner-%'),
  CHECK (lower(recipient) NOT LIKE 'treasury:%'),
  CHECK (lower(recipient) NOT LIKE 'treasury-%'),
  CHECK (lower(recipient) NOT LIKE 'ledger:%'),
  CHECK (lower(recipient) NOT LIKE 'ledger-%'),
  CHECK (
    (network = 'LOCAL'
      AND upper(token) LIKE 'LOCAL_TEST_%'
      AND instruction = 'LOCAL_TEST_TRANSFER'
      AND upper(program_id) LIKE 'LOCAL_TEST_%'
      AND token_mint IS NULL
      AND source_token_account IS NULL
      AND recipient_token_account IS NULL
      AND recent_blockhash IS NULL)
    OR (network = 'SOLANA_DEVNET'
      AND upper(token) LIKE 'SPL_TEST_%'
      AND instruction = 'SPL_TOKEN_TRANSFER'
      AND program_id = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
      AND token_mint IS NOT NULL
      AND source_token_account IS NOT NULL
      AND recipient_token_account IS NOT NULL)
  ),
  CHECK (amount_units <= per_payment_limit_units),
  CHECK (amount_units + task_spent_units <= per_task_limit_units),
  CHECK (amount_units + daily_spent_units <= daily_limit_units)
);

CREATE INDEX IF NOT EXISTS mvp_signer_requests_payment_idx
  ON mvp_signer_requests(payment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_signer_requests_task_trace_idx
  ON mvp_signer_requests(task_id, trace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_signer_requests_status_idx
  ON mvp_signer_requests(status, expires_at, created_at DESC);

CREATE TABLE IF NOT EXISTS mvp_signer_results (
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE
    REFERENCES mvp_signer_requests(id) ON DELETE RESTRICT,
  authorization_id TEXT NOT NULL
    REFERENCES mvp_mpp_signer_authorizations(id) ON DELETE RESTRICT,
  payment_id TEXT NOT NULL
    REFERENCES mvp_service_payments(id) ON DELETE RESTRICT,
  request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
  status TEXT NOT NULL CHECK (
    status IN (
      'REQUESTED', 'SIGNED', 'SUBMITTED', 'CONFIRMED', 'FAILED',
      'REJECTED', 'STOPPED', 'EXPIRED', 'REVOKED'
    )
  ),
  external_signature TEXT,
  receipt_id TEXT,
  failure_code TEXT,
  failure_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mvp_signer_results_payment_idx
  ON mvp_signer_results(payment_id, completed_at DESC);
CREATE INDEX IF NOT EXISTS mvp_signer_results_status_idx
  ON mvp_signer_results(status, completed_at DESC);

CREATE TABLE IF NOT EXISTS mvp_signer_receipts (
  id TEXT PRIMARY KEY,
  result_id TEXT NOT NULL UNIQUE
    REFERENCES mvp_signer_results(id) ON DELETE RESTRICT,
  request_id TEXT NOT NULL
    REFERENCES mvp_signer_requests(id) ON DELETE RESTRICT,
  authorization_id TEXT NOT NULL
    REFERENCES mvp_mpp_signer_authorizations(id) ON DELETE RESTRICT,
  payment_id TEXT NOT NULL
    REFERENCES mvp_service_payments(id) ON DELETE RESTRICT,
  challenge_id TEXT NOT NULL,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE RESTRICT,
  run_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  network TEXT NOT NULL CHECK (network IN ('LOCAL', 'SOLANA_DEVNET')),
  cluster TEXT NOT NULL,
  token TEXT NOT NULL,
  amount_units BIGINT NOT NULL CHECK (amount_units > 0),
  status TEXT NOT NULL CHECK (
    status IN (
      'REQUESTED', 'SIGNED', 'SUBMITTED', 'CONFIRMED', 'FAILED',
      'REJECTED', 'STOPPED', 'EXPIRED', 'REVOKED'
    )
  ),
  request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
  external_signature TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mvp_signer_receipts_payment_idx
  ON mvp_signer_receipts(payment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_signer_receipts_correlation_idx
  ON mvp_signer_receipts(correlation_id, created_at ASC, id ASC);

ALTER TABLE mvp_service_payments
  ADD CONSTRAINT mvp_service_payments_signer_request_fk
  FOREIGN KEY (signer_request_id)
  REFERENCES mvp_signer_requests(id)
  ON DELETE RESTRICT;

ALTER TABLE mvp_service_payments
  ADD CONSTRAINT mvp_service_payments_receipt_fk
  FOREIGN KEY (receipt_id)
  REFERENCES mvp_signer_receipts(id)
  ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION reject_mvp_signer_wallet_identity_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.agent_id <> OLD.agent_id
     OR NEW.network <> OLD.network
     OR NEW.cluster <> OLD.cluster
     OR NEW.created_at <> OLD.created_at
     OR (
       NEW.rotation_version = OLD.rotation_version
       AND NEW.public_key <> OLD.public_key
     )
     OR (
       NEW.rotation_version <> OLD.rotation_version
       AND (
         NEW.rotation_version <> OLD.rotation_version + 1
         OR NEW.public_key = OLD.public_key
       )
     )
  THEN
    RAISE EXCEPTION 'mvp_signer_wallets identity changes require a sequential key rotation';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_signer_wallets_identity_immutable
  ON mvp_signer_wallets;
CREATE TRIGGER mvp_signer_wallets_identity_immutable
  BEFORE UPDATE ON mvp_signer_wallets
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_signer_wallet_identity_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_signer_request_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_signer_requests is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_signer_requests_no_mutation
  ON mvp_signer_requests;
CREATE TRIGGER mvp_signer_requests_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_signer_requests
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_signer_request_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_signer_result_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_signer_results is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_signer_results_no_mutation
  ON mvp_signer_results;
CREATE TRIGGER mvp_signer_results_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_signer_results
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_signer_result_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_signer_receipt_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_signer_receipts is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_signer_receipts_no_mutation
  ON mvp_signer_receipts;
CREATE TRIGGER mvp_signer_receipts_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_signer_receipts
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_signer_receipt_mutation();
