-- R-CAO migration 0015: MPP Policy Engine and execution capability boundary.
-- Policy decisions, budget reservations, approvals, and Signer capabilities
-- are durable control-plane records.  This migration adds no wallet, key,
-- network, signing, or transaction-submission table.
-- Applied migrations are immutable; future changes require another migration.

ALTER TABLE mvp_service_payments
  ADD COLUMN IF NOT EXISTS policy_decision_id TEXT,
  ADD COLUMN IF NOT EXISTS budget_reservation_id TEXT;

ALTER TABLE mvp_service_payments
  DROP CONSTRAINT IF EXISTS mvp_service_payments_status_check;
ALTER TABLE mvp_service_payments
  ADD CONSTRAINT mvp_service_payments_status_check
  CHECK (
    status IN (
      'PROPOSED', 'APPROVAL_REQUIRED', 'APPROVED', 'SIGNER_REQUESTED',
      'SUBMITTED', 'CONFIRMED', 'FAILED', 'EXPIRED', 'DENIED', 'STOPPED',
      'CANCELLED'
    )
  );

ALTER TABLE mvp_service_payment_events
  DROP CONSTRAINT IF EXISTS mvp_service_payment_events_event_type_check;
ALTER TABLE mvp_service_payment_events
  ADD CONSTRAINT mvp_service_payment_events_event_type_check
  CHECK (
    event_type IN (
      'PROPOSED', 'APPROVAL_REQUIRED', 'APPROVED', 'SIGNER_REQUESTED',
      'SUBMITTED', 'CONFIRMED', 'FAILED', 'EXPIRED', 'DENIED', 'STOPPED',
      'CANCELLED'
    )
  );

-- A decision is append-only and remains queryable even when an application
-- rejects a proposal before a payment row can be created.
CREATE TABLE IF NOT EXISTS mvp_mpp_policy_decisions (
  id TEXT PRIMARY KEY,
  payment_id TEXT,
  idempotency_key TEXT NOT NULL,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE RESTRICT,
  run_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id) ON DELETE RESTRICT,
  profile_id TEXT REFERENCES mvp_agent_payment_profiles(id) ON DELETE RESTRICT,
  profile_version INTEGER CHECK (profile_version IS NULL OR profile_version > 0),
  decision TEXT NOT NULL CHECK (
    decision IN ('allow', 'require_owner_approval', 'deny')
  ),
  reason TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  approval_id TEXT REFERENCES approval_requests(id) ON DELETE RESTRICT,
  reservation_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key, payment_id)
);

