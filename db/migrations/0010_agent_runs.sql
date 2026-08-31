-- R-CAO migration 0010: policy-bound Agent Run persistence.
-- Applied migrations are immutable; future changes require another migration.

CREATE TABLE IF NOT EXISTS mvp_agent_runs (
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

CREATE INDEX IF NOT EXISTS mvp_agent_runs_task_started_idx
  ON mvp_agent_runs(task_id, started_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_agent_runs_agent_started_idx
  ON mvp_agent_runs(agent_id, started_at DESC);
CREATE INDEX IF NOT EXISTS mvp_agent_runs_trace_idx
  ON mvp_agent_runs(trace_id, started_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_agent_runs_status_idx
  ON mvp_agent_runs(status, started_at DESC);

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

DROP TRIGGER IF EXISTS mvp_agent_runs_request_immutable ON mvp_agent_runs;
CREATE TRIGGER mvp_agent_runs_request_immutable
  BEFORE UPDATE ON mvp_agent_runs
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_run_request_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_agent_run_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_agent_runs is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_agent_runs_no_delete ON mvp_agent_runs;
CREATE TRIGGER mvp_agent_runs_no_delete
  BEFORE DELETE ON mvp_agent_runs
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_run_delete();
