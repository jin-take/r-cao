CREATE TYPE agent_role AS ENUM (
  'OWNER', 'MANAGER', 'RESEARCHER', 'BUILDER', 'REVIEWER', 'TREASURY', 'AUDITOR'
);
CREATE TYPE agent_status AS ENUM ('ACTIVE', 'PAUSED', 'RETIRED');
CREATE TYPE task_state AS ENUM (
  'DRAFT', 'ISSUED', 'IN_PROGRESS', 'IN_REVIEW', 'ACCEPTED', 'REJECTED', 'REWARDED', 'CANCELLED'
);
CREATE TYPE proposal_status AS ENUM ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED');

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

CREATE TABLE ledger_entries (
  id UUID PRIMARY KEY,
  agent_id UUID NOT NULL REFERENCES agents(id),
  entry_type TEXT NOT NULL CHECK (entry_type IN ('REWARD', 'ADJUSTMENT', 'TREASURY_RETENTION')),
  amount_lamports BIGINT NOT NULL,
  source TEXT NOT NULL,
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
  action TEXT NOT NULL,
  before_state JSONB,
  after_state JSONB,
  evidence_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tasks_state_idx ON tasks(state);
CREATE INDEX assignments_agent_idx ON task_assignments(agent_id);
CREATE INDEX ledger_agent_created_idx ON ledger_entries(agent_id, created_at DESC);
CREATE INDEX audit_created_idx ON audit_logs(created_at DESC);
