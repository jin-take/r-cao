# Policy-bound Agent Runtime

The Agent Runtime is the only composition boundary between R-CAO and a model
provider. Provider packages are optional and are never imported by the core
policy modules at startup.

## Invocation contract

```text
AgentRunRequest
  → request validation and stop check
  → ProviderRequest (value-only)
  → ProviderAdapter.complete()
  → ProviderOutput normalization
  → tool/token/cost/secret validation
  → AgentRunResult (proposal only)
```

`ProviderRequest` contains the Task, Agent, model, prompt version, trace, tool
allowlist, egress scope, sandbox, and output limit. It contains no Repository,
command callback, wallet, signer, private key, or application service object.

The result can contain text, structured output, proposed actions, Evidence
references, and planned tool calls. A planned tool call is accepted only when
its name is in the request allowlist; the runtime does not execute it. An
action proposal is never a Task transition, Ledger write, Treasury decision,
Reward payment, Authority change, wallet operation, or external side effect.

## Providers

| Provider | Adapter | Required egress |
|---|---|---|
| TEST | `DeterministicTestRuntime` | none |
| OpenAI Responses | `OpenAIResponsesAdapter` | `OPENAI_API` |
| OpenAI Agents SDK | `OpenAIAgentsAdapter` | `OPENAI_API` |
| Codex SDK/MCP | `CodexMcpAdapter` | `CODEX_MCP` |
| vLLM / llama.cpp | `OpenAICompatibleSlmAdapter` | `LOCAL_SLM` |

The SDK client or HTTP transport is injected by the deployment composition
root. This keeps network egress explicit and keeps CI offline. Every non-test
provider also requires an enabled sandbox with `allow_network=true` and the
matching egress scope. Mainnet, customer asset, wallet, signer, and secret
scopes are not valid runtime scopes.

## Limits and stop behavior

Each request persists its timeout, retry count, input/output/total token caps,
cost cap in micro-USD, and tool-call cap. Timeouts and transient provider
errors retry only within the finite `max_retries` value. Cancellation and
stop controls cancel the provider task and produce a terminal `CANCELLED` or
`STOPPED` Run; they are never retried.

`mvp_agent_runs` stores request metadata, hashes of prompt/input content,
provider usage/cost, output proposal, Evidence references, and terminal status.
Run start and finish are accompanied by sanitized Audit and Outbox records in
the same PostgreSQL transaction. Request metadata is immutable and Run rows
are append-only at the database boundary.