CREATE INDEX IF NOT EXISTS mvp_mpp_policy_decisions_payment_idx
  ON mvp_mpp_policy_decisions(payment_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_mpp_policy_decisions_correlation_idx
  ON mvp_mpp_policy_decisions(correlation_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_mpp_policy_decisions_pending_idx
  ON mvp_mpp_policy_decisions(decision, created_at DESC);

-- Counters are split into Task and UTC-day scopes.  Both rows are locked in a
-- deterministic order by the repository before a reservation is inserted.
CREATE TABLE IF NOT EXISTS mvp_mpp_budget_counters (
  profile_id TEXT NOT NULL REFERENCES mvp_agent_payment_profiles(id) ON DELETE RESTRICT,
  scope_type TEXT NOT NULL CHECK (scope_type IN ('TASK', 'DAILY')),
  scope_id TEXT NOT NULL,
  period_key TEXT NOT NULL,
  limit_units BIGINT NOT NULL CHECK (limit_units > 0),
  reserved_units BIGINT NOT NULL DEFAULT 0 CHECK (reserved_units >= 0),
  consumed_units BIGINT NOT NULL DEFAULT 0 CHECK (consumed_units >= 0),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (profile_id, scope_type, scope_id, period_key),
  CHECK (reserved_units + consumed_units <= limit_units)
);

CREATE INDEX IF NOT EXISTS mvp_mpp_budget_counters_daily_idx
  ON mvp_mpp_budget_counters(scope_id, period_key, scope_type);

CREATE TABLE IF NOT EXISTS mvp_mpp_budget_reservations (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  payment_id TEXT NOT NULL,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id) ON DELETE RESTRICT,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE RESTRICT,
  profile_id TEXT NOT NULL REFERENCES mvp_agent_payment_profiles(id) ON DELETE RESTRICT,
  profile_version INTEGER NOT NULL CHECK (profile_version > 0),
  amount_units BIGINT NOT NULL CHECK (amount_units > 0),
  daily_period TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'RESERVED' CHECK (
    status IN ('RESERVED', 'CONSUMED', 'RELEASED', 'CANCELLED')
  ),
  correlation_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (payment_id)
);

CREATE INDEX IF NOT EXISTS mvp_mpp_budget_reservations_profile_idx
  ON mvp_mpp_budget_reservations(profile_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_mpp_budget_reservations_task_idx
  ON mvp_mpp_budget_reservations(task_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS mvp_mpp_signer_authorizations (
  id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL UNIQUE,
  policy_decision_id TEXT NOT NULL,
  approval_id TEXT REFERENCES approval_requests(id) ON DELETE RESTRICT,
  authorization_hash TEXT NOT NULL CHECK (length(authorization_hash) = 64),
  issued_by TEXT NOT NULL,
  issued_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'ISSUED' CHECK (
    status IN ('ISSUED', 'CONSUMED', 'REVOKED', 'EXPIRED')
  ),
  CHECK (expires_at > issued_at)
);

CREATE INDEX IF NOT EXISTS mvp_mpp_signer_authorizations_expiry_idx
  ON mvp_mpp_signer_authorizations(status, expires_at);

ALTER TABLE mvp_service_payments
  ADD CONSTRAINT mvp_service_payments_policy_decision_fk
  FOREIGN KEY (policy_decision_id)
  REFERENCES mvp_mpp_policy_decisions(id)
  ON DELETE RESTRICT
  DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE mvp_service_payments
  ADD CONSTRAINT mvp_service_payments_budget_reservation_fk
  FOREIGN KEY (budget_reservation_id)
  REFERENCES mvp_mpp_budget_reservations(id)
  ON DELETE RESTRICT
  DEFERRABLE INITIALLY DEFERRED;

CREATE OR REPLACE FUNCTION reject_mvp_mpp_policy_decision_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_mpp_policy_decisions is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_mpp_policy_decisions_no_mutation
  ON mvp_mpp_policy_decisions;
CREATE TRIGGER mvp_mpp_policy_decisions_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_mpp_policy_decisions
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_mpp_policy_decision_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_mpp_budget_reservation_identity_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.payment_id <> OLD.payment_id
     OR NEW.agent_id <> OLD.agent_id
     OR NEW.task_id <> OLD.task_id
     OR NEW.profile_id <> OLD.profile_id
     OR NEW.profile_version <> OLD.profile_version
     OR NEW.amount_units <> OLD.amount_units
     OR NEW.daily_period <> OLD.daily_period
     OR NEW.correlation_id <> OLD.correlation_id
     OR NEW.created_at <> OLD.created_at
  THEN
    RAISE EXCEPTION 'mvp_mpp_budget_reservations identity is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_mpp_budget_reservations_identity_immutable
  ON mvp_mpp_budget_reservations;
CREATE TRIGGER mvp_mpp_budget_reservations_identity_immutable
  BEFORE UPDATE ON mvp_mpp_budget_reservations
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_mpp_budget_reservation_identity_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_mpp_signer_authorization_identity_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.payment_id <> OLD.payment_id
     OR NEW.policy_decision_id <> OLD.policy_decision_id
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

DROP TRIGGER IF EXISTS mvp_mpp_signer_authorizations_identity_immutable
  ON mvp_mpp_signer_authorizations;
CREATE TRIGGER mvp_mpp_signer_authorizations_identity_immutable
  BEFORE UPDATE ON mvp_mpp_signer_authorizations
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_mpp_signer_authorization_identity_mutation();

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
     OR NEW.program_id <> OLD.program_id
     OR NEW.profile_id IS DISTINCT FROM OLD.profile_id
     OR NEW.profile_version IS DISTINCT FROM OLD.profile_version
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
     OR NEW.policy_decision_id IS DISTINCT FROM OLD.policy_decision_id
     OR NEW.budget_reservation_id IS DISTINCT FROM OLD.budget_reservation_id
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
