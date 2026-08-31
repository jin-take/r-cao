-- R-CAO migration 0011: masked Evidence and Memory read models.
-- Applied migrations are immutable; future changes require another migration.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS mvp_evidence (
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

CREATE TABLE IF NOT EXISTS mvp_memory_items (
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

CREATE INDEX IF NOT EXISTS mvp_evidence_task_created_idx
  ON mvp_evidence(task_id, created_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_evidence_run_idx
  ON mvp_evidence(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_evidence_message_idx
  ON mvp_evidence(message_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_evidence_status_retention_idx
  ON mvp_evidence(status, retention_until, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_evidence_fts_idx
  ON mvp_evidence USING GIN (to_tsvector('simple', title || ' ' || content));

CREATE INDEX IF NOT EXISTS mvp_memory_items_task_created_idx
  ON mvp_memory_items(task_id, created_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS mvp_memory_items_run_idx
  ON mvp_memory_items(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_memory_items_message_idx
  ON mvp_memory_items(message_id, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_memory_items_status_retention_idx
  ON mvp_memory_items(status, retention_until, created_at DESC);
CREATE INDEX IF NOT EXISTS mvp_memory_items_fts_idx
  ON mvp_memory_items USING GIN (to_tsvector('simple', title || ' ' || content));

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

DROP TRIGGER IF EXISTS mvp_evidence_content_immutable ON mvp_evidence;
CREATE TRIGGER mvp_evidence_content_immutable
  BEFORE UPDATE ON mvp_evidence
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_evidence_content_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_evidence_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_evidence is append-only; revoke or expire it instead';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_evidence_no_delete ON mvp_evidence;
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

DROP TRIGGER IF EXISTS mvp_memory_content_immutable ON mvp_memory_items;
CREATE TRIGGER mvp_memory_content_immutable
  BEFORE UPDATE ON mvp_memory_items
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_memory_content_mutation();

CREATE OR REPLACE FUNCTION reject_mvp_memory_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'mvp_memory_items is append-only; revoke or expire it instead';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mvp_memory_no_delete ON mvp_memory_items;
CREATE TRIGGER mvp_memory_no_delete
  BEFORE DELETE ON mvp_memory_items
  FOR EACH ROW EXECUTE FUNCTION reject_mvp_memory_delete();
