-- R-CAO migration 0014: versioned Agent Payment Profiles.
--
-- Migration 0006 created a minimal profile relation.  This migration keeps
-- that table name and evolves it into the MPP profile contract used by the
-- Python Policy boundary and the Owner Console.  Profiles are constraints,
-- not signing authority; no private key, seed phrase, or signed transaction
-- column is introduced.

ALTER TABLE mvp_agent_payment_profiles
  DROP CONSTRAINT IF EXISTS mvp_agent_payment_profiles_agent_id_key;

ALTER TABLE mvp_agent_payment_profiles
  ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS wallet_id TEXT,
  ADD COLUMN IF NOT EXISTS public_key TEXT,
  ADD COLUMN IF NOT EXISTS cluster TEXT NOT NULL DEFAULT 'LOCAL',
  ADD COLUMN IF NOT EXISTS service_id TEXT NOT NULL DEFAULT 'legacy-unconfigured',
  ADD COLUMN IF NOT EXISTS recipient TEXT NOT NULL DEFAULT 'legacy-unconfigured',
  ADD COLUMN IF NOT EXISTS recipient_kind TEXT NOT NULL DEFAULT 'SERVICE',
  ADD COLUMN IF NOT EXISTS mint_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS service_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS program_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS purpose_allowlist JSONB NOT NULL DEFAULT '["SERVICE_PAYMENT"]'::jsonb,
  ADD COLUMN IF NOT EXISTS risk_level TEXT NOT NULL DEFAULT 'LOW',
  ADD COLUMN IF NOT EXISTS approval_mode TEXT NOT NULL DEFAULT 'OWNER_APPROVAL',
  ADD COLUMN IF NOT EXISTS per_payment_limit_units BIGINT NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS per_task_limit_units BIGINT NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS daily_limit_units BIGINT NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS auto_approval_limit_units BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS max_expiry_seconds INTEGER NOT NULL DEFAULT 3600,
  ADD COLUMN IF NOT EXISTS rotation_state TEXT NOT NULL DEFAULT 'CURRENT',
  ADD COLUMN IF NOT EXISTS owner_approval_id TEXT,
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Existing rows from the minimal relation are fail-closed.  Empty legacy
-- allowlists are made explicit disabled placeholders rather than becoming an
-- accidental wildcard under the new contract.
UPDATE mvp_agent_payment_profiles
SET network = CASE
    WHEN upper(network) IN ('SOLANA_DEVNET', 'DEVNET') THEN 'SOLANA_DEVNET'
    ELSE 'LOCAL'
  END,
  cluster = CASE
    WHEN upper(network) IN ('SOLANA_DEVNET', 'DEVNET') THEN 'DEVNET'
    ELSE 'LOCAL'
  END,
  status = CASE
    WHEN status = 'ACTIVE' AND (
      expires_at IS NULL
      OR jsonb_array_length(token_allowlist) = 0
      OR jsonb_array_length(recipient_allowlist) = 0
    ) THEN 'DISABLED'
    ELSE status
  END,
  expires_at = COALESCE(expires_at, now()),
  token_allowlist = CASE
    WHEN jsonb_array_length(token_allowlist) = 0 THEN '["LEGACY_DISABLED"]'::jsonb
    ELSE token_allowlist
  END,
  recipient = CASE
    WHEN recipient = 'legacy-unconfigured'
      AND jsonb_array_length(recipient_allowlist) > 0
    THEN recipient_allowlist ->> 0
    ELSE recipient
  END,
  recipient_allowlist = CASE
    WHEN jsonb_array_length(recipient_allowlist) = 0
    THEN '["legacy-unconfigured"]'::jsonb
    ELSE recipient_allowlist
  END,
  service_allowlist = CASE
    WHEN jsonb_array_length(service_allowlist) = 0
    THEN jsonb_build_array(service_id)
    ELSE service_allowlist
  END,
  purpose_allowlist = '["SERVICE_PAYMENT"]'::jsonb,
  per_payment_limit_units = CASE
    WHEN per_payment_limit_lamports > 0 THEN per_payment_limit_lamports
    WHEN per_payment_limit_units > 0 THEN per_payment_limit_units
    ELSE 1
  END,
  per_task_limit_units = CASE
    WHEN daily_limit_lamports > 0 THEN daily_limit_lamports
    WHEN per_payment_limit_lamports > 0 THEN per_payment_limit_lamports
    WHEN per_task_limit_units > 0 THEN per_task_limit_units
    ELSE 1
  END,
  daily_limit_units = CASE
    WHEN daily_limit_lamports > 0 THEN daily_limit_lamports
    WHEN per_payment_limit_lamports > 0 THEN per_payment_limit_lamports
    WHEN daily_limit_units > 0 THEN daily_limit_units
    ELSE 1
  END,
  updated_at = COALESCE(updated_at, now())
