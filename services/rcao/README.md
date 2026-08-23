# R-CAO Control Plane

This package is the canonical Python boundary for Phase 1. It owns:

- constitutional authorization and Task state transitions;
- integer-lamport virtual Reward calculation;
- formal Agent-to-Agent message validation;
- provider-neutral Agent run contracts;
- read-only operations search contract.

The Next.js console must not duplicate these rules. Model providers can propose
actions, but only this layer can validate them and persist an accepted decision.

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
