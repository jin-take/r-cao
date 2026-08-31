# Evidence and Memory

Evidence and Memory are reusable records correlated to `task_id`, `run_id`,
`message_id`, and `review_id`. They are read models: a search result cannot
execute a Task transition, Reward, Payment, Authority change, or external
action.

## Write boundary

Registration applies secret and PII masking before calculating `content_hash`.
The database stores only the masked content, its SHA-256 hash, and optional
embedding plus `embedding_model`. Prompt and provider inputs remain outside
these records unless the caller explicitly submits a sanitized Evidence item.

Evidence and Memory rows are append-only at the database boundary. Content,
source references, creator, and correlation fields are immutable. Lifecycle
changes use `REVOKED` or `EXPIRED` status and are recorded in Audit and
Outbox within the same PostgreSQL transaction.

## Access and retention

Each record uses one explicit scope:

- `OWNER_ONLY`: returned only to the Owner.
- `TASK`: returned to an Agent only when the Agent's canonical Task membership
  contains the record's `task_id`.
- `AGENT`: returned only to an Agent listed in `allowed_agent_ids`.

Expired records are excluded when `retention_until` is in the past. Revoked
and expired records are never returned by the default active search.

## Search

Keyword search uses PostgreSQL `tsvector`/`plainto_tsquery` indexes. Optional
embeddings use the pgvector cosine-distance operator; the embedding model is
stored with every vector so incompatible model versions can be kept separate.
The offline backend implements the same scope, retention, masking, hash, and
cosine ordering rules for deterministic tests.
