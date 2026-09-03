-- R-CAO migration 0017: MPP Client Challenge/proof attempt history.
-- The Client history is append-only and contains no key or signed transaction.
-- It records the retry/idempotency boundary between an HTTP 402 Challenge and
-- a public Signer proof. Applied migrations are immutable.

CREATE TABLE IF NOT EXISTS mvp_mpp_client_attempts (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL CHECK (provider_id <> ''),
  payment_id TEXT NOT NULL,
  challenge_id TEXT NOT NULL,
  challenge_hash TEXT NOT NULL CHECK (length(challenge_hash) = 64),
  idempotency_key TEXT NOT NULL,
  nonce TEXT NOT NULL,
  task_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
  response_status INTEGER CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
  status TEXT NOT NULL CHECK (
    status IN (
      'CHALLENGE_RECEIVED', 'PENDING_APPROVAL', 'DENIED',
      'SIGNER_REQUESTED', 'SIGNER_FAILED', 'PROOF_SUBMITTED',
      'SUCCEEDED', 'FAILED', 'STOPPED', 'REPLAYED'
    )
  ),
  signer_request_id TEXT,
  signer_result_id TEXT,
  signer_receipt_id TEXT,
  provider_receipt_id TEXT,
  proof_hash TEXT CHECK (proof_hash IS NULL OR length(proof_hash) = 64),
  reason TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key, attempt_number)
);

CREATE INDEX IF NOT EXISTS mvp_mpp_client_attempts_payment_idx
  ON mvp_mpp_client_attempts(payment_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_mpp_client_attempts_challenge_idx
  ON mvp_mpp_client_attempts(challenge_id, nonce, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_mpp_client_attempts_status_idx
  ON mvp_mpp_client_attempts(status, created_at DESC);

CREATE OR REPLACE FUNCTION reject_mvp_mpp_client_attempt_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_mpp_client_attempts is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_mpp_client_attempts_no_mutation
  ON mvp_mpp_client_attempts;
CREATE TRIGGER mvp_mpp_client_attempts_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_mpp_client_attempts
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_mpp_client_attempt_mutation();
