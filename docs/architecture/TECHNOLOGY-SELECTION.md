# Technology selection — Phase 1 revision

## Decision

R-CAO does not choose a single language for every concern. The authoritative
domain and Agent orchestration layer is Python, the Owner-facing console is
TypeScript, and PostgreSQL is the transactional source of truth. Rust is an
optional future adapter, not a Phase 1 requirement.

This split follows the actual risk boundary: constitutional rules and AI
workflow orchestration need fast iteration and inspectable tests, while the
console needs a mature web UI stack. A second implementation of the policy in
TypeScript would create two sources of truth, so the console only consumes
read models and calls the Control Plane.

## Selected components

| Concern | Selection | Why | Phase 1 boundary |
|---|---|---|---|
| Owner Console | Next.js + TypeScript | Server-rendered dashboard and strong UI typing | Read-only prototype; no authority logic |
| Control Plane | Python 3.12 + FastAPI + Pydantic | Clear policy/domain code, validation, async provider adapters | Canonical Policy, Task, Reward, message, and audit contracts |
| General Agent runtime | OpenAI Agents SDK for Python | Bounded loops, handoffs, guardrails, approvals, state, tracing | Optional provider; tests use deterministic runtime |
| Custom Agent loop | OpenAI Responses API | R-CAO can own routing, structured proposals, and persistence | Adapter boundary only; model output is never authority |
| Coding specialist | Codex SDK or Codex MCP | Coding-focused execution with explicit tool and review boundaries | Optional provider; no automatic production merge/deploy |
| Local SLM | vLLM or llama.cpp OpenAI-compatible endpoint | Low-cost retrieval, classification, summarization, redaction | Optional; SLM cannot approve, issue, transfer, or override |
| System of record | PostgreSQL | Transactions, relational integrity, audit queries | Tasks, Agents, runs, messages, ledger, proposals, audit |
| Semantic search | pgvector | Keep memory and operations search beside transactional data | `memory_items.embedding` is stored; model-specific index follows evaluation |
| Observability | OpenAI tracing-compatible run metadata + audit tables | Link model/tool decisions to constitutional evidence | `task_id`, `run_id`, `trace_id`, `message_id`, model/prompt version |
| Deployment | Docker Compose in Phase 1 | Reproducible local boundary | PostgreSQL image includes pgvector; production platform is later |
| Systems language | None required | Avoid complexity without a measured requirement | Consider Rust only for a separately justified hot path or isolation boundary |

## Agent provider policy

Provider choice is a runtime configuration, not an authority decision. The
Control Plane records the provider, model, prompt version, tool allow-list, and
run identifiers before accepting a result. A provider may return:

1. text or structured analysis;
2. evidence references;
3. proposed actions or a handoff request.

It may not directly mutate the ledger, approve a Treasury proposal, issue a
formal Task, or create an Agent. Those actions are validated by the Python
Policy Engine and, where required, require an Owner decision.

The initial provider modes are:

- `TEST`: deterministic, offline, used by CI;
- `OPENAI`: OpenAI Agents SDK or Responses API;
- `CODEX`: coding-focused Codex SDK/MCP execution;
- `LOCAL_SLM`: OpenAI-compatible local endpoint.

No API key is needed for the tests. Provider packages are optional dependencies
so the core policy suite remains reproducible offline.

## Agent-to-Agent communication and search

The formal message envelope contains `task_id` plus `run_id`, `trace_id`,
`conversation_id`, `parent_message_id`, an idempotency key, authority scope,
evidence references, expiry, and message type. A message is a durable proposal
or request, not a capability token. Delegation can narrow authority but cannot
grant Owner-only powers. Direct asset/Reward transfer fields are rejected.

The operations read model indexes Tasks, Agent runs, messages, memory, and
audit records. The Owner Console prototype exposes filters for full-text query,
scope, `task_id`, `run_id`, `agent_id`, and status. PostgreSQL/pgvector is the
planned backing store; the in-memory Python search implementation exists only to
fix the API shape and test filtering before the repository adapter is added.

## Reconsideration triggers

Re-evaluate the selection only with evidence such as:

- measured Python runtime contention or an isolation requirement;
- a wallet/on-chain program that needs a separate security boundary;
- a vector-search workload that exceeds PostgreSQL's operational envelope;
- provider cost, latency, or data-residency requirements that justify a local
  model as the default.

Until one of these triggers is demonstrated, adding Rust or a second policy
implementation would increase operational surface without improving the Phase 1
completion condition.

## References

- [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents)
- [OpenAI Agents SDK quickstart](https://developers.openai.com/api/docs/guides/agents/quickstart)
- [OpenAI tracing and observability](https://developers.openai.com/api/docs/guides/agents/integrations-observability)
- [OpenAI Responses API multi-agent workflows](https://developers.openai.com/api/docs/guides/responses-multi-agent)
- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [pgvector](https://github.com/pgvector/pgvector)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/)
- [llama.cpp server](https://github.com/ggml-org/llama.cpp)