WHERE TRUE;

-- Normalise legacy values that cannot satisfy the new closed allowlists.  The
-- profile is disabled before the strict checks are installed, so adoption is
-- safe even when an older database contains a partially configured row.
UPDATE mvp_agent_payment_profiles
SET token_allowlist = CASE
    WHEN network = 'SOLANA_DEVNET' THEN '["SPL_TEST_DISABLED"]'::jsonb
    ELSE '["LOCAL_TEST_DISABLED"]'::jsonb
  END,
  status = CASE WHEN status = 'ACTIVE' THEN 'DISABLED' ELSE status END
WHERE jsonb_array_length(token_allowlist) = 0
   OR EXISTS (
     SELECT 1
     FROM jsonb_array_elements_text(token_allowlist) AS item(token)
     WHERE upper(item.token) IN ('SOL', 'VIRTUAL', 'VIRTUAL_REWARD', 'REWARD', 'TREASURY')
        OR (network = 'LOCAL' AND item.token NOT LIKE 'LOCAL_TEST_%')
        OR (network = 'SOLANA_DEVNET' AND item.token NOT LIKE 'SPL_TEST_%')
   );

UPDATE mvp_agent_payment_profiles
SET recipient = 'legacy-unconfigured',
  recipient_allowlist = '["legacy-unconfigured"]'::jsonb,
  status = CASE WHEN status = 'ACTIVE' THEN 'DISABLED' ELSE status END
WHERE EXISTS (
  SELECT 1
  FROM jsonb_array_elements_text(recipient_allowlist) AS item(recipient)
  WHERE lower(item.recipient) LIKE 'agent:%'
     OR lower(item.recipient) LIKE 'agent-%'
     OR lower(item.recipient) LIKE 'owner:%'
     OR lower(item.recipient) LIKE 'owner-%'
     OR lower(item.recipient) LIKE 'treasury:%'
     OR lower(item.recipient) LIKE 'treasury-%'
     OR lower(item.recipient) LIKE 'ledger:%'
     OR lower(item.recipient) LIKE 'ledger-%'
);

ALTER TABLE mvp_agent_payment_profiles
  ALTER COLUMN expires_at SET NOT NULL;

