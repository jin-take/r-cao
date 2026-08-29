-- R-CAO migration 0004: bind idempotency keys to complete requests
-- Existing legacy rows remain readable for audit but cannot be replayed safely.
-- Applied migrations must never be edited; add a new forward migration instead.

ALTER TABLE mvp_command_idempotency
  ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;

UPDATE mvp_command_idempotency
SET request_fingerprint = 'legacy-unfingerprinted:' || idempotency_key
WHERE request_fingerprint IS NULL;

-- Keep the column nullable while older application instances may still write
-- rows without the new field during a rolling deployment. The repository
-- rejects NULL and legacy sentinel values for replay; a later cleanup
-- migration may enforce NOT NULL after the old writer is retired.
