CREATE EXTENSION IF NOT EXISTS vector;

CREATE TYPE agent_role AS ENUM (
  'OWNER', 'MANAGER', 'RESEARCHER', 'BUILDER', 'REVIEWER', 'TREASURY', 'AUDITOR'
);
CREATE TYPE agent_status AS ENUM ('ACTIVE', 'PAUSED', 'RETIRED');
CREATE TYPE task_state AS ENUM (
  'DRAFT', 'ISSUED', 'IN_PROGRESS', 'IN_REVIEW', 'ACCEPTED', 'REJECTED', 'REWARDED', 'CANCELLED'
);
CREATE TYPE proposal_status AS ENUM ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED');
CREATE TYPE run_status AS ENUM ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'BLOCKED', 'CANCELLED');
CREATE TYPE message_type AS ENUM (
  'COMMAND', 'DELEGATION', 'REQUEST', 'RESPONSE', 'HANDOFF', 'REVIEW_REQUEST',
  'REVIEW_RESULT', 'BLOCK', 'ESCALATION', 'DECISION_REQUEST', 'OWNER_DECISION', 'EVIDENCE'
);
CREATE TYPE memory_type AS ENUM ('FACT', 'DECISION', 'POLICY', 'EVIDENCE', 'SUMMARY');

CREATE TABLE agents (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  role agent_role NOT NULL,
  capability_hash TEXT NOT NULL,
  model TEXT NOT NULL,
  status agent_status NOT NULL DEFAULT 'ACTIVE',
  reputation NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (reputation BETWEEN 0 AND 100),
  rank INTEGER NOT NULL DEFAULT 1 CHECK (rank > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_registry (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL CHECK (provider IN ('TEST', 'OPENAI', 'CODEX', 'LOCAL_SLM')),
  model_name TEXT NOT NULL,
  model_kind TEXT NOT NULL CHECK (model_kind IN ('LLM', 'SLM', 'EMBEDDING')),
  version TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE prompt_versions (
  id UUID PRIMARY KEY,
  prompt_name TEXT NOT NULL,
  version TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT',
  approved_by UUID REFERENCES agents(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (prompt_name, version)
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  reward_lamports BIGINT NOT NULL CHECK (reward_lamports >= 0),
  difficulty SMALLINT NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  state task_state NOT NULL DEFAULT 'DRAFT',
  deadline TIMESTAMPTZ NOT NULL,
  acceptance_criteria JSONB NOT NULL CHECK (jsonb_typeof(acceptance_criteria) = 'array'),
  issued_by UUID REFERENCES agents(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE task_assignments (
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  agent_id UUID NOT NULL REFERENCES agents(id),
  role agent_role NOT NULL CHECK (role <> 'OWNER'),
  contribution_score NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (contribution_score BETWEEN 0 AND 100),
  PRIMARY KEY (task_id, agent_id)
);

CREATE TABLE agent_runs (
  id UUID PRIMARY KEY,
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  agent_id UUID NOT NULL REFERENCES agents(id),
  provider TEXT NOT NULL CHECK (provider IN ('TEST', 'OPENAI', 'CODEX', 'LOCAL_SLM')),
  model_registry_id TEXT REFERENCES model_registry(id),
  prompt_version_id UUID REFERENCES prompt_versions(id),
  status run_status NOT NULL DEFAULT 'QUEUED',
  input_ref TEXT,
  output_ref TEXT,
  tool_allow_list JSONB NOT NULL DEFAULT '[]'::jsonb,
  proposed_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
  token_usage JSONB,
  latency_ms INTEGER,
  trace_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);

CREATE TABLE agent_messages (
  id UUID PRIMARY KEY,
  schema_version TEXT NOT NULL DEFAULT '1.0',
  idempotency_key TEXT NOT NULL UNIQUE,
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  trace_id TEXT NOT NULL,
  conversation_id TEXT,
  parent_message_id UUID REFERENCES agent_messages(id) ON DELETE SET NULL,
  sender_agent_id UUID NOT NULL REFERENCES agents(id),
  recipient_agent_id UUID NOT NULL REFERENCES agents(id),
  message_type message_type NOT NULL,
  authority_context JSONB NOT NULL DEFAULT '{}'::jsonb,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL DEFAULT 'SENT',
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE evaluations (
  id UUID PRIMARY KEY,
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  reviewer_id UUID NOT NULL REFERENCES agents(id),
  quality NUMERIC(5,2) NOT NULL CHECK (quality BETWEEN 0 AND 100),
  risk NUMERIC(5,2) NOT NULL CHECK (risk BETWEEN 0 AND 100),
  comment TEXT NOT NULL,
  final_score NUMERIC(5,2) NOT NULL CHECK (final_score BETWEEN 0 AND 100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memory_items (
  id UUID PRIMARY KEY,
  memory_type memory_type NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
  run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  message_id UUID REFERENCES agent_messages(id) ON DELETE SET NULL,
  embedding_model TEXT,
  embedding vector,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ledger_entries (
  id UUID PRIMARY KEY,
  agent_id UUID NOT NULL REFERENCES agents(id),
  entry_type TEXT NOT NULL CHECK (entry_type IN ('REWARD', 'ADJUSTMENT', 'TREASURY_RETENTION')),
  amount_lamports BIGINT NOT NULL,
  source TEXT NOT NULL,
  task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
  tx_ref TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE treasury_proposals (
  id UUID PRIMARY KEY,
  proposal_type TEXT NOT NULL CHECK (proposal_type IN ('RESEARCH', 'INFRASTRUCTURE', 'PRODUCT', 'RESERVE')),
  amount_lamports BIGINT NOT NULL CHECK (amount_lamports > 0),
  expected_roi_bps INTEGER NOT NULL,
  risk SMALLINT NOT NULL CHECK (risk BETWEEN 1 AND 5),
  status proposal_status NOT NULL DEFAULT 'DRAFT',
  proposed_by UUID NOT NULL REFERENCES agents(id),
  approval_by UUID REFERENCES agents(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
  id UUID PRIMARY KEY,
  actor_id UUID NOT NULL REFERENCES agents(id),
  task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
  run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  message_id UUID REFERENCES agent_messages(id) ON DELETE SET NULL,
  trace_id TEXT,
  action TEXT NOT NULL,
  before_state JSONB,
  after_state JSONB,
  evidence_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE outbox_events (
  id UUID PRIMARY KEY,
  aggregate_type TEXT NOT NULL,
  aggregate_id UUID NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Operational stop state, telemetry, and incident history. These records are
-- separate from domain authority and remain safe to inspect during a halt.
CREATE TABLE mvp_stop_controls (
  target TEXT NOT NULL CHECK (target IN (
    'GLOBAL', 'COMMAND', 'RUN', 'AGENT', 'PROVIDER', 'MPP', 'SIGNER', 'PAYMENT'
  )),
  target_id TEXT NOT NULL,
  stopped BOOLEAN NOT NULL,
  reason TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  changed_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (target, target_id)
);

CREATE TABLE mvp_stop_control_history (
  id TEXT PRIMARY KEY,
  target TEXT NOT NULL,
  target_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('STOP', 'RESUME')),
  actor_id TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  reason TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_observability_events (
  id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  request_id TEXT,
  run_id TEXT,
  trace_id TEXT,
  actor_id TEXT,
  status TEXT,
  duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
  input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
  total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
  cost_microusd BIGINT CHECK (cost_microusd IS NULL OR cost_microusd >= 0),
  attempts INTEGER CHECK (attempts IS NULL OR attempts >= 0),
  metric_value DOUBLE PRECISION,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(metadata) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_incidents (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  status TEXT NOT NULL DEFAULT 'OPEN'
    CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
  summary TEXT NOT NULL,
  opened_by TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  recovery_steps JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(recovery_steps) = 'array'),
  resolved_by TEXT,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_incident_timeline (
  id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL REFERENCES mvp_incidents(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  note TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tasks_state_idx ON tasks(state);
CREATE INDEX assignments_agent_idx ON task_assignments(agent_id);
CREATE INDEX agent_runs_task_created_idx ON agent_runs(task_id, created_at DESC);
CREATE INDEX agent_runs_trace_idx ON agent_runs(trace_id);
CREATE INDEX agent_messages_task_created_idx ON agent_messages(task_id, created_at DESC);
CREATE INDEX agent_messages_trace_idx ON agent_messages(trace_id);
CREATE INDEX agent_messages_conversation_idx ON agent_messages(conversation_id, created_at DESC);
CREATE INDEX memory_items_source_task_idx ON memory_items(task_id, created_at DESC);
CREATE INDEX memory_items_fts_idx ON memory_items USING GIN (
  to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, ''))
);
CREATE INDEX ledger_agent_created_idx ON ledger_entries(agent_id, created_at DESC);
CREATE INDEX audit_created_idx ON audit_logs(created_at DESC);
CREATE INDEX audit_trace_idx ON audit_logs(trace_id);
CREATE INDEX outbox_pending_idx ON outbox_events(created_at) WHERE published_at IS NULL;
CREATE INDEX mvp_stop_controls_stopped_idx
  ON mvp_stop_controls(stopped, target, target_id);
CREATE INDEX mvp_stop_control_history_created_idx
  ON mvp_stop_control_history(created_at DESC, id DESC);
CREATE INDEX mvp_stop_control_history_target_idx
  ON mvp_stop_control_history(target, target_id, created_at ASC, id ASC);
CREATE INDEX mvp_observability_events_trace_idx
  ON mvp_observability_events(trace_id, created_at ASC, id ASC);
CREATE INDEX mvp_observability_events_run_idx
  ON mvp_observability_events(run_id, created_at ASC, id ASC);
CREATE INDEX mvp_observability_events_name_created_idx
  ON mvp_observability_events(event_name, created_at DESC, id DESC);
CREATE INDEX mvp_incidents_status_created_idx
  ON mvp_incidents(status, created_at DESC, id DESC);
CREATE INDEX mvp_incident_timeline_incident_created_idx
  ON mvp_incident_timeline(incident_id, created_at ASC, id ASC);

CREATE OR REPLACE FUNCTION reject_mvp_stop_history_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_stop_control_history is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_stop_control_history_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_stop_control_history
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_stop_history_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_observability_event_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_observability_events is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_observability_events_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_observability_events
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_observability_event_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_incident_timeline_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_incident_timeline is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_incident_timeline_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_incident_timeline
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_incident_timeline_mutation();

-- The embedding column is intentionally dimensionless in Phase 1 because an
-- OpenAI model and a local SLM may use different dimensions. Add a
-- model-specific HNSW index only after the embedding model is fixed by an ADR.
CREATE VIEW operation_records AS
SELECT
  id AS record_id,
  'MESSAGES'::TEXT AS scope,
  message_type::TEXT AS title,
  payload::TEXT AS body,
  task_id,
  run_id,
  sender_agent_id AS agent_id,
  status,
  created_at
FROM agent_messages
UNION ALL
SELECT
  id AS record_id,
  'RUNS'::TEXT AS scope,
  provider || ':' || coalesce(model_registry_id, 'unregistered') AS title,
  coalesce(output_ref, '') AS body,
  task_id,
  id AS run_id,
  agent_id,
  status::TEXT,
  created_at
FROM agent_runs
UNION ALL
SELECT
  id AS record_id,
  'MEMORY'::TEXT AS scope,
  title,
  content AS body,
  task_id,
  run_id,
  NULL::UUID AS agent_id,
  status,
  created_at
FROM memory_items
UNION ALL
SELECT
  id AS record_id,
  'AUDIT'::TEXT AS scope,
  action AS title,
  coalesce(after_state::TEXT, '') AS body,
  task_id,
  run_id,
  actor_id AS agent_id,
  'RECORDED'::TEXT AS status,
  created_at
FROM audit_logs;

-- -------------------------------------------------------------------------
-- Owner-Directed MVP schema
-- -------------------------------------------------------------------------
-- These tables are deliberately separate from the Phase 1 simulation tables
-- above. They are the persistence contract for the Owner-Directed command
-- store and can be migrated independently. No Agent-to-Agent Reward Transfer
-- table exists by design.

CREATE TYPE mvp_agent_type AS ENUM (
  'EXECUTIVE', 'SUB_AGENT', 'EXPANSION_AGENT', 'AUDIT'
);
CREATE TYPE mvp_agent_status AS ENUM (
  'ACTIVE', 'SUSPENDED', 'RETIRED', 'DRAFT'
);
CREATE TYPE mvp_task_status AS ENUM (
  'DRAFT', 'APPROVED', 'PLANNING', 'IN_PROGRESS', 'REVIEW', 'AUDIT',
  'OWNER_REVIEW', 'REWORK', 'BLOCKED', 'COMPLETED', 'REJECTED', 'CANCELLED'
);
CREATE TYPE mvp_audit_result AS ENUM (
  'PASS', 'PASS_WITH_CONDITIONS', 'FAIL', 'OWNER_REVIEW_REQUIRED'
);
CREATE TYPE mvp_reward_status AS ENUM (
  'Pending', 'Proposed', 'Approved', 'Paid', 'Reserved', 'Cancelled'
);
CREATE TYPE mvp_approval_decision AS ENUM (
  'APPROVE', 'REJECT', 'REQUEST_CHANGES', 'HOLD'
);
CREATE TYPE mvp_approval_type AS ENUM (
  'TASK_COMPLETION', 'REWARD', 'BOARD_PROPOSAL', 'EXTERNAL_ACTION',
  'AGENT_CREATION', 'AGENT_AUTHORITY_CHANGE', 'POLICY_EXCEPTION'
);
CREATE TYPE mvp_external_channel AS ENUM (
  'EMAIL', 'DM', 'SNS', 'API_WRITE', 'CONTRACT', 'OTHER'
);
CREATE TYPE mvp_external_status AS ENUM (
  'PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'NOT_EXECUTED', 'EXECUTED'
);
CREATE TYPE mvp_policy_result AS ENUM (
  'ALLOW', 'DENY', 'OWNER_APPROVAL_REQUIRED', 'ALLOW_WITH_SCOPE'
);

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE owners (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  CONSTRAINT owners_user_fk FOREIGN KEY (id) REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_agents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  identity_id TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  organization_layer TEXT NOT NULL DEFAULT 'VALUE_CREATION'
    CHECK (organization_layer IN ('VALUE_CREATION', 'VALUE_PROTECTION', 'VALUE_EVOLUTION')),
  mission TEXT NOT NULL,
  responsibilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  authority JSONB NOT NULL DEFAULT '[]'::jsonb,
  prohibited_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
  reports_to TEXT NOT NULL,
  agent_type mvp_agent_type NOT NULL,
  status mvp_agent_status NOT NULL DEFAULT 'ACTIVE',
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  model TEXT NOT NULL DEFAULT 'policy-bound',
  provider TEXT NOT NULL DEFAULT 'TEST',
  prompt_version TEXT NOT NULL DEFAULT 'unversioned',
  capability_hash TEXT NOT NULL,
  allowed_tools JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(allowed_tools) = 'array'),
  network_scope JSONB NOT NULL DEFAULT '["OFFCHAIN"]'::jsonb
    CHECK (jsonb_typeof(network_scope) = 'array'),
  budget_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  risk_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  budget_limit_lamports BIGINT NOT NULL DEFAULT 0 CHECK (budget_limit_lamports >= 0),
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_authorities (
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  authority TEXT NOT NULL,
  approved_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (agent_id, authority)
);

CREATE TABLE agent_restrictions (
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  restriction TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (agent_id, restriction)
);

CREATE TABLE mvp_tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  objective TEXT NOT NULL,
  background TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  deadline TIMESTAMPTZ NOT NULL,
  acceptance_criteria JSONB NOT NULL CHECK (jsonb_typeof(acceptance_criteria) = 'array'),
  reward_budget_lamports BIGINT NOT NULL CHECK (reward_budget_lamports >= 0),
  assigned_executive_agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  risk_level TEXT NOT NULL CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  external_action_allowed BOOLEAN NOT NULL DEFAULT FALSE,
  owner_approval_required BOOLEAN NOT NULL DEFAULT TRUE,
  status mvp_task_status NOT NULL DEFAULT 'DRAFT',
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  progress SMALLINT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  created_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_sub_tasks (
  id TEXT PRIMARY KEY,
  parent_task_id TEXT NOT NULL REFERENCES mvp_tasks(id),
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  assigned_agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  status mvp_task_status NOT NULL DEFAULT 'DRAFT',
  progress SMALLINT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  dependencies JSONB NOT NULL DEFAULT '[]'::jsonb,
  artifact TEXT,
  review_result TEXT,
  audit_result mvp_audit_result,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_task_assignments (
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id),
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  assigned_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (task_id, agent_id)
);

CREATE TABLE mvp_task_acceptance_history (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE CASCADE,
  acceptance_criteria JSONB NOT NULL
    CHECK (jsonb_typeof(acceptance_criteria) = 'array'),
  changed_by TEXT NOT NULL REFERENCES owners(id),
  change_type TEXT NOT NULL CHECK (change_type IN ('INITIAL', 'AMENDMENT')),
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_agent_memberships (
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  membership_role TEXT NOT NULL CHECK (membership_role <> ''),
  assigned_by TEXT NOT NULL REFERENCES owners(id),
  active BOOLEAN NOT NULL DEFAULT TRUE,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (task_id, agent_id)
);

CREATE TABLE mvp_agent_delegations (
  id TEXT PRIMARY KEY,
  parent_agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  child_agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  task_id TEXT REFERENCES mvp_tasks(id) ON DELETE CASCADE,
  allowed_scope JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(allowed_scope) = 'array'),
  budget_limit_lamports BIGINT NOT NULL DEFAULT 0 CHECK (budget_limit_lamports >= 0),
  risk_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE', 'SUSPENDED', 'EXPIRED', 'REVOKED')),
  expires_at TIMESTAMPTZ NOT NULL,
  created_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (parent_agent_id <> child_agent_id)
);

CREATE TABLE mvp_agent_evaluation_history (
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

CREATE TABLE mvp_agent_payment_profiles (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  wallet_id TEXT,
  public_key TEXT,
  network TEXT NOT NULL CHECK (network IN ('LOCAL', 'SOLANA_DEVNET')),
  cluster TEXT NOT NULL CHECK (
    (network = 'LOCAL' AND cluster = 'LOCAL')
    OR (network = 'SOLANA_DEVNET' AND cluster = 'DEVNET')
  ),
  service_id TEXT NOT NULL,
  recipient TEXT NOT NULL,
  recipient_kind TEXT NOT NULL DEFAULT 'SERVICE'
    CHECK (recipient_kind = 'SERVICE'),
  token_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(token_allowlist) = 'array'),
  mint_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(mint_allowlist) = 'array'),
  service_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(service_allowlist) = 'array'),
  recipient_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(recipient_allowlist) = 'array'),
  program_allowlist JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(program_allowlist) = 'array'),
  purpose_allowlist JSONB NOT NULL DEFAULT '["SERVICE_PAYMENT"]'::jsonb
    CHECK (purpose_allowlist = '["SERVICE_PAYMENT"]'::jsonb),
  risk_level TEXT NOT NULL DEFAULT 'LOW'
    CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  approval_mode TEXT NOT NULL DEFAULT 'OWNER_APPROVAL'
    CHECK (approval_mode IN ('AUTO_ALLOW', 'OWNER_APPROVAL', 'DENY')),
  -- Kept for compatibility with the 0006 relation; Policy uses *_units.
  per_payment_limit_lamports BIGINT NOT NULL DEFAULT 0 CHECK (per_payment_limit_lamports >= 0),
  daily_limit_lamports BIGINT NOT NULL DEFAULT 0 CHECK (daily_limit_lamports >= 0),
  per_payment_limit_units BIGINT NOT NULL CHECK (per_payment_limit_units > 0),
  per_task_limit_units BIGINT NOT NULL CHECK (per_task_limit_units >= per_payment_limit_units),
  daily_limit_units BIGINT NOT NULL CHECK (daily_limit_units >= per_task_limit_units),
  auto_approval_limit_units BIGINT NOT NULL DEFAULT 0
    CHECK (auto_approval_limit_units >= 0 AND auto_approval_limit_units <= per_payment_limit_units),
  max_expiry_seconds INTEGER NOT NULL CHECK (max_expiry_seconds BETWEEN 1 AND 86400),
  status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('DRAFT', 'DISABLED', 'ACTIVE', 'SUSPENDED', 'EXPIRED', 'REVOKED')),
  rotation_state TEXT NOT NULL DEFAULT 'CURRENT'
    CHECK (rotation_state IN ('CURRENT', 'PENDING', 'RETIRED', 'REVOKED')),
  expires_at TIMESTAMPTZ NOT NULL,
  created_by TEXT NOT NULL REFERENCES owners(id),
  owner_approval_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (service_allowlist @> jsonb_build_array(service_id)),
  CHECK (recipient_allowlist @> jsonb_build_array(recipient)),
  CHECK (lower(recipient) NOT LIKE 'agent:%'),
  CHECK (lower(recipient) NOT LIKE 'agent-%'),
  CHECK (lower(recipient) NOT LIKE 'owner:%'),
  CHECK (lower(recipient) NOT LIKE 'owner-%'),
  CHECK (lower(recipient) NOT LIKE 'treasury:%'),
  CHECK (lower(recipient) NOT LIKE 'treasury-%'),
  CHECK (lower(recipient) NOT LIKE 'ledger:%'),
  CHECK (lower(recipient) NOT LIKE 'ledger-%')
);

CREATE TABLE mvp_agent_change_history (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  change_type TEXT NOT NULL,
  before_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  changed_by TEXT NOT NULL REFERENCES owners(id),
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE task_artifacts (
  id UUID PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id),
  sub_task_id TEXT REFERENCES mvp_sub_tasks(id),
  uri TEXT NOT NULL,
  content_hash TEXT,
  submitted_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_reviews (
  id UUID PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id),
  reviewer TEXT NOT NULL REFERENCES mvp_agents(id),
  quality SMALLINT NOT NULL CHECK (quality BETWEEN 0 AND 100),
  completeness SMALLINT NOT NULL CHECK (completeness BETWEEN 0 AND 100),
  correctness SMALLINT NOT NULL CHECK (correctness BETWEEN 0 AND 100),
  required_changes JSONB NOT NULL DEFAULT '[]'::jsonb,
  comment TEXT NOT NULL DEFAULT '',
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_audits (
  id UUID PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id),
  auditor TEXT NOT NULL REFERENCES mvp_agents(id),
  policy_compliance BOOLEAN NOT NULL,
  security_risk TEXT NOT NULL CHECK (security_risk IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  external_action_check BOOLEAN NOT NULL,
  reward_manipulation_check BOOLEAN NOT NULL,
  authority_violation_check BOOLEAN NOT NULL,
  result mvp_audit_result NOT NULL,
  comment TEXT NOT NULL DEFAULT '',
  audited_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE owner_evaluations (
  id UUID PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id),
  quality SMALLINT NOT NULL CHECK (quality BETWEEN 0 AND 100),
  difficulty SMALLINT NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  contribution SMALLINT NOT NULL CHECK (contribution BETWEEN 0 AND 100),
  timeliness SMALLINT NOT NULL CHECK (timeliness BETWEEN 0 AND 100),
  rework SMALLINT NOT NULL CHECK (rework BETWEEN 0 AND 100),
  strategic_value SMALLINT NOT NULL CHECK (strategic_value BETWEEN 0 AND 100),
  owner_comment TEXT NOT NULL DEFAULT '',
  evaluated_by TEXT NOT NULL REFERENCES owners(id),
  evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reward_budgets (
  task_id TEXT PRIMARY KEY REFERENCES mvp_tasks(id),
  amount_lamports BIGINT NOT NULL CHECK (amount_lamports >= 0),
  defined_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reward_allocations (
  id UUID PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id),
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  reward_budget_lamports BIGINT NOT NULL CHECK (reward_budget_lamports >= 0),
  proposed_reward_lamports BIGINT NOT NULL DEFAULT 0 CHECK (proposed_reward_lamports >= 0),
  approved_reward_lamports BIGINT CHECK (approved_reward_lamports >= 0),
  paid_reward_lamports BIGINT NOT NULL DEFAULT 0 CHECK (paid_reward_lamports >= 0),
  reserved_reward_lamports BIGINT NOT NULL DEFAULT 0 CHECK (reserved_reward_lamports >= 0),
  cancelled_reward_lamports BIGINT NOT NULL DEFAULT 0 CHECK (cancelled_reward_lamports >= 0),
  status mvp_reward_status NOT NULL DEFAULT 'Pending',
  approved_by TEXT REFERENCES owners(id),
  approved_at TIMESTAMPTZ,
  comment TEXT NOT NULL DEFAULT ''
);

CREATE TABLE reward_ledger (
  id UUID PRIMARY KEY,
  allocation_id UUID NOT NULL REFERENCES reward_allocations(id),
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id),
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  amount_lamports BIGINT NOT NULL CHECK (amount_lamports >= 0),
  status mvp_reward_status NOT NULL,
  recorded_by TEXT NOT NULL REFERENCES owners(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Virtual Reward accounting is separate from MPP Service Payment accounting.
-- It records no on-chain or customer-asset movement.
CREATE TABLE mvp_treasury_accounts (
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

CREATE TABLE mvp_virtual_ledger_entries (
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

CREATE TABLE approval_requests (
  id TEXT PRIMARY KEY,
  approval_type mvp_approval_type NOT NULL,
  target_id TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  owner_decision mvp_approval_decision,
  comment TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);

CREATE TABLE mvp_agent_payment_profile_versions (
  profile_id TEXT NOT NULL REFERENCES mvp_agent_payment_profiles(id) ON DELETE RESTRICT,
  version INTEGER NOT NULL CHECK (version > 0),
  snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
  changed_by TEXT NOT NULL REFERENCES owners(id),
  change_type TEXT NOT NULL CHECK (change_type IN ('CREATE', 'UPDATE', 'STATUS', 'ROTATE')),
  owner_approval_id TEXT REFERENCES approval_requests(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (profile_id, version)
);

CREATE UNIQUE INDEX mvp_agent_payment_profiles_active_identity_idx
  ON mvp_agent_payment_profiles(agent_id, service_id, recipient, network)
  WHERE status IN ('ACTIVE', 'SUSPENDED');
CREATE INDEX mvp_agent_payment_profiles_agent_status_idx
  ON mvp_agent_payment_profiles(agent_id, status, expires_at);
CREATE INDEX mvp_agent_payment_profile_versions_created_idx
  ON mvp_agent_payment_profile_versions(profile_id, version DESC, created_at DESC);

-- MPP Service Payments are external-service intents only. They are separate
-- from the Virtual Reward Ledger and never represent Agent-to-Agent transfers.
CREATE TABLE mvp_service_payments (
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
  program_id TEXT NOT NULL DEFAULT '',
  profile_id TEXT REFERENCES mvp_agent_payment_profiles(id) ON DELETE RESTRICT,
  profile_version INTEGER CHECK (
    (profile_id IS NULL AND profile_version IS NULL)
    OR (profile_id IS NOT NULL AND profile_version > 0)
  ),
  policy_decision_id TEXT,
  budget_reservation_id TEXT,
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
      'SUBMITTED', 'CONFIRMED', 'FAILED', 'EXPIRED', 'DENIED', 'STOPPED',
      'CANCELLED'
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

CREATE TABLE mvp_service_payment_events (
  id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL REFERENCES mvp_service_payments(id) ON DELETE RESTRICT,
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'PROPOSED', 'APPROVAL_REQUIRED', 'APPROVED', 'SIGNER_REQUESTED',
      'SUBMITTED', 'CONFIRMED', 'FAILED', 'EXPIRED', 'DENIED', 'STOPPED',
      'CANCELLED'
    )
  ),
  idempotency_key TEXT NOT NULL UNIQUE,
  correlation_id TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(payload) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_mpp_policy_decisions (
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

CREATE TABLE mvp_mpp_budget_counters (
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

CREATE TABLE mvp_mpp_budget_reservations (
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

CREATE TABLE mvp_mpp_signer_authorizations (
  id TEXT PRIMARY KEY,
  payment_id TEXT NOT NULL UNIQUE,
  policy_decision_id TEXT NOT NULL,
  policy_version TEXT NOT NULL DEFAULT 'mpp-policy-engine-v1',
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

-- The isolated Signer stores only public wallet identity in the control
-- plane.  Encrypted key material remains inside the Signer process/key store.
CREATE TABLE mvp_signer_wallets (
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

CREATE TABLE mvp_signer_requests (
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

CREATE TABLE mvp_signer_results (
  id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE REFERENCES mvp_signer_requests(id) ON DELETE RESTRICT,
  authorization_id TEXT NOT NULL
    REFERENCES mvp_mpp_signer_authorizations(id) ON DELETE RESTRICT,
  payment_id TEXT NOT NULL REFERENCES mvp_service_payments(id) ON DELETE RESTRICT,
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

CREATE TABLE mvp_signer_receipts (
  id TEXT PRIMARY KEY,
  result_id TEXT NOT NULL UNIQUE REFERENCES mvp_signer_results(id) ON DELETE RESTRICT,
  request_id TEXT NOT NULL REFERENCES mvp_signer_requests(id) ON DELETE RESTRICT,
  authorization_id TEXT NOT NULL
    REFERENCES mvp_mpp_signer_authorizations(id) ON DELETE RESTRICT,
  payment_id TEXT NOT NULL REFERENCES mvp_service_payments(id) ON DELETE RESTRICT,
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

-- MPP Client retry/idempotency history is public control-plane metadata only.
CREATE TABLE mvp_mpp_client_attempts (
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

CREATE INDEX mvp_mpp_client_attempts_payment_idx
  ON mvp_mpp_client_attempts(payment_id, created_at ASC, id ASC);
CREATE INDEX mvp_mpp_client_attempts_challenge_idx
  ON mvp_mpp_client_attempts(challenge_id, nonce, created_at ASC, id ASC);
CREATE INDEX mvp_mpp_client_attempts_status_idx
  ON mvp_mpp_client_attempts(status, created_at DESC);

CREATE UNIQUE INDEX mvp_signer_wallets_agent_identity_idx
  ON mvp_signer_wallets(agent_id, network, public_key);
CREATE INDEX mvp_signer_wallets_agent_status_idx
  ON mvp_signer_wallets(agent_id, status, updated_at DESC);
CREATE INDEX mvp_signer_requests_payment_idx
  ON mvp_signer_requests(payment_id, created_at DESC);
CREATE INDEX mvp_signer_requests_task_trace_idx
  ON mvp_signer_requests(task_id, trace_id, created_at DESC);
CREATE INDEX mvp_signer_requests_status_idx
  ON mvp_signer_requests(status, expires_at, created_at DESC);
CREATE INDEX mvp_signer_results_payment_idx
  ON mvp_signer_results(payment_id, completed_at DESC);
CREATE INDEX mvp_signer_results_status_idx
  ON mvp_signer_results(status, completed_at DESC);
CREATE INDEX mvp_signer_receipts_payment_idx
  ON mvp_signer_receipts(payment_id, created_at DESC);
CREATE INDEX mvp_signer_receipts_correlation_idx
  ON mvp_signer_receipts(correlation_id, created_at ASC, id ASC);

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

CREATE TABLE board_proposals (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  proposer TEXT NOT NULL,
  background TEXT NOT NULL,
  objective TEXT NOT NULL,
  required_budget_lamports BIGINT NOT NULL CHECK (required_budget_lamports >= 0),
  expected_return TEXT NOT NULL,
  expected_period TEXT NOT NULL,
  risks JSONB NOT NULL DEFAULT '[]'::jsonb,
  alternatives JSONB NOT NULL DEFAULT '[]'::jsonb,
  recommended_option TEXT NOT NULL,
  exit_criteria JSONB NOT NULL DEFAULT '[]'::jsonb,
  strategy_review TEXT,
  treasury_review TEXT,
  audit_review TEXT,
  owner_decision mvp_approval_decision,
  status TEXT NOT NULL DEFAULT 'SUBMITTED',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE external_action_requests (
  id TEXT PRIMARY KEY,
  task_id TEXT REFERENCES mvp_tasks(id),
  requested_by TEXT NOT NULL,
  recipient TEXT NOT NULL,
  channel mvp_external_channel NOT NULL,
  purpose TEXT NOT NULL,
  content TEXT NOT NULL,
  allowed_action_count INTEGER NOT NULL CHECK (allowed_action_count > 0),
  expires_at TIMESTAMPTZ NOT NULL,
  owner_decision mvp_approval_decision,
  status mvp_external_status NOT NULL DEFAULT 'PENDING',
  execution_count INTEGER NOT NULL DEFAULT 0 CHECK (execution_count >= 0),
  execution_result TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE policy_decisions (
  id UUID PRIMARY KEY,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  result mvp_policy_result NOT NULL,
  reason TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mvp_audit_logs (
  id TEXT PRIMARY KEY,
  event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version > 0),
  event_type TEXT NOT NULL DEFAULT 'STATE_CHANGE',
  actor TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  before_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_state JSONB NOT NULL DEFAULT '{}'::jsonb,
  policy_result mvp_policy_result NOT NULL,
  reason TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  transaction_id TEXT,
  task_id TEXT,
  run_id TEXT,
  message_id TEXT,
  payment_id TEXT,
  ledger_entry_id TEXT,
  evidence_hash TEXT,
  event_hash TEXT CHECK (event_hash IS NULL OR length(event_hash) = 64),
  previous_event_hash TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Command idempotency and the MVP outbox use TEXT aggregate IDs because the
-- Owner-Directed MVP identifiers (for example, T-001) are not UUIDs.
CREATE TABLE mvp_command_idempotency (
  idempotency_key TEXT PRIMARY KEY,
  command_name TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  -- Nullable for rolling upgrades; new writers always provide a fingerprint
  -- and legacy rows are not eligible for replay.
  request_fingerprint TEXT,
  response JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  CHECK ((response IS NULL) = (completed_at IS NULL))
);

CREATE TABLE mvp_outbox_events (
  id TEXT PRIMARY KEY,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  event_version INTEGER NOT NULL DEFAULT 1 CHECK (event_version > 0),
  idempotency_key TEXT NOT NULL UNIQUE,
  payload JSONB NOT NULL,
  transaction_id TEXT,
  delivery_status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK (delivery_status IN ('PENDING', 'IN_FLIGHT', 'PUBLISHED', 'FAILED')),
  delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK (delivery_attempts >= 0),
  last_error TEXT,
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Task-bound Agent-to-Agent messages are separate from the legacy UUID-based
-- Phase 1 table above because the Owner-Directed MVP uses text identifiers.
-- The envelope is immutable; only the lifecycle status and updated_at may
-- change through the Message Gateway.
CREATE TABLE mvp_agent_messages (
  id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL CHECK (schema_version = '1.0'),
  idempotency_key TEXT NOT NULL UNIQUE,
  nonce TEXT NOT NULL,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE CASCADE,
  run_id TEXT,
  trace_id TEXT NOT NULL,
  conversation_id TEXT,
  parent_message_id TEXT REFERENCES mvp_agent_messages(id) ON DELETE SET NULL,
  sender_agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  recipient_agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  message_type TEXT NOT NULL CHECK (
    message_type IN (
      'COMMAND', 'DELEGATION', 'REQUEST', 'RESPONSE', 'HANDOFF',
      'REVIEW_REQUEST', 'REVIEW_RESULT', 'BLOCK', 'ESCALATION',
      'DECISION_REQUEST', 'OWNER_DECISION', 'EVIDENCE'
    )
  ),
  authority_context JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(authority_context) = 'object'),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(payload) = 'object'),
  evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(evidence_refs) = 'array'),
  status TEXT NOT NULL DEFAULT 'SENT' CHECK (
    status IN ('SENT', 'DELIVERED', 'ACKNOWLEDGED', 'CONSUMED', 'REJECTED', 'EXPIRED')
  ),
  expires_at TIMESTAMPTZ NOT NULL,
  correlation_id TEXT NOT NULL,
  message_fingerprint TEXT NOT NULL CHECK (length(message_fingerprint) = 64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (sender_agent_id, nonce),
  CHECK (sender_agent_id <> recipient_agent_id)
);

-- Agent Runs store provider metadata and normalized proposals. Provider input
-- is represented by hashes so the consolidated schema never becomes a secret
-- or prompt archive; the request limits and trace references remain queryable.
CREATE TABLE mvp_agent_runs (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  task_id TEXT NOT NULL REFERENCES mvp_tasks(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES mvp_agents(id),
  provider TEXT NOT NULL CHECK (provider IN ('OPENAI', 'CODEX', 'LOCAL_SLM', 'TEST')),
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  allowed_tools JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(allowed_tools) = 'array'),
  network_scope JSONB NOT NULL DEFAULT '["OFFCHAIN"]'::jsonb
    CHECK (jsonb_typeof(network_scope) = 'array'),
  sandbox JSONB NOT NULL DEFAULT '{"enabled": true, "filesystem": "NONE", "allow_network": false}'::jsonb
    CHECK (jsonb_typeof(sandbox) = 'object'),
  limits JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(limits) = 'object'),
  input_hash TEXT NOT NULL CHECK (length(input_hash) = 64),
  system_prompt_hash TEXT NOT NULL CHECK (length(system_prompt_hash) = 64),
  status TEXT NOT NULL CHECK (
    status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'TIMED_OUT',
               'CANCELLED', 'STOPPED', 'REJECTED')
  ),
  output TEXT NOT NULL DEFAULT '',
  structured_output JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(structured_output) = 'object'),
  proposed_actions JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(proposed_actions) = 'array'),
  evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(evidence_refs) = 'array'),
  tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(tool_calls) = 'array'),
  input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
  output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
  total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (total_tokens >= 0),
  cost_microusd BIGINT NOT NULL DEFAULT 0 CHECK (cost_microusd >= 0),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  duration_ms BIGINT NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
  error_code TEXT,
  error_message TEXT,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Evidence and Memory are masked, searchable read models. They are not an
-- authority store and cannot be used as a substitute for Policy decisions.
CREATE TABLE mvp_evidence (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
  created_by TEXT NOT NULL,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('OWNER', 'AGENT')),
  source_uri TEXT,
  task_id TEXT REFERENCES mvp_tasks(id) ON DELETE SET NULL,
  run_id TEXT,
  message_id TEXT,
  review_id TEXT,
  access_scope TEXT NOT NULL CHECK (access_scope IN ('OWNER_ONLY', 'TASK', 'AGENT')),
  allowed_agent_ids JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(allowed_agent_ids) = 'array'),
  retention_until TIMESTAMPTZ,
  embedding_model TEXT,
  embedding vector,
  status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((access_scope = 'AGENT') = (jsonb_array_length(allowed_agent_ids) > 0)),
  CHECK (embedding IS NULL OR embedding_model IS NOT NULL)
);

CREATE TABLE mvp_memory_items (
  id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  memory_type TEXT NOT NULL CHECK (
    memory_type IN ('FACT', 'DECISION', 'POLICY', 'EVIDENCE', 'SUMMARY')
  ),
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
  created_by TEXT NOT NULL,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('OWNER', 'AGENT')),
  source_evidence_id TEXT REFERENCES mvp_evidence(id) ON DELETE SET NULL,
  source_uri TEXT,
  task_id TEXT REFERENCES mvp_tasks(id) ON DELETE SET NULL,
  run_id TEXT,
  message_id TEXT,
  review_id TEXT,
  access_scope TEXT NOT NULL CHECK (access_scope IN ('OWNER_ONLY', 'TASK', 'AGENT')),
  allowed_agent_ids JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(allowed_agent_ids) = 'array'),
  retention_until TIMESTAMPTZ,
  embedding_model TEXT,
  embedding vector,
  status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE', 'REVOKED', 'EXPIRED')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((access_scope = 'AGENT') = (jsonb_array_length(allowed_agent_ids) > 0)),
  CHECK (embedding IS NULL OR embedding_model IS NOT NULL)
);

CREATE INDEX mvp_tasks_status_idx ON mvp_tasks(status);
CREATE INDEX mvp_tasks_executive_idx ON mvp_tasks(assigned_executive_agent_id);
CREATE INDEX mvp_sub_tasks_parent_idx ON mvp_sub_tasks(parent_task_id);
CREATE INDEX mvp_task_acceptance_history_task_idx
  ON mvp_task_acceptance_history(task_id, created_at DESC);
CREATE INDEX mvp_reviews_task_idx ON mvp_reviews(task_id, reviewed_at DESC);
CREATE INDEX mvp_audits_task_idx ON mvp_audits(task_id, audited_at DESC);
CREATE INDEX reward_allocations_task_idx ON reward_allocations(task_id);
CREATE INDEX mvp_virtual_ledger_account_idx
  ON mvp_virtual_ledger_entries(account_id, created_at ASC, id ASC);
CREATE INDEX mvp_virtual_ledger_task_idx
  ON mvp_virtual_ledger_entries(task_id, created_at DESC);
CREATE INDEX mvp_virtual_ledger_allocation_idx
  ON mvp_virtual_ledger_entries(allocation_id, created_at DESC);
CREATE INDEX mvp_service_payments_task_created_idx
  ON mvp_service_payments(task_id, created_at DESC, id ASC);
CREATE INDEX mvp_service_payments_run_idx
  ON mvp_service_payments(run_id, created_at DESC, id ASC);
CREATE INDEX mvp_service_payments_trace_idx
  ON mvp_service_payments(trace_id, created_at DESC, id ASC);
CREATE INDEX mvp_service_payments_correlation_idx
  ON mvp_service_payments(correlation_id, created_at DESC, id ASC);
CREATE INDEX mvp_service_payments_status_idx
  ON mvp_service_payments(status, expires_at, created_at DESC);
CREATE INDEX mvp_service_payment_events_payment_idx
  ON mvp_service_payment_events(payment_id, created_at ASC, id ASC);
CREATE INDEX approval_requests_pending_idx ON approval_requests(created_at) WHERE owner_decision IS NULL;
CREATE INDEX external_action_pending_idx ON external_action_requests(created_at) WHERE owner_decision IS NULL;
CREATE INDEX mvp_audit_created_idx ON mvp_audit_logs(created_at DESC);
CREATE INDEX mvp_audit_correlation_idx ON mvp_audit_logs(correlation_id);
CREATE INDEX mvp_audit_task_created_idx ON mvp_audit_logs(task_id, created_at DESC);
CREATE INDEX mvp_audit_target_created_idx ON mvp_audit_logs(target_type, target_id, created_at DESC);
CREATE INDEX mvp_audit_event_type_idx ON mvp_audit_logs(event_type, created_at DESC);
CREATE INDEX mvp_outbox_pending_idx ON mvp_outbox_events(created_at)
  WHERE published_at IS NULL;
CREATE INDEX mvp_outbox_delivery_idx ON mvp_outbox_events(delivery_status, available_at, created_at);
CREATE INDEX mvp_agent_messages_task_created_idx
  ON mvp_agent_messages(task_id, created_at ASC, id ASC);
CREATE INDEX mvp_agent_messages_trace_idx
  ON mvp_agent_messages(trace_id, created_at ASC, id ASC);
CREATE INDEX mvp_agent_messages_conversation_idx
  ON mvp_agent_messages(conversation_id, created_at ASC, id ASC);
CREATE INDEX mvp_agent_messages_recipient_status_idx
  ON mvp_agent_messages(recipient_agent_id, status, expires_at);
CREATE INDEX mvp_agent_runs_task_started_idx
  ON mvp_agent_runs(task_id, started_at ASC, id ASC);
CREATE INDEX mvp_agent_runs_agent_started_idx
  ON mvp_agent_runs(agent_id, started_at DESC);
CREATE INDEX mvp_agent_runs_trace_idx
  ON mvp_agent_runs(trace_id, started_at ASC, id ASC);
CREATE INDEX mvp_agent_runs_status_idx
  ON mvp_agent_runs(status, started_at DESC);
CREATE INDEX mvp_evidence_task_created_idx
  ON mvp_evidence(task_id, created_at DESC, id ASC);
CREATE INDEX mvp_evidence_run_idx
  ON mvp_evidence(run_id, created_at DESC);
CREATE INDEX mvp_evidence_message_idx
  ON mvp_evidence(message_id, created_at DESC);
CREATE INDEX mvp_evidence_status_retention_idx
  ON mvp_evidence(status, retention_until, created_at DESC);
CREATE INDEX mvp_evidence_fts_idx
  ON mvp_evidence USING GIN (to_tsvector('simple', title || ' ' || content));
CREATE INDEX mvp_memory_items_task_created_idx
  ON mvp_memory_items(task_id, created_at DESC, id ASC);
CREATE INDEX mvp_memory_items_run_idx
  ON mvp_memory_items(run_id, created_at DESC);
CREATE INDEX mvp_memory_items_message_idx
  ON mvp_memory_items(message_id, created_at DESC);
CREATE INDEX mvp_memory_items_status_retention_idx
  ON mvp_memory_items(status, retention_until, created_at DESC);
CREATE INDEX mvp_memory_items_fts_idx
  ON mvp_memory_items USING GIN (to_tsvector('simple', title || ' ' || content));

-- Audit records are append-only at the database boundary as well as in the
-- application command boundary.
CREATE OR REPLACE FUNCTION reject_mvp_audit_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_audit_logs is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_audit_logs_no_update
  BEFORE UPDATE OR DELETE ON mvp_audit_logs
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_audit_mutation();

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
     OR NEW.policy_decision_id IS DISTINCT FROM OLD.policy_decision_id
     OR NEW.budget_reservation_id IS DISTINCT FROM OLD.budget_reservation_id
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

CREATE TRIGGER mvp_service_payments_identity_immutable
  BEFORE UPDATE ON mvp_service_payments
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_service_payment_identity_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_service_payment_event_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_service_payment_events is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_service_payment_events_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_service_payment_events
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_service_payment_event_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_mpp_policy_decision_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_mpp_policy_decisions is append-only';
END;
$$ LANGUAGE plpgsql;

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

CREATE TRIGGER mvp_mpp_budget_reservations_identity_immutable
  BEFORE UPDATE ON mvp_mpp_budget_reservations
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_mpp_budget_reservation_identity_mutation();

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

CREATE TRIGGER mvp_mpp_signer_authorizations_identity_immutable
  BEFORE UPDATE ON mvp_mpp_signer_authorizations
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_mpp_signer_authorization_identity_mutation();

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

CREATE TRIGGER mvp_signer_wallets_identity_immutable
  BEFORE UPDATE ON mvp_signer_wallets
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_signer_wallet_identity_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_signer_request_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_signer_requests is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_signer_requests_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_signer_requests
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_signer_request_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_signer_result_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_signer_results is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_signer_results_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_signer_results
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_signer_result_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_signer_receipt_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_signer_receipts is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_signer_receipts_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_signer_receipts
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_signer_receipt_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_mpp_client_attempt_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_mpp_client_attempts is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_mpp_client_attempts_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_mpp_client_attempts
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_mpp_client_attempt_mutation();

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

CREATE TRIGGER mvp_agent_payment_profiles_identity_immutable
  BEFORE UPDATE ON mvp_agent_payment_profiles
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_payment_profile_identity_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_agent_payment_profile_version_mutation()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_agent_payment_profile_versions is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_agent_payment_profile_versions_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_agent_payment_profile_versions
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_payment_profile_version_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_agent_message_mutation() RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.schema_version <> OLD.schema_version
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.nonce <> OLD.nonce
     OR NEW.task_id <> OLD.task_id
     OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.trace_id <> OLD.trace_id
     OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id
     OR NEW.parent_message_id IS DISTINCT FROM OLD.parent_message_id
     OR NEW.sender_agent_id <> OLD.sender_agent_id
     OR NEW.recipient_agent_id <> OLD.recipient_agent_id
     OR NEW.message_type <> OLD.message_type
     OR NEW.authority_context <> OLD.authority_context
     OR NEW.payload <> OLD.payload
     OR NEW.evidence_refs <> OLD.evidence_refs
     OR NEW.expires_at <> OLD.expires_at
     OR NEW.correlation_id <> OLD.correlation_id
     OR NEW.message_fingerprint <> OLD.message_fingerprint
     OR NEW.created_at <> OLD.created_at
  THEN
    RAISE EXCEPTION 'mvp_agent_messages envelope is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_agent_messages_envelope_immutable
  BEFORE UPDATE ON mvp_agent_messages
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_message_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_agent_message_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_agent_messages is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_agent_messages_no_delete
  BEFORE DELETE ON mvp_agent_messages
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_message_delete();

CREATE OR REPLACE FUNCTION reject_mvp_agent_run_request_mutation() RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.task_id <> OLD.task_id
     OR NEW.agent_id <> OLD.agent_id
     OR NEW.provider <> OLD.provider
     OR NEW.model <> OLD.model
     OR NEW.prompt_version <> OLD.prompt_version
     OR NEW.trace_id <> OLD.trace_id
     OR NEW.allowed_tools <> OLD.allowed_tools
     OR NEW.network_scope <> OLD.network_scope
     OR NEW.sandbox <> OLD.sandbox
     OR NEW.limits <> OLD.limits
     OR NEW.input_hash <> OLD.input_hash
     OR NEW.system_prompt_hash <> OLD.system_prompt_hash
     OR NEW.started_at <> OLD.started_at
     OR NEW.created_at <> OLD.created_at
  THEN
    RAISE EXCEPTION 'mvp_agent_runs request metadata is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_agent_runs_request_immutable
  BEFORE UPDATE ON mvp_agent_runs
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_run_request_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_agent_run_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_agent_runs is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_agent_runs_no_delete
  BEFORE DELETE ON mvp_agent_runs
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_run_delete();

CREATE OR REPLACE FUNCTION reject_mvp_evidence_content_mutation() RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.content <> OLD.content
     OR NEW.content_hash <> OLD.content_hash
     OR NEW.created_by <> OLD.created_by
     OR NEW.actor_type <> OLD.actor_type
     OR NEW.source_uri IS DISTINCT FROM OLD.source_uri
     OR NEW.task_id IS DISTINCT FROM OLD.task_id
     OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.message_id IS DISTINCT FROM OLD.message_id
     OR NEW.review_id IS DISTINCT FROM OLD.review_id
     OR NEW.created_at <> OLD.created_at
  THEN
    RAISE EXCEPTION 'mvp_evidence source and content are immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_evidence_content_immutable
  BEFORE UPDATE ON mvp_evidence
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_evidence_content_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_evidence_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_evidence is append-only; revoke or expire it instead';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_evidence_no_delete
  BEFORE DELETE ON mvp_evidence
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_evidence_delete();

CREATE OR REPLACE FUNCTION reject_mvp_memory_content_mutation() RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.memory_type <> OLD.memory_type
     OR NEW.content <> OLD.content
     OR NEW.content_hash <> OLD.content_hash
     OR NEW.created_by <> OLD.created_by
     OR NEW.actor_type <> OLD.actor_type
     OR NEW.source_evidence_id IS DISTINCT FROM OLD.source_evidence_id
     OR NEW.source_uri IS DISTINCT FROM OLD.source_uri
     OR NEW.task_id IS DISTINCT FROM OLD.task_id
     OR NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.message_id IS DISTINCT FROM OLD.message_id
     OR NEW.review_id IS DISTINCT FROM OLD.review_id
     OR NEW.created_at <> OLD.created_at
  THEN
    RAISE EXCEPTION 'mvp_memory_items source and content are immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_memory_content_immutable
  BEFORE UPDATE ON mvp_memory_items
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_memory_content_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_memory_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_memory_items is append-only; revoke or expire it instead';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER mvp_memory_no_delete
  BEFORE DELETE ON mvp_memory_items
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_memory_delete();
