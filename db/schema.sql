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
  role TEXT NOT NULL,
  mission TEXT NOT NULL,
  responsibilities JSONB NOT NULL DEFAULT '[]'::jsonb,
  authority JSONB NOT NULL DEFAULT '[]'::jsonb,
  prohibited_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
  reports_to TEXT NOT NULL,
  agent_type mvp_agent_type NOT NULL,
  status mvp_agent_status NOT NULL DEFAULT 'ACTIVE',
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  model TEXT NOT NULL DEFAULT 'policy-bound',
  capability_hash TEXT NOT NULL,
  budget_limit_lamports BIGINT NOT NULL DEFAULT 0 CHECK (budget_limit_lamports >= 0),
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
  idempotency_key TEXT NOT NULL UNIQUE,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX mvp_tasks_status_idx ON mvp_tasks(status);
CREATE INDEX mvp_tasks_executive_idx ON mvp_tasks(assigned_executive_agent_id);
CREATE INDEX mvp_sub_tasks_parent_idx ON mvp_sub_tasks(parent_task_id);
CREATE INDEX mvp_reviews_task_idx ON mvp_reviews(task_id, reviewed_at DESC);
CREATE INDEX mvp_audits_task_idx ON mvp_audits(task_id, audited_at DESC);
CREATE INDEX reward_allocations_task_idx ON reward_allocations(task_id);
CREATE INDEX approval_requests_pending_idx ON approval_requests(created_at) WHERE owner_decision IS NULL;
CREATE INDEX external_action_pending_idx ON external_action_requests(created_at) WHERE owner_decision IS NULL;
CREATE INDEX mvp_audit_created_idx ON mvp_audit_logs(created_at DESC);
CREATE INDEX mvp_audit_correlation_idx ON mvp_audit_logs(correlation_id);
CREATE INDEX mvp_outbox_pending_idx ON mvp_outbox_events(created_at)
  WHERE published_at IS NULL;

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
