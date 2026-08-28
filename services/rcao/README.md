# R-CAO Control Plane

This package is the canonical Python boundary for Phase 1. It owns:

- constitutional authorization and Task state transitions;
- integer-lamport virtual Reward calculation;
- formal Agent-to-Agent message validation;
- provider-neutral Agent run contracts;
- read-only operations search contract;
- API authentication, canonical Owner Identity, and request-scoped Actor Context.

The Next.js console must not duplicate these rules. Model providers can propose
actions, but only this layer can validate them and persist an accepted decision.

## Authentication boundary

Authenticated requests use a signed RCAO bearer token. The token contains an
external subject, token ID, expiry, execution phase, and identity version, but
does not carry the authoritative role. The IdentityRegistry resolves the
subject to a canonical ActorIdentity and derives the role, actor type, task
membership, and capabilities.

The Phase 1 reference implementation provides:

- GET /api/v1/auth/me for the canonical Actor Context;
- POST /api/v1/auth/policy-check for a non-mutating Policy decision;
- token expiry, revocation, identity suspension, and phase separation;
- in-memory authentication audit events without storing bearer tokens.

RCAO_AUTH_SECRET must be supplied by the runtime and contain at least 32 bytes.
It must not be committed, returned in an API response, or written to logs.
The current environment registry is intentionally minimal and configures the
Owner from RCAO_OWNER_ID / RCAO_OWNER_SUBJECT. PostgreSQL-backed identities and
external identity-provider integration remain follow-up work.

Owner-only commands and Agent commands must call authorize_actor_action before
performing state changes. Non-owner actors must provide a task_id and belong to
that Task; the existing constitutional Policy still decides whether the role
may perform the action. This endpoint does not create a Task or mutate state.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q tests
.venv/bin/uvicorn app.main:app --reload
```

For optional provider integrations:

```bash
.venv/bin/pip install -e '.[openai]'
```

`TEST` is the default CI mode. OpenAI Agents SDK / Responses API, Codex SDK or
MCP, and a local OpenAI-compatible SLM endpoint are selected explicitly and
remain behind the same `AgentRuntime` protocol. None of them can bypass the
Policy Engine or create a production asset-transfer path.
