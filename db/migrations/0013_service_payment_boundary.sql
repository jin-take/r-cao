-- R-CAO migration 0013: MPP Service Payment boundary.
-- Service Payments are external-service intents only. They are deliberately
-- not connected to the Virtual Reward Ledger or Treasury balance tables.
-- Applied migrations are immutable; future changes require another migration.

CREATE TABLE IF NOT EXISTS mvp_service_payments (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  challenge_id TEXT NOT NULL UNIQUE,
  nonce TEXT NOT NULL UNIQUE,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE RESTRICT,
  run_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id) ON DELETE RESTRICT,
  service_id TEXT NOT NULL,
  recipient TEXT NOT NULL,
  recipient_kind TEXT NOT NULL DEFAULT 'SERVICE'
    CHECK (recipient_kind = 'SERVICE'),
  network TEXT NOT NULL CHECK (network IN ('LOCAL', 'SOLANA_DEVNET')),
  token TEXT NOT NULL CHECK (
    upper(token) NOT IN ('SOL', 'VIRTUAL', 'VIRTUAL_REWARD', 'REWARD', 'TREASURY')
  ),
  amount_units BIGINT NOT NULL CHECK (amount_units > 0),
  purpose TEXT NOT NULL CHECK (purpose = 'SERVICE_PAYMENT'),
  expires_at TIMESTAMPTZ NOT NULL,
  challenge_hash TEXT NOT NULL CHECK (length(challenge_hash) = 64),
  policy_version TEXT NOT NULL,
  policy_decision TEXT NOT NULL CHECK (
    policy_decision IN ('allow', 'require_owner_approval', 'deny')
  ),
  status TEXT NOT NULL DEFAULT 'PROPOSED' CHECK (
    status IN (
      'PROPOSED', 'APPROVAL_REQUIRED', 'APPROVED', 'SIGNER_REQUESTED',
      'SUBMITTED', 'CONFIRMED', 'FAILED', 'EXPIRED', 'DENIED', 'STOPPED'
    )
  ),
  owner_approval_id TEXT REFERENCES approval_requests(id) ON DELETE RESTRICT,
  signer_request_id TEXT,
  receipt_id TEXT,
  transaction_signature TEXT,
  failure_code TEXT,
  failure_message TEXT,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (recipient <> agent_id),
  CHECK (lower(recipient) NOT LIKE 'agent:%'),
  CHECK (lower(recipient) NOT LIKE 'agent-%'),
  CHECK (lower(recipient) NOT LIKE 'owner:%'),
  CHECK (lower(recipient) NOT LIKE 'owner-%'),
  CHECK (lower(recipient) NOT LIKE 'treasury:%'),
  CHECK (lower(recipient) NOT LIKE 'treasury-%'),
  CHECK (lower(recipient) NOT LIKE 'ledger:%'),
  CHECK (lower(recipient) NOT LIKE 'ledger-%'),
  CHECK (
    (network = 'LOCAL' AND token LIKE 'LOCAL_TEST_%')
    OR (network = 'SOLANA_DEVNET' AND token LIKE 'SPL_TEST_%')
  )
);

CREATE TABLE IF NOT EXISTS mvp_service_payment_events (
  id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL REFERENCES mvp_service_payments(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'PROPOSED', 'APPROVAL_REQUIRED', 'APPROVED', 'SIGNER_REQUESTED',
      'SUBMITTED', 'CONFIRMED', 'FAILED', 'EXPIRED', 'DENIED', 'STOPPED'
    )
  ),
  idempotency_key TEXT NOT NULL UNIQUE,
  correlation_id TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(payload) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mvp_service_payments_task_created_idx
  ON mvp_service_payments(task_id, created_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_service_payments_run_idx
  ON mvp_service_payments(run_id, created_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_service_payments_trace_idx
  ON mvp_service_payments(trace_id, created_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_service_payments_correlation_idx
  ON mvp_service_payments(correlation_id, created_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_service_payments_status_idx
  ON mvp_service_payments(status, expires_at, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_service_payment_events_payment_idx
  ON mvp_service_payment_events(payment_id, created_at ASC, id ASC);

CREATE OR REPLACE FUNCTION reject_mvp_service_payment_identity_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.challenge_id <> OLD.challenge_id
     OR NEW.nonce <> OLD.nonce
     OR NEW.task_id <> OLD.task_id
     OR NEW.run_id <> OLD.run_id
     OR NEW.trace_id <> OLD.trace_id
     OR NEW.correlation_id <> OLD.correlation_id
     OR NEW.agent_id <> OLD.agent_id
     OR NEW.service_id <> OLD.service_id
     OR NEW.recipient <> OLD.recipient
     OR NEW.recipient_kind <> OLD.recipient_kind
     OR NEW.network <> OLD.network
     OR NEW.token <> OLD.token
     OR NEW.amount_units <> OLD.amount_units
     OR NEW.purpose <> OLD.purpose
     OR NEW.expires_at <> OLD.expires_at
     OR NEW.challenge_hash <> OLD.challenge_hash
     OR NEW.policy_version <> OLD.policy_version
     OR NEW.policy_decision <> OLD.policy_decision
     OR NEW.created_by <> OLD.created_by
     OR NEW.created_at <> OLD.created_at
  THEN
    RAISE EXCEPTION 'mvp_service_payments identity and policy metadata are immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_service_payments_identity_immutable
  ON mvp_service_payments;
CREATE TRIGGER mvp_service_payments_identity_immutable
  BEFORE UPDATE ON mvp_service_payments
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_service_payment_identity_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_service_payment_event_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_service_payment_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_service_payment_events_no_mutation
  ON mvp_service_payment_events;
CREATE TRIGGER mvp_service_payment_events_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_service_payment_events
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_service_payment_event_mutation();
