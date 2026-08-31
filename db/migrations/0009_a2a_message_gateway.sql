-- R-CAO migration 0009: Task-bound A2A message gateway.
-- Applied migrations are immutable; future changes require another migration.

CREATE TABLE IF NOT EXISTS mvp_agent_messages (
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

CREATE INDEX IF NOT EXISTS mvp_agent_messages_task_created_idx
  ON mvp_agent_messages(task_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_agent_messages_trace_idx
  ON mvp_agent_messages(trace_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_agent_messages_conversation_idx
  ON mvp_agent_messages(conversation_id, created_at ASC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_agent_messages_recipient_status_idx
  ON mvp_agent_messages(recipient_agent_id, status, expires_at);

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

DROP TRIGGER IF EXISTS mvp_agent_messages_envelope_immutable
  ON mvp_agent_messages;
CREATE TRIGGER mvp_agent_messages_envelope_immutable
  BEFORE UPDATE ON mvp_agent_messages
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_message_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_agent_message_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_agent_messages is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_agent_messages_no_delete ON mvp_agent_messages;
CREATE TRIGGER mvp_agent_messages_no_delete
  BEFORE DELETE ON mvp_agent_messages
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_agent_message_delete();
