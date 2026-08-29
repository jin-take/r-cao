-- R-CAO migration 0002: Owner-Directed MVP
-- This migration adds the PR #37 MVP persistence contract.
-- Applied migrations must never be edited; add a new forward migration instead.

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
  id UUID PRIMARY KEY,
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

