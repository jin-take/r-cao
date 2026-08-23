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
