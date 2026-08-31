-- R-CAO migration 0012: operational stop controls, telemetry, and incidents.
-- Applied migrations are immutable; future changes require another migration.

CREATE TABLE IF NOT EXISTS mvp_stop_controls (
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

CREATE TABLE IF NOT EXISTS mvp_stop_control_history (
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

CREATE TABLE IF NOT EXISTS mvp_observability_events (
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

CREATE TABLE IF NOT EXISTS mvp_incidents (
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

CREATE TABLE IF NOT EXISTS mvp_incident_timeline (
  id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL REFERENCES mvp_incidents(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  note TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS mvp_stop_controls_stopped_idx
  ON mvp_stop_controls(stopped, target, target_id);
CREATE INDEX IF NOT EXISTS mvp_stop_control_history_created_idx
  ON mvp_stop_control_history(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS mvp_stop_control_history_target_idx
  ON mvp_stop_control_history(target, target_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_observability_events_trace_idx
  ON mvp_observability_events(trace_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_observability_events_run_idx
  ON mvp_observability_events(run_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_observability_events_name_created_idx
  ON mvp_observability_events(event_name, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS mvp_incidents_status_created_idx
  ON mvp_incidents(status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS mvp_incident_timeline_incident_created_idx
  ON mvp_incident_timeline(incident_id, created_at ASC, id ASC);

CREATE OR REPLACE FUNCTION reject_mvp_stop_history_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_stop_control_history is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_stop_control_history_no_mutation
  ON mvp_stop_control_history;
CREATE TRIGGER mvp_stop_control_history_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_stop_control_history
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_stop_history_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_observability_event_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_observability_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_observability_events_no_mutation
  ON mvp_observability_events;
CREATE TRIGGER mvp_observability_events_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_observability_events
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_observability_event_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_incident_timeline_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_incident_timeline is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_incident_timeline_no_mutation
  ON mvp_incident_timeline;
CREATE TRIGGER mvp_incident_timeline_no_mutation
  BEFORE UPDATE OR DELETE ON mvp_incident_timeline
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_incident_timeline_mutation();
