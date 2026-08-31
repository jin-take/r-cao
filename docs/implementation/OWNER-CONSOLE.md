# Owner Console API boundary

The Next.js Owner Console is a client of the Python Control Plane. It does
not seed or mutate a local copy of Tasks, Agents, approvals, Rewards, or
Audit records.

## Connection

Run the API with an explicit authentication secret and allow only the console
origins that are actually used:

```dotenv
RCAO_AUTH_SECRET=<at-least-32-random-bytes>
RCAO_CONSOLE_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
NEXT_PUBLIC_RCAO_API_URL=http://localhost:8000
```

The browser accepts an Owner bearer token from the connection panel and keeps
it in `sessionStorage` only. The API resolves the canonical identity from the
token and rejects non-Owner identities for console reads and Owner commands.
The token is never included in the read model or Audit records.

## Read and command paths

All console reads use authenticated API routes. When
`RCAO_TASK_BACKEND=postgres` is enabled, the routes read the persisted MVP
tables through a repository transaction. In the default local reference mode,
the same API contract is served by the in-memory control-plane store; the
browser still uses the API boundary and never falls back to demo fixtures.

Owner commands use the same routes in both modes:

- create and transition a Task;
- record an Owner Evaluation;
- decide an Approval or approve a virtual Reward; and
- suspend or resume an Agent.

Commands include an `Idempotency-Key` and the UI surfaces `401`, `403`,
`409`, unavailable-service, network, and empty-state outcomes. A `409` is
shown as a conflict so the Owner can refresh the read model before retrying.

Operations search is read-only and retains `task_id`, `run_id`, `agent_id`,
and correlation references across Tasks, Runs, Messages, Evidence, Memory,
and Audit records.

## Safety boundary

The console does not display private keys, seed phrases, bearer tokens, or real
asset balances. Reward data remains a virtual ledger reference, and external
actions remain Owner-approval-gated and non-executing in the Phase 1 MVP.
