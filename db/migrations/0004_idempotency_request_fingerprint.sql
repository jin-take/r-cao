-- R-CAO migration 0004: bind idempotency keys to complete requests
-- Existing legacy rows remain readable for audit but cannot be replayed safely.
-- Applied migrations must never be edited; add a new forward migration instead.

ALTER TABLE mvp_command_idempotency
  ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;

UPDATE mvp_command_idempotency
SET request_fingerprint = 'legacy-unfingerprinted:' || idempotency_key
WHERE request_fingerprint IS NULL;

ALTER TABLE mvp_command_idempotency
  ALTER COLUMN request_fingerprint SET NOT NULL;