-- Version history is an append-only snapshot relation.  It makes a Payment's
-- profile_version reproducible after a later Owner change or rotation.
CREATE TABLE IF NOT EXISTS mvp_agent_payment_profile_versions (
  profile_id TEXT NOT NULL REFERENCES mvp_agent_payment_profiles(id) ON DELETE RESTRICT,
  version INTEGER NOT NULL CHECK (version > 0),
  snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
  changed_by TEXT NOT NULL REFERENCES owners(id),
  change_type TEXT NOT NULL CHECK (change_type IN ('CREATE', 'UPDATE', 'STATUS', 'ROTATE')),
  owner_approval_id TEXT REFERENCES approval_requests(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (profile_id, version)
);

-- MPP profiles are unique by the Agent/Service/Recipient/network tuple while
-- enabled.  Disabled/revoked historical identities remain auditable.
CREATE UNIQUE INDEX IF NOT EXISTS mvp_agent_payment_profiles_active_identity_idx
  ON mvp_agent_payment_profiles(agent_id, service_id, recipient, network)
  WHERE status IN ('ACTIVE', 'SUSPENDED');
CREATE INDEX IF NOT EXISTS mvp_agent_payment_profiles_agent_status_idx
  ON mvp_agent_payment_profiles(agent_id, status, expires_at);
CREATE INDEX IF NOT EXISTS mvp_agent_payment_profile_versions_created_idx
  ON mvp_agent_payment_profile_versions(profile_id, version DESC, created_at DESC);

CREATE OR REPLACE FUNCTION mvp_profile_token_allowlist_valid(
  profile_network TEXT,
  tokens JSONB
)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT jsonb_typeof(tokens) = 'array'
    AND jsonb_array_length(tokens) > 0
    AND NOT EXISTS (
      SELECT 1
      FROM jsonb_array_elements_text(tokens) AS item(token)
      WHERE upper(item.token) IN ('SOL', 'VIRTUAL', 'VIRTUAL_REWARD', 'REWARD', 'TREASURY')
         OR (profile_network = 'LOCAL' AND item.token NOT LIKE 'LOCAL_TEST_%')
         OR (profile_network = 'SOLANA_DEVNET' AND item.token NOT LIKE 'SPL_TEST_%')
    )
$$;

CREATE OR REPLACE FUNCTION mvp_profile_recipient_allowlist_valid(recipients JSONB)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT jsonb_typeof(recipients) = 'array'
    AND jsonb_array_length(recipients) > 0
    AND NOT EXISTS (
      SELECT 1
      FROM jsonb_array_elements_text(recipients) AS item(recipient)
      WHERE lower(item.recipient) LIKE 'agent:%'
         OR lower(item.recipient) LIKE 'agent-%'
         OR lower(item.recipient) LIKE 'owner:%'
         OR lower(item.recipient) LIKE 'owner-%'
         OR lower(item.recipient) LIKE 'treasury:%'
         OR lower(item.recipient) LIKE 'treasury-%'
         OR lower(item.recipient) LIKE 'ledger:%'
         OR lower(item.recipient) LIKE 'ledger-%'
    )
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_version_positive'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_version_positive
      CHECK (version > 0);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_network_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_network_valid
      CHECK (network IN ('LOCAL', 'SOLANA_DEVNET'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_cluster_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_cluster_valid
      CHECK ((network = 'LOCAL' AND cluster = 'LOCAL')
          OR (network = 'SOLANA_DEVNET' AND cluster = 'DEVNET'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_status_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      DROP CONSTRAINT IF EXISTS mvp_agent_payment_profiles_status_check;
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_status_valid
      CHECK (status IN ('DRAFT', 'DISABLED', 'ACTIVE', 'SUSPENDED', 'EXPIRED', 'REVOKED'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_recipient_kind_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_recipient_kind_valid
      CHECK (recipient_kind = 'SERVICE');
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_approval_mode_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_approval_mode_valid
      CHECK (approval_mode IN ('AUTO_ALLOW', 'OWNER_APPROVAL', 'DENY'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_risk_level_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_risk_level_valid
      CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_rotation_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_rotation_valid
      CHECK (rotation_state IN ('CURRENT', 'PENDING', 'RETIRED', 'REVOKED'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_limits_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_limits_valid
      CHECK (
        per_payment_limit_units > 0
        AND per_task_limit_units >= per_payment_limit_units
        AND daily_limit_units >= per_task_limit_units
        AND auto_approval_limit_units >= 0
        AND auto_approval_limit_units <= per_payment_limit_units
        AND max_expiry_seconds > 0
        AND max_expiry_seconds <= 86400
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_allowlists_valid'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_allowlists_valid
      CHECK (
        jsonb_typeof(token_allowlist) = 'array'
        AND jsonb_typeof(mint_allowlist) = 'array'
        AND jsonb_typeof(service_allowlist) = 'array'
        AND jsonb_array_length(service_allowlist) > 0
        AND jsonb_typeof(recipient_allowlist) = 'array'
        AND jsonb_typeof(program_allowlist) = 'array'
        AND purpose_allowlist = '["SERVICE_PAYMENT"]'::jsonb
        AND service_allowlist @> jsonb_build_array(service_id)
        AND recipient_allowlist @> jsonb_build_array(recipient)
        AND mvp_profile_token_allowlist_valid(network, token_allowlist)
        AND mvp_profile_recipient_allowlist_valid(recipient_allowlist)
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_agent_payment_profiles_no_secret_identity'
  ) THEN
    ALTER TABLE mvp_agent_payment_profiles
      ADD CONSTRAINT mvp_agent_payment_profiles_no_secret_identity
      CHECK (
        (wallet_id IS NULL OR (
          lower(wallet_id) NOT LIKE '%private%'
          AND lower(wallet_id) NOT LIKE '%secret%'
          AND lower(wallet_id) NOT LIKE '%seed%'
          AND lower(wallet_id) NOT LIKE '%mnemonic%'
        ))
        AND (public_key IS NULL OR (
          lower(public_key) NOT LIKE '%private%'
          AND lower(public_key) NOT LIKE '%secret%'
          AND lower(public_key) NOT LIKE '%seed%'
          AND lower(public_key) NOT LIKE '%mnemonic%'
        ))
      );
  END IF;
END
$$;

ALTER TABLE mvp_agent_payment_profiles
  ADD CONSTRAINT mvp_agent_payment_profiles_owner_approval_fk
  FOREIGN KEY (owner_approval_id) REFERENCES approval_requests(id) ON DELETE RESTRICT;

CREATE OR REPLACE FUNCTION reject_mvp_agent_payment_profile_identity_mutation()
RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.agent_id <> OLD.agent_id
     OR NEW.created_by <> OLD.created_by
     OR NEW.created_at <> OLD.created_at
     OR NEW.version <> OLD.version + 1
  THEN
    RAISE EXCEPTION 'mvp_agent_payment_profiles identity is immutable and version must increment';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_agent_payment_profiles_identity_immutable
  ON mvp_agent_payment_profiles;
CREATE TRIGGER mvp_agent_payment_profiles_identity_immutable
  BEFORE UPDATE ON mvp_agent_payment_profiles
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_payment_profile_identity_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_agent_payment_profile_version_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_agent_payment_profile_versions is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_agent_payment_profile_versions_no_mutation
  ON mvp_agent_payment_profile_versions;
CREATE TRIGGER mvp_agent_payment_profile_versions_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_agent_payment_profile_versions
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_payment_profile_version_mutation();

-- Bind the Payment Challenge to the profile snapshot without breaking hashes
-- for pre-0014 records (the application omits absent optional fields when
-- canonicalising those records).
ALTER TABLE mvp_service_payments
  ADD COLUMN IF NOT EXISTS program_id TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS profile_id TEXT,
  ADD COLUMN IF NOT EXISTS profile_version INTEGER;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_service_payments_profile_fk'
  ) THEN
    ALTER TABLE mvp_service_payments
      ADD CONSTRAINT mvp_service_payments_profile_fk
      FOREIGN KEY (profile_id) REFERENCES mvp_agent_payment_profiles(id) ON DELETE RESTRICT;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'mvp_service_payments_profile_version_valid'
  ) THEN
    ALTER TABLE mvp_service_payments
      ADD CONSTRAINT mvp_service_payments_profile_version_valid
      CHECK ((profile_id IS NULL AND profile_version IS NULL)
          OR (profile_id IS NOT NULL AND profile_version > 0));
  END IF;
END
$$;

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
