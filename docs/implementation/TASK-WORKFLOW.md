# Persistent Task Command Workflow

`services/rcao/app/task_workflow.py` is the PostgreSQL command boundary for
the Owner-Directed workflow. The existing `OwnerDirectedStore` remains a
deterministic UI fixture; it is not the durable source of truth for this
workflow.

## Command boundary

Each command opens one `PostgresRepository` unit of work and performs its
domain write, Audit append, Outbox enqueue, and idempotency completion before
the transaction commits. The Outbox is only a notification boundary. It does
not execute a provider, wallet, signer, payment, or external API.

Supported command groups are:

- Owner Task creation, Executive assignment, reward budget creation, and
  Acceptance Criteria amendments while the Task is `DRAFT`.
- Executive Sub Task creation, assigned-Agent evidence submission, and
  versioned Task transitions.
- Independent Reviewer submission and independent Auditor submission.
- Owner Evaluation followed by explicit Task-completion or Reward approval.
- Request Changes, Reject, Block, Cancel, and Rework transitions with the
  existing state machine.

Agent commands call the canonical Agent Registry for active status, Task
membership, role, Risk scope, and delegation scope. An inactive, expired,
unregistered, or non-member Agent cannot advance a persistent Task.

## Safety invariants

- Owner commands require the canonical Owner identity.
- `COMPLETED` and final Reward approval require an Owner Evaluation.
- Review and Audit failures cannot create an Owner Review approval.
- Acceptance Criteria history is append-only and amendments are DRAFT-only.
- Client idempotency keys are bound to actor, command, and request fingerprint.
- Audit and Outbox writes are in the same transaction as the state change.
- Replay remains read-only and is never used as an execution path.

## Composition

Use `postgres_task_workflow(DATABASE_URL)` from the application composition
root after the database has been migrated through version 0007. HTTP handlers
should pass the request's authenticated `ActorContext` and an idempotency key
from the command header. The UI fixture is kept separate until the Console
read model is moved to PostgreSQL.
