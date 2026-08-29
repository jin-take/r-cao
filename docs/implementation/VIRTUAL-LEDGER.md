# Virtual Reward Ledger and Treasury

`services/rcao/app/virtual_ledger.py` is the accounting boundary for the
Owner-directed virtual Reward economy. It is intentionally not an MPP or
Solana payment adapter.

## State model

```text
Owner Evaluation → Proposed
Owner approval   → Reserved (approved amount is recorded)
Owner release    → Paid
Owner rejection  → Cancelled
```

`mvp_treasury_accounts` stores the operational balance, while
`mvp_virtual_ledger_entries` is the append-only accounting input used for
reconciliation. The invariant is:

`funded = available + reserved + paid + retained`

Reserve and payment commands lock the Treasury row, check the version and
available/reserved balance, and update the Reward allocation in the same
transaction. All commands carry an explicit virtual asset type, currency,
calculation version, and idempotency key.

## Boundaries

- Only the canonical Owner can fund, reserve, pay, or cancel virtual Rewards.
- Owner Evaluation is required before a Reward can be Approved or Paid.
- A Reward amount cannot exceed its Task budget or available Treasury balance.
- Negative values, non-integer lamports, overflow-prone retention settings, and
  duplicate idempotency keys are rejected.
- Reconciliation reports stored and calculated balances and can stop the
  workflow when they differ.
- There is no direct Agent-to-Agent Reward or asset transfer method.
- MPP Service Payments use a separate future Ledger/account type and must not
  be inserted into this Reward Ledger.

## Persistent command boundary

The PostgreSQL Task backend exposes the following Owner-only operations:

- `POST /api/v1/commands/treasury/fund` — add virtual Treasury capacity.
- `POST /api/v1/commands/approvals/{approval_id}/decision` — reserve a Reward
  only after Owner Evaluation and explicit approval.
- `POST /api/v1/commands/rewards/{allocation_id}/pay` — release a reserved
  Reward and apply integer-based Treasury retention.
- `GET /api/v1/ledger/reconcile` — recalculate the balance and fail closed if
  the stored account diverges from its append-only entries.

Each state-changing operation accepts `Idempotency-Key`; the command record,
Ledger entry, Audit event, and Outbox event are committed in the same unit of
work. The default in-memory MVP endpoints remain a UI fixture and do not share
the PostgreSQL Treasury balance.
