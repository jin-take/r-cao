# A2A Message Gateway

R-CAO Agent-to-Agent communication uses a versioned, Task-bound message
envelope. The gateway is a coordination boundary, not a second command API.

## Envelope contract

Every message contains:

- schema version `1.0`;
- a unique `message_id`, `idempotency_key`, and sender-scoped `nonce`;
- `task_id`, optional `run_id`, `trace_id`, and optional conversation/parent
  references;
- registered sender and recipient Agent identities;
- a typed `message_type` and bounded JSON payload;
- optional evidence references;
- a mandatory future `expires_at`;
- an authority context that can reference only a persisted delegation;
- a server correlation ID and lifecycle status.

The gateway does not trust role, scope, budget, or expiry claims from the
payload. It resolves the sender and recipient from the canonical Agent
Registry and checks active status, Task membership, delegation parent/child,
scope, risk, budget, and expiry.

## Lifecycle

```text
SENT → DELIVERED → ACKNOWLEDGED → CONSUMED
  └──────────────→ REJECTED
  └──────────────→ EXPIRED
```

Only the registered recipient can advance a non-expired message. Terminal
states cannot be reopened. Expired messages cannot be delivered or consumed.

## Persistence boundary

`mvp_agent_messages` stores the immutable envelope and current lifecycle
status. The database prevents envelope mutation and deletion; the Gateway is
the only application path that changes status. Each accepted message and
status change appends a sanitized `AuditEvent` and an `OutboxEvent` in the
same PostgreSQL transaction.

The idempotency key and sender nonce are unique. A retry with the same request
fingerprint returns the stored message and does not append another Audit or
Outbox record. Reusing either identifier for a different request is rejected.

## Authority boundary

A2A messages can request, propose, report, hand off, review, escalate, or
submit evidence. They cannot directly execute Task transitions, Ledger or
Treasury changes, Reward approval, Authority changes, wallet operations,
payments, signatures, or external actions. Those effects remain behind the
authenticated Command API and Owner/Policy checks.
