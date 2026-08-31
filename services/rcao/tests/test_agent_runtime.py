from __future__ import annotations

import asyncio

from app.agent_runtime import (
    AgentRunStatus,
    AgentRunRequest,
    DeterministicTestRuntime,
    InMemoryAgentRunStore,
    ModelProvider,
    OpenAIResponsesAdapter,
    PolicyBoundAgentRuntime,
    ProviderOutput,
    ProviderRegistry,
    ProviderTransientError,
)


def request(**changes: object) -> AgentRunRequest:
    values: dict[str, object] = {
        "run_id": "run-001",
        "task_id": "T-001",
        "agent_id": "agent-builder",
        "provider": ModelProvider.TEST,
        "model": "deterministic-v1",
        "input": "Prepare a reviewable implementation proposal",
        "trace_id": "trace-001",
        "prompt_version": "prompt-v1",
    }
    values.update(changes)
    return AgentRunRequest(**values)


def run(runtime: PolicyBoundAgentRuntime, value: AgentRunRequest):
    return asyncio.run(runtime.run(value))


def test_test_runtime_is_reproducible_and_persists_proposal_only_result() -> None:
    store = InMemoryAgentRunStore()
    runtime = PolicyBoundAgentRuntime(store=store)
    result = run(runtime, request())

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.output == "TEST_OUTPUT:agent-builder:Prepare a reviewable implementation proposal"
    assert result.proposal_only is True
    assert store.records["run-001"].prompt_version == "prompt-v1"
    assert store.records["run-001"].limits["max_retries"] == 0
    assert store.records["run-001"].input_hash

    second = asyncio.run(DeterministicTestRuntime().run(request(run_id="run-002")))
    assert second.output == result.output


def test_tool_allowlist_and_automatic_execution_are_closed() -> None:
    class ToolAdapter:
        provider = ModelProvider.TEST

        async def complete(self, _request):
            return ProviderOutput.from_mapping(
                {
                    "text": "proposal",
                    "tool_calls": [{"name": "repo.write"}],
                    "proposed_actions": [{"action": "TRANSITION_TASK", "execute": True}],
                }
            )

    runtime = PolicyBoundAgentRuntime(
        ProviderRegistry({ModelProvider.TEST: ToolAdapter()})
    )
    result = run(runtime, request(allowed_tools=["repo.read"]))

    assert result.status is AgentRunStatus.REJECTED
    assert result.error_code == "POLICY_REJECTED"


def test_transient_provider_error_retries_only_within_bound() -> None:
    class FlakyAdapter:
        provider = ModelProvider.TEST

        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _request):
            self.calls += 1
            if self.calls == 1:
                raise ProviderTransientError("temporary provider failure")
            return ProviderOutput(text="accepted proposal")

    adapter = FlakyAdapter()
    runtime = PolicyBoundAgentRuntime(
        ProviderRegistry({ModelProvider.TEST: adapter})
    )
    result = run(runtime, request(max_retries=1))

    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.attempts == 2
    assert adapter.calls == 2


def test_timeout_and_cancellation_are_terminal_and_do_not_retry() -> None:
    class SlowAdapter:
        provider = ModelProvider.TEST

        async def complete(self, _request):
            await asyncio.sleep(1)
            return ProviderOutput(text="too late")

    runtime = PolicyBoundAgentRuntime(
        ProviderRegistry({ModelProvider.TEST: SlowAdapter()})
    )
    timed_out = run(runtime, request(timeout_seconds=0.01, max_retries=2))
    assert timed_out.status is AgentRunStatus.TIMED_OUT
    assert timed_out.attempts == 3

    async def cancelled_result():
        event = asyncio.Event()
        task = asyncio.create_task(
            runtime.run(request(run_id="run-cancel"), cancellation_event=event)
        )
        await asyncio.sleep(0.01)
        event.set()
        return await task

    cancelled = asyncio.run(cancelled_result())
    assert cancelled.status is AgentRunStatus.CANCELLED
    assert cancelled.attempts == 1


def test_external_provider_requires_explicit_egress_and_sandbox() -> None:
    called = False

    class Responses:
        async def create(self, **_kwargs):
            nonlocal called
            called = True
            return {"output_text": "proposal"}

    class Client:
        responses = Responses()

    runtime = PolicyBoundAgentRuntime(
        ProviderRegistry({ModelProvider.OPENAI: OpenAIResponsesAdapter(Client())})
    )
    rejected = run(
        runtime,
        request(
            provider=ModelProvider.OPENAI,
            network_scope=["OFFCHAIN"],
        ),
    )
    assert rejected.status is AgentRunStatus.REJECTED
    assert called is False

    accepted = run(
        runtime,
        request(
            run_id="run-openai",
            provider=ModelProvider.OPENAI,
            network_scope=["OPENAI_API"],
            sandbox={"enabled": True, "filesystem": "NONE", "allow_network": True},
        ),
    )
    assert accepted.status is AgentRunStatus.SUCCEEDED
    assert called is True


def test_structured_provider_response_is_normalized_without_sdk_dependency() -> None:
    output = ProviderOutput.from_value(
        {
            "choices": [{"message": {"content": "{\"summary\": \"ok\"}"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        }
    )

    assert output.text == '{"summary": "ok"}'
    assert output.structured_output == {"summary": "ok"}
    assert output.usage.total_tokens == 6
