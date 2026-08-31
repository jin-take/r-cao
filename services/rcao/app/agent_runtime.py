"""Policy-bound Agent runtime and provider adapter contracts.

Providers are deliberately capability-less from the Control Plane's point of
view. They receive a value-only ``ProviderRequest`` and return a structured
proposal. They never receive a repository, Task workflow, Ledger, Treasury,
wallet, signer, or command callback. The runtime validates the provider
output, enforces the request limits, and persists the Run as evidence for the
separate command boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter
from .repository import PostgresRepository, RepositoryTransaction


class ModelProvider(str, Enum):
    OPENAI = "OPENAI"
    CODEX = "CODEX"
    LOCAL_SLM = "LOCAL_SLM"
    TEST = "TEST"


class AgentRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"
    REJECTED = "REJECTED"


class RuntimeErrorBase(RuntimeError):
    """Base class for bounded runtime failures."""


class RuntimePolicyError(RuntimeErrorBase):
    """The request or provider output exceeds the constitutional boundary."""


class ProviderAdapterError(RuntimeErrorBase):
    """A provider adapter failed without producing a valid proposal."""


class ProviderTransientError(ProviderAdapterError):
    """A provider failure may be retried within the request retry budget."""


class ProviderUnavailableError(ProviderAdapterError):
    """The selected optional provider is not configured."""


class AgentRunConflict(RuntimeErrorBase):
    """A run ID or idempotency key is already bound to another request."""


class RuntimeCancelled(RuntimeErrorBase):
    """The run was cancelled before a provider result was accepted."""


class RuntimeStopped(RuntimeErrorBase):
    """The runtime stop control blocked or interrupted the run."""


class SandboxPolicy(BaseModel):
    """The minimum sandbox granted to a provider invocation."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    filesystem: Literal["NONE", "READ_ONLY_WORKSPACE"] = "NONE"
    allow_network: bool = False
    workspace_root: str | None = None

    @model_validator(mode="after")
    def validate_sandbox(self) -> "SandboxPolicy":
        if self.workspace_root and self.filesystem == "NONE":
            raise ValueError("workspace_root requires READ_ONLY_WORKSPACE")
        if not self.enabled and (self.allow_network or self.workspace_root):
            raise ValueError("disabled sandbox cannot grant network or workspace access")
        return self


FORBIDDEN_NETWORK_SCOPES = frozenset(
    {
        "MAINNET",
        "SOLANA_MAINNET",
        "CUSTOMER_ASSETS",
        "PRIVATE_KEYS",
        "SECRETS",
        "WALLET",
        "SIGNER",
    }
)

PROVIDER_NETWORK_SCOPE: dict[ModelProvider, str | None] = {
    ModelProvider.TEST: None,
    ModelProvider.OPENAI: "OPENAI_API",
    ModelProvider.CODEX: "CODEX_MCP",
    ModelProvider.LOCAL_SLM: "LOCAL_SLM",
}


class AgentRunRequest(BaseModel):
    """A fully bounded request passed to one provider adapter."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    provider: ModelProvider
    model: str = Field(min_length=1)
    input: str = Field(min_length=1)
    allowed_tools: list[str] = Field(default_factory=list)
    trace_id: str = Field(default_factory=lambda: f"trace-{uuid4().hex}")
    prompt_version: str = Field(default="unversioned", min_length=1)
    system_prompt: str = ""
    network_scope: list[str] = Field(default_factory=lambda: ["OFFCHAIN"])
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_retries: int = Field(default=0, ge=0, le=3)
    max_input_tokens: int = Field(default=16_384, gt=0, le=100_000)
    max_output_tokens: int = Field(default=4_096, gt=0, le=100_000)
    max_total_tokens: int = Field(default=20_480, gt=0, le=200_000)
    max_cost_microusd: int = Field(default=1_000_000, ge=0, le=100_000_000)
    max_tool_calls: int = Field(default=16, ge=0, le=100)
    idempotency_key: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_limits_and_scope(self) -> "AgentRunRequest":
        if self.max_total_tokens < self.max_output_tokens:
            raise ValueError("max_total_tokens must include max_output_tokens")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed_tools must not contain duplicates")
        if any(not item.strip() for item in self.allowed_tools):
            raise ValueError("allowed_tools cannot contain empty names")
        if len(set(self.network_scope)) != len(self.network_scope):
            raise ValueError("network_scope must not contain duplicates")
        normalized_scope = {item.strip().upper() for item in self.network_scope}
        if any(not item for item in normalized_scope):
            raise ValueError("network_scope cannot contain empty names")
        forbidden = normalized_scope.intersection(FORBIDDEN_NETWORK_SCOPES)
        if forbidden:
            raise ValueError(
                "runtime network scope is forbidden: " + ", ".join(sorted(forbidden))
            )
        if not self.trace_id.strip() or not self.prompt_version.strip():
            raise ValueError("trace_id and prompt_version are required")
        if self.idempotency_key is None:
            self.idempotency_key = self.run_id
        return self


@dataclass(frozen=True)
class ProviderRequest:
    """Value-only provider input; no application capability is included."""

    run_id: str
    task_id: str
    agent_id: str
    provider: ModelProvider
    model: str
    input: str
    trace_id: str
    prompt_version: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    network_scope: tuple[str, ...]
    sandbox: SandboxPolicy
    max_output_tokens: int

    @classmethod
    def from_request(cls, request: AgentRunRequest) -> "ProviderRequest":
        return cls(
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            provider=request.provider,
            model=request.model,
            input=request.input,
            trace_id=request.trace_id,
            prompt_version=request.prompt_version,
            system_prompt=request.system_prompt,
            allowed_tools=tuple(request.allowed_tools),
            network_scope=tuple(request.network_scope),
            sandbox=request.sandbox,
            max_output_tokens=request.max_output_tokens,
        )


class ProviderToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(default_factory=lambda: f"call-{uuid4().hex}")
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def normalize_total(self) -> "ProviderUsage":
        if self.total_tokens == 0:
            self.total_tokens = self.input_tokens + self.output_tokens
        return self


def _text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("output_text", "final_output", "text", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
        for key in ("content", "message"):
            if key in value:
                extracted = _text_from_value(value[key])
                if extracted:
                    return extracted
        return ""
    if isinstance(value, list):
        parts = [_text_from_value(item) for item in value]
        return "".join(item for item in parts if item)
    return ""


class ProviderOutput(BaseModel):
    """Normalized structured result returned by a provider adapter."""

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    structured_output: dict[str, Any] | None = None
    proposed_actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    tool_calls: list[ProviderToolCall] = Field(default_factory=list)
    usage: ProviderUsage = Field(default_factory=ProviderUsage)

    @classmethod
    def from_value(cls, value: Any) -> "ProviderOutput":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return cls(text=value)
            if isinstance(decoded, dict):
                return cls.from_mapping(decoded, fallback_text=value)
            return cls(text=value)
        if isinstance(value, Mapping):
            return cls.from_mapping(value)
        attrs = {
            name: getattr(value, name)
            for name in (
                "output_text",
                "final_output",
                "text",
                "content",
                "usage",
                "proposed_actions",
                "evidence_refs",
                "tool_calls",
            )
            if hasattr(value, name)
        }
        if attrs:
            return cls.from_mapping(attrs)
        if hasattr(value, "model_dump"):
            dumped = value.model_dump()
            if isinstance(dumped, Mapping):
                return cls.from_mapping(dumped)
        raise ProviderAdapterError("provider returned an unsupported result type")

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        fallback_text: str = "",
    ) -> "ProviderOutput":
        raw_text = value.get(
            "output_text",
            value.get("final_output", value.get("text", value.get("content"))),
        )
        text = _text_from_value(raw_text) or _text_from_value(value.get("choices")) or fallback_text
        structured = value.get("structured_output", value.get("output"))
        if structured is not None and not isinstance(structured, dict):
            structured = None
        if structured is None and text.strip().startswith("{"):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                structured = decoded
        usage = _usage_from_value(value.get("usage", {}))
        proposed = value.get("proposed_actions", value.get("actions", []))
        evidence = value.get("evidence_refs", [])
        tool_calls = value.get("tool_calls", [])
        return cls(
            text=text,
            structured_output=structured,
            proposed_actions=_as_dict_list(proposed, "proposed_actions"),
            evidence_refs=_as_string_list(evidence, "evidence_refs"),
            tool_calls=_as_tool_calls(tool_calls),
            usage=usage,
        )


def _as_dict_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ProviderAdapterError(f"provider field {field_name} must be a list of objects")
    return value


def _as_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProviderAdapterError(f"provider field {field_name} must be a list of strings")
    return value


def _as_tool_calls(value: Any) -> list[ProviderToolCall]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProviderAdapterError("provider field tool_calls must be a list")
    normalized: list[ProviderToolCall] = []
    for item in value:
        if isinstance(item, ProviderToolCall):
            normalized.append(item)
        elif isinstance(item, Mapping):
            normalized.append(ProviderToolCall.model_validate(item))
        else:
            raise ProviderAdapterError("provider tool_calls must contain objects")
    return normalized


def _usage_from_value(value: Any) -> ProviderUsage:
    if isinstance(value, ProviderUsage):
        return value
    if value is None:
        return ProviderUsage()
    if not isinstance(value, Mapping):
        value = {
            name: getattr(value, name)
            for name in ("input_tokens", "output_tokens", "total_tokens", "cost_microusd")
            if hasattr(value, name)
        }
    if not isinstance(value, Mapping):
        return ProviderUsage()
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
        "cost_microusd": ("cost_microusd", "cost_micro_usd"),
    }
    normalized: dict[str, int] = {}
    for target, names in aliases.items():
        for name in names:
            raw = value.get(name)
            if isinstance(raw, int) and raw >= 0:
                normalized[target] = raw
                break
    return ProviderUsage(**normalized)


def _estimate_tokens(value: str) -> int:
    if not value:
        return 0
    # A conservative provider-independent estimate used only when an adapter
    # does not report usage. It is not presented as provider billing data.
    return max(1, (len(value) + 3) // 4)


def _effective_usage(request: AgentRunRequest, output: ProviderOutput) -> ProviderUsage:
    usage = output.usage
    input_tokens = usage.input_tokens or _estimate_tokens(request.input)
    output_tokens = usage.output_tokens or _estimate_tokens(output.text)
    total_tokens = usage.total_tokens or input_tokens + output_tokens
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_microusd=usage.cost_microusd,
    )


class ProviderAdapter(Protocol):
    provider: ModelProvider

    async def complete(self, request: ProviderRequest) -> ProviderOutput:
        """Return a proposal without executing a Control Plane command."""


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _require_provider_network(request: ProviderRequest, expected: str) -> None:
    if expected not in {item.upper() for item in request.network_scope}:
        raise RuntimePolicyError(f"provider requires explicit network scope {expected}")
    if not request.sandbox.enabled or not request.sandbox.allow_network:
        raise RuntimePolicyError("provider network access requires an enabled network sandbox")


class CallableProviderAdapter:
    """Adapt an injected async or sync function without exposing app state."""

    def __init__(self, provider: ModelProvider, callback: Callable[[ProviderRequest], Any]) -> None:
        self.provider = provider
        self._callback = callback

    async def complete(self, request: ProviderRequest) -> ProviderOutput:
        return ProviderOutput.from_value(await _maybe_await(self._callback(request)))


class AgentRuntime(Protocol):
    async def run(
        self,
        request: AgentRunRequest,
        *,
        cancellation_event: asyncio.Event | None = None,
        stop_checker: Callable[[], bool | str | None] | None = None,
    ) -> "AgentRunResult":
        """Run one bounded Agent turn and return a structured proposal."""


class DeterministicTestRuntime:
    """Offline adapter used by CI; it never calls a model or external tool."""

    provider = ModelProvider.TEST

    async def complete(self, request: ProviderRequest) -> ProviderOutput:
        return ProviderOutput(
            text=f"TEST_OUTPUT:{request.agent_id}:{request.input}",
            structured_output={
                "provider": ModelProvider.TEST.value,
                "prompt_version": request.prompt_version,
            },
        )

    async def run(self, request: AgentRunRequest) -> "AgentRunResult":
        output = await self.complete(ProviderRequest.from_request(request))
        return AgentRunResult(
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            trace_id=request.trace_id,
            model=request.model,
            prompt_version=request.prompt_version,
            output=output.text,
            structured_output=output.structured_output,
            proposed_actions=output.proposed_actions,
            evidence_refs=output.evidence_refs,
            provider=ModelProvider.TEST,
        )


class OpenAIResponsesAdapter:
    """OpenAI Responses API adapter with an injected SDK client."""

    provider = ModelProvider.OPENAI

    def __init__(self, client: Any) -> None:
        self.client = client

    async def complete(self, request: ProviderRequest) -> ProviderOutput:
        _require_provider_network(request, "OPENAI_API")
        responses = getattr(self.client, "responses", None)
        create = getattr(responses, "create", None)
        if create is None:
            raise ProviderUnavailableError("OpenAI Responses client is not configured")
        kwargs: dict[str, Any] = {
            "model": request.model,
            "input": request.input,
            "max_output_tokens": request.max_output_tokens,
            "store": False,
            "metadata": {
                "rcao_run_id": request.run_id,
                "rcao_task_id": request.task_id,
                "rcao_trace_id": request.trace_id,
                "rcao_prompt_version": request.prompt_version,
            },
        }
        if request.system_prompt:
            kwargs["instructions"] = request.system_prompt
        try:
            response = await _maybe_await(create(**kwargs))
        except ProviderAdapterError:
            raise
        except Exception as exc:
            raise ProviderAdapterError("OpenAI Responses request failed") from exc
        return ProviderOutput.from_value(response)


class OpenAIAgentsAdapter:
    """OpenAI Agents SDK adapter using an injected runner facade."""

    provider = ModelProvider.OPENAI

    def __init__(self, runner: Any) -> None:
        self.runner = runner

    async def complete(self, request: ProviderRequest) -> ProviderOutput:
        _require_provider_network(request, "OPENAI_API")
        run_method = getattr(self.runner, "run", self.runner)
        if not callable(run_method):
            raise ProviderUnavailableError("OpenAI Agents runner is not configured")
        try:
            result = await _maybe_await(run_method(request))
        except Exception as exc:
            raise ProviderAdapterError("OpenAI Agents run failed") from exc
        return ProviderOutput.from_value(result)


class CodexMcpAdapter:
    """Codex SDK/MCP adapter; repository, merge, and deploy are excluded."""

    provider = ModelProvider.CODEX

    def __init__(self, runner: Any) -> None:
        self.runner = runner

    async def complete(self, request: ProviderRequest) -> ProviderOutput:
        _require_provider_network(request, "CODEX_MCP")
        run_method = getattr(self.runner, "run", self.runner)
        if not callable(run_method):
            raise ProviderUnavailableError("Codex/MCP runner is not configured")
        try:
            result = await _maybe_await(run_method(request))
        except Exception as exc:
            raise ProviderAdapterError("Codex/MCP run failed") from exc
        return ProviderOutput.from_value(result)


class OpenAICompatibleSlmAdapter:
    """OpenAI-compatible vLLM/llama.cpp adapter with explicit transport."""

    provider = ModelProvider.LOCAL_SLM

    def __init__(
        self,
        endpoint: str,
        transport: Callable[[str, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        if not endpoint.strip():
            raise ValueError("SLM endpoint is required")
        self.endpoint = endpoint
        self.transport = transport

    async def complete(self, request: ProviderRequest) -> ProviderOutput:
        _require_provider_network(request, "LOCAL_SLM")
        if self.transport is None:
            raise ProviderUnavailableError("SLM transport is not configured")
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                *(
                    [{"role": "system", "content": request.system_prompt}]
                    if request.system_prompt
                    else []
                ),
                {"role": "user", "content": request.input},
            ],
            "max_tokens": request.max_output_tokens,
            "response_format": {"type": "json_object"},
            "metadata": {
                "rcao_run_id": request.run_id,
                "rcao_task_id": request.task_id,
                "rcao_trace_id": request.trace_id,
            },
        }
        try:
            response = await _maybe_await(self.transport(self.endpoint, payload))
        except Exception as exc:
            raise ProviderAdapterError("Local SLM request failed") from exc
        if hasattr(response, "json") and callable(response.json):
            response = await _maybe_await(response.json())
        return ProviderOutput.from_value(response)


OpenAIResponsesAPIAdapter = OpenAIResponsesAdapter
OpenAICompatibleSLMAdapter = OpenAICompatibleSlmAdapter


class ProviderRegistry:
    """Closed registry of adapters selected by the composition root."""

    def __init__(self, adapters: Mapping[ModelProvider, ProviderAdapter] | None = None) -> None:
        self._adapters: dict[ModelProvider, ProviderAdapter] = {
            ModelProvider.TEST: DeterministicTestRuntime()
        }
        if adapters:
            for provider, adapter in adapters.items():
                self.register(provider, adapter)

    def register(self, provider: ModelProvider, adapter: ProviderAdapter) -> None:
        if getattr(adapter, "provider", provider) is not provider:
            raise ValueError("provider adapter identity does not match registry key")
        self._adapters[provider] = adapter

    def resolve(self, provider: ModelProvider) -> ProviderAdapter:
        try:
            return self._adapters[provider]
        except KeyError as exc:
            raise ProviderUnavailableError(
                f"provider adapter is not configured: {provider.value}"
            ) from exc


class AgentRunResult(BaseModel):
    """Provider result after runtime validation; all actions remain proposals."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_id: str | None = None
    agent_id: str | None = None
    trace_id: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    output: str
    structured_output: dict[str, Any] | None = None
    proposed_actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    tool_calls: list[ProviderToolCall] = Field(default_factory=list)
    provider: ModelProvider
    status: AgentRunStatus = AgentRunStatus.SUCCEEDED
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)
    attempts: int = Field(default=1, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    proposal_only: bool = True


class AgentRunRecord(BaseModel):
    """Persisted Run metadata and sanitized provider result."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    idempotency_key: str
    task_id: str
    agent_id: str
    provider: ModelProvider
    model: str
    prompt_version: str
    trace_id: str
    allowed_tools: list[str]
    network_scope: list[str]
    sandbox: dict[str, Any]
    limits: dict[str, Any]
    input_hash: str
    system_prompt_hash: str
    status: AgentRunStatus
    output: str = ""
    structured_output: dict[str, Any] | None = None
    proposed_actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    tool_calls: list[ProviderToolCall] = Field(default_factory=list)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_microusd: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None

    @classmethod
    def running(cls, request: AgentRunRequest, *, started_at: datetime) -> "AgentRunRecord":
        return cls(
            run_id=request.run_id,
            idempotency_key=request.idempotency_key or request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            provider=request.provider,
            model=request.model,
            prompt_version=request.prompt_version,
            trace_id=request.trace_id,
            allowed_tools=list(request.allowed_tools),
            network_scope=list(request.network_scope),
            sandbox=request.sandbox.model_dump(mode="json"),
            limits=_limits_payload(request),
            input_hash=_sha256(request.input),
            system_prompt_hash=_sha256(request.system_prompt),
            status=AgentRunStatus.RUNNING,
            started_at=started_at,
        )

    @classmethod
    def from_result(
        cls,
        request: AgentRunRequest,
        result: AgentRunResult,
        *,
        started_at: datetime,
        finished_at: datetime,
    ) -> "AgentRunRecord":
        return cls(
            run_id=request.run_id,
            idempotency_key=request.idempotency_key or request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            provider=request.provider,
            model=request.model,
            prompt_version=request.prompt_version,
            trace_id=request.trace_id,
            allowed_tools=list(request.allowed_tools),
            network_scope=list(request.network_scope),
            sandbox=request.sandbox.model_dump(mode="json"),
            limits=_limits_payload(request),
            input_hash=_sha256(request.input),
            system_prompt_hash=_sha256(request.system_prompt),
            status=result.status,
            output=result.output,
            structured_output=result.structured_output,
            proposed_actions=result.proposed_actions,
            evidence_refs=result.evidence_refs,
            tool_calls=result.tool_calls,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
            cost_microusd=result.cost_microusd,
            attempts=result.attempts,
            duration_ms=result.duration_ms,
            error_code=result.error_code,
            error_message=result.error_message,
            started_at=started_at,
            finished_at=finished_at,
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _limits_payload(request: AgentRunRequest) -> dict[str, Any]:
    return {
        "timeout_seconds": request.timeout_seconds,
        "max_retries": request.max_retries,
        "max_input_tokens": request.max_input_tokens,
        "max_output_tokens": request.max_output_tokens,
        "max_total_tokens": request.max_total_tokens,
        "max_cost_microusd": request.max_cost_microusd,
        "max_tool_calls": request.max_tool_calls,
    }


class AgentRunStore(Protocol):
    def start(self, record: AgentRunRecord) -> None:
        """Persist the immutable request metadata and RUNNING state."""

    def finish(self, record: AgentRunRecord) -> None:
        """Persist the result and terminal state."""


@dataclass
class InMemoryAgentRunStore:
    """Deterministic store for tests and local offline simulations."""

    records: dict[str, AgentRunRecord] = field(default_factory=dict)

    def start(self, record: AgentRunRecord) -> None:
        existing = self.records.get(record.run_id)
        if existing is not None:
            if existing.model_dump(mode="json", exclude={"status", "finished_at"}) != record.model_dump(mode="json", exclude={"status", "finished_at"}):
                raise AgentRunConflict("run_id is already bound to another request")
            raise AgentRunConflict("run_id has already been started")
        self.records[record.run_id] = record

    def finish(self, record: AgentRunRecord) -> None:
        if record.run_id not in self.records:
            raise AgentRunConflict("run was not started")
        self.records[record.run_id] = record

    def get(self, run_id: str) -> AgentRunRecord | None:
        return self.records.get(run_id)


RUN_RECORD_COLUMNS = (
    "id",
    "idempotency_key",
    "task_id",
    "agent_id",
    "provider",
    "model",
    "prompt_version",
    "trace_id",
    "allowed_tools",
    "network_scope",
    "sandbox",
    "limits",
    "input_hash",
    "system_prompt_hash",
    "status",
    "output",
    "structured_output",
    "proposed_actions",
    "evidence_refs",
    "tool_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_microusd",
    "attempts",
    "duration_ms",
    "error_code",
    "error_message",
    "started_at",
    "finished_at",
    "created_at",
    "updated_at",
)


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeErrorBase("stored Agent Run JSON is invalid") from exc
    return value


def _run_from_row(row: Any) -> AgentRunRecord:
    values = dict(row) if isinstance(row, Mapping) else dict(zip(RUN_RECORD_COLUMNS, row, strict=True))
    return AgentRunRecord(
        run_id=str(values["id"]),
        idempotency_key=str(values["idempotency_key"]),
        task_id=str(values["task_id"]),
        agent_id=str(values["agent_id"]),
        provider=ModelProvider(str(values["provider"])),
        model=str(values["model"]),
        prompt_version=str(values["prompt_version"]),
        trace_id=str(values["trace_id"]),
        allowed_tools=_json_value(values["allowed_tools"], []),
        network_scope=_json_value(values["network_scope"], []),
        sandbox=_json_value(values["sandbox"], {}),
        limits=_json_value(values["limits"], {}),
        input_hash=str(values["input_hash"]),
        system_prompt_hash=str(values["system_prompt_hash"]),
        status=AgentRunStatus(str(values["status"])),
        output=str(values.get("output") or ""),
        structured_output=_json_value(values.get("structured_output"), None),
        proposed_actions=_json_value(values.get("proposed_actions"), []),
        evidence_refs=_json_value(values.get("evidence_refs"), []),
        tool_calls=_json_value(values.get("tool_calls"), []),
        input_tokens=int(values.get("input_tokens") or 0),
        output_tokens=int(values.get("output_tokens") or 0),
        total_tokens=int(values.get("total_tokens") or 0),
        cost_microusd=int(values.get("cost_microusd") or 0),
        attempts=int(values.get("attempts") or 0),
        duration_ms=int(values.get("duration_ms") or 0),
        error_code=values.get("error_code"),
        error_message=values.get("error_message"),
        started_at=values["started_at"],
        finished_at=values.get("finished_at"),
    )


class AgentRunRepository:
    """PostgreSQL adapter for Run metadata and terminal provider results."""

    def __init__(self, transaction: RepositoryTransaction) -> None:
        self.transaction = transaction

    def _get_by_idempotency(self, key: str, *, for_update: bool = False) -> AgentRunRecord | None:
        lock = " FOR UPDATE" if for_update else ""
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(RUN_RECORD_COLUMNS)}
            FROM mvp_agent_runs
            WHERE idempotency_key = %s{lock}
            """,
            (key,),
        )
        return _run_from_row(row) if row is not None else None

    def start(self, record: AgentRunRecord) -> None:
        existing = self._get_by_idempotency(record.idempotency_key, for_update=True)
        if existing is not None:
            if existing.run_id != record.run_id:
                raise AgentRunConflict("idempotency key is bound to another Agent Run")
            raise AgentRunConflict("Agent Run has already been started")
        self.transaction.execute(
            """
            INSERT INTO mvp_agent_runs
              (id, idempotency_key, task_id, agent_id, provider, model,
                    prompt_version, trace_id, allowed_tools, network_scope, sandbox,
               limits, input_hash, system_prompt_hash, status, started_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
            """,
            (
                record.run_id,
                record.idempotency_key,
                record.task_id,
                record.agent_id,
                record.provider.value,
                record.model,
                record.prompt_version,
                record.trace_id,
                json.dumps(record.allowed_tools, ensure_ascii=False),
                json.dumps(record.network_scope, ensure_ascii=False),
                json.dumps(record.sandbox, ensure_ascii=False, sort_keys=True),
                json.dumps(record.limits, ensure_ascii=False, sort_keys=True),
                record.input_hash,
                record.system_prompt_hash,
                record.status.value,
                record.started_at,
            ),
        )
        self._emit(
            record,
            action="START_AGENT_RUN",
            before={},
            after=record.model_dump(mode="json"),
            reason="Policy-bound Agent Run started",
            idempotency_key=f"agent-run-start:{record.run_id}",
        )

    def finish(self, record: AgentRunRecord) -> None:
        existing = self._get_by_idempotency(record.idempotency_key, for_update=True)
        if existing is None:
            raise AgentRunConflict("Agent Run was not started")
        if existing.run_id != record.run_id:
            raise AgentRunConflict("idempotency key is bound to another Agent Run")
        row = self.transaction.fetch_one(
            f"""
            UPDATE mvp_agent_runs
            SET status = %s,
                output = %s,
                structured_output = %s::jsonb,
                proposed_actions = %s::jsonb,
                evidence_refs = %s::jsonb,
                tool_calls = %s::jsonb,
                input_tokens = %s,
                output_tokens = %s,
                total_tokens = %s,
                cost_microusd = %s,
                attempts = %s,
                duration_ms = %s,
                error_code = %s,
                error_message = %s,
                finished_at = %s,
                updated_at = now()
            WHERE id = %s AND status = 'RUNNING'
            RETURNING {', '.join(RUN_RECORD_COLUMNS)}
            """,
            (
                record.status.value,
                record.output,
                json.dumps(record.structured_output or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(record.proposed_actions, ensure_ascii=False, sort_keys=True),
                json.dumps(record.evidence_refs, ensure_ascii=False),
                json.dumps([item.model_dump(mode="json") for item in record.tool_calls], ensure_ascii=False, sort_keys=True),
                record.input_tokens,
                record.output_tokens,
                record.total_tokens,
                record.cost_microusd,
                record.attempts,
                record.duration_ms,
                record.error_code,
                record.error_message,
                record.finished_at,
                record.run_id,
            ),
        )
        if row is None:
            raise AgentRunConflict("Agent Run is not in RUNNING state")
        updated = _run_from_row(row)
        self._emit(
            updated,
            action="FINISH_AGENT_RUN",
            before=existing.model_dump(mode="json"),
            after=updated.model_dump(mode="json"),
            reason=f"Agent Run finished with status {updated.status.value}",
            idempotency_key=f"agent-run-finish:{updated.run_id}:{updated.status.value}",
        )

    def get(self, run_id: str) -> AgentRunRecord | None:
        row = self.transaction.fetch_one(
            f"SELECT {', '.join(RUN_RECORD_COLUMNS)} FROM mvp_agent_runs WHERE id = %s",
            (run_id,),
        )
        return _run_from_row(row) if row is not None else None

    def list(
        self,
        *,
        task_id: str | None = None,
        agent_id: str | None = None,
        trace_id: str | None = None,
        status: AgentRunStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AgentRunRecord, ...]:
        if not 1 <= limit <= 1000:
            raise RuntimeErrorBase("limit must be between 1 and 1000")
        if offset < 0:
            raise RuntimeErrorBase("offset cannot be negative")
        predicates: list[str] = []
        params: list[Any] = []
        for column, value in (("task_id", task_id), ("agent_id", agent_id), ("trace_id", trace_id)):
            if value is not None:
                predicates.append(f"{column} = %s")
                params.append(value)
        if status is not None:
            predicates.append("status = %s")
            params.append(status.value)
        where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        rows = self.transaction.fetch_all(
            f"""
            SELECT {', '.join(RUN_RECORD_COLUMNS)}
            FROM mvp_agent_runs
            {where}
            ORDER BY started_at ASC, id ASC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        return tuple(_run_from_row(row) for row in rows)

    def _emit(
        self,
        record: AgentRunRecord,
        *,
        action: str,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        reason: str,
        idempotency_key: str,
    ) -> None:
        correlation_id = record.trace_id
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                event_version=1,
                event_type="AGENT_RUN",
                actor_id=record.agent_id,
                actor_type="AGENT",
                action=action,
                target_type="AGENT_RUN",
                target_id=record.run_id,
                before_state=before,
                after_state=after,
                policy_result="ALLOW",
                reason=reason,
                correlation_id=correlation_id,
                transaction_id=correlation_id,
                task_id=record.task_id,
                run_id=record.run_id,
            ),
        )
        OutboxWriter.enqueue(
            self.transaction,
            OutboxEvent(
                event_id=f"outbox-{uuid4().hex}",
                aggregate_type="AGENT_RUN",
                aggregate_id=record.run_id,
                event_type=action,
                idempotency_key=idempotency_key,
                payload={
                    "action": action,
                    "run": record.model_dump(mode="json"),
                    "correlation_id": correlation_id,
                },
                event_version=1,
                transaction_id=correlation_id,
            ),
        )


class PersistentAgentRunStore:
    """Application facade that keeps each Run write in one PostgreSQL UoW."""

    def __init__(self, repository: PostgresRepository) -> None:
        self.repository = repository

    def start(self, record: AgentRunRecord) -> None:
        self.repository.run(lambda tx: AgentRunRepository(tx).start(record))

    def finish(self, record: AgentRunRecord) -> None:
        self.repository.run(lambda tx: AgentRunRepository(tx).finish(record))

    def get(self, run_id: str) -> AgentRunRecord | None:
        return self.repository.run(lambda tx: AgentRunRepository(tx).get(run_id))


class PolicyBoundAgentRuntime:
    """Run one provider turn under explicit limits and stop controls."""

    def __init__(
        self,
        providers: ProviderRegistry | None = None,
        *,
        store: AgentRunStore | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.providers = providers or ProviderRegistry()
        self.store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic

    async def run(
        self,
        request: AgentRunRequest,
        *,
        cancellation_event: asyncio.Event | None = None,
        stop_checker: Callable[[], bool | str | None] | None = None,
    ) -> AgentRunResult:
        started_at = _as_utc(self._clock())
        started_tick = self._monotonic()
        running = AgentRunRecord.running(request, started_at=started_at)
        if self.store is not None:
            self.store.start(running)

        attempts = 0
        try:
            self._validate_request(request)
            self._check_controls(cancellation_event, stop_checker)
            adapter = self.providers.resolve(request.provider)
            if getattr(adapter, "provider", request.provider) is not request.provider:
                raise RuntimePolicyError("provider adapter identity does not match request")
            provider_request = ProviderRequest.from_request(request)

            while True:
                attempts += 1
                try:
                    self._check_controls(cancellation_event, stop_checker)
                    output = await self._invoke(
                        adapter,
                        provider_request,
                        timeout_seconds=request.timeout_seconds,
                        cancellation_event=cancellation_event,
                    )
                    self._check_controls(cancellation_event, stop_checker)
                    output = ProviderOutput.from_value(output)
                    self._validate_output(request, output)
                    result = self._success_result(request, output, attempts, started_tick)
                    break
                except RuntimeCancelled:
                    raise
                except RuntimeStopped:
                    raise
                except asyncio.TimeoutError as exc:
                    if attempts <= request.max_retries:
                        continue
                    raise exc
                except ProviderTransientError:
                    if attempts <= request.max_retries:
                        continue
                    raise
        except RuntimeCancelled as exc:
            result = self._error_result(
                request, AgentRunStatus.CANCELLED, "CANCELLED", str(exc), attempts, started_tick
            )
        except RuntimeStopped as exc:
            result = self._error_result(
                request, AgentRunStatus.STOPPED, "STOPPED", str(exc), attempts, started_tick
            )
        except asyncio.TimeoutError:
            result = self._error_result(
                request,
                AgentRunStatus.TIMED_OUT,
                "TIMEOUT",
                "provider deadline exceeded",
                attempts,
                started_tick,
            )
        except RuntimePolicyError as exc:
            result = self._error_result(
                request,
                AgentRunStatus.REJECTED,
                "POLICY_REJECTED",
                str(exc),
                attempts,
                started_tick,
            )
        except ProviderUnavailableError as exc:
            result = self._error_result(
                request,
                AgentRunStatus.FAILED,
                "PROVIDER_UNAVAILABLE",
                str(exc),
                attempts,
                started_tick,
            )
        except ProviderAdapterError as exc:
            result = self._error_result(
                request,
                AgentRunStatus.FAILED,
                "PROVIDER_ERROR",
                str(exc),
                attempts,
                started_tick,
            )
        except Exception as exc:
            result = self._error_result(
                request,
                AgentRunStatus.FAILED,
                "RUNTIME_ERROR",
                type(exc).__name__,
                attempts,
                started_tick,
            )

        if self.store is not None:
            finished_at = _as_utc(self._clock())
            self.store.finish(
                AgentRunRecord.from_result(
                    request,
                    result,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
        return result

    @staticmethod
    def _validate_request(request: AgentRunRequest) -> None:
        if not request.sandbox.enabled:
            raise RuntimePolicyError("provider execution requires an enabled sandbox")
        required_scope = PROVIDER_NETWORK_SCOPE[request.provider]
        if required_scope is not None:
            _require_provider_network(ProviderRequest.from_request(request), required_scope)
        if _contains_sensitive_material(request.input) or _contains_sensitive_material(request.system_prompt):
            raise RuntimePolicyError("provider input contains secret or wallet material")

    @staticmethod
    def _check_controls(
        cancellation_event: asyncio.Event | None,
        stop_checker: Callable[[], bool | str | None] | None,
    ) -> None:
        if cancellation_event is not None and cancellation_event.is_set():
            raise RuntimeCancelled("run cancellation requested")
        if stop_checker is not None:
            stopped = stop_checker()
            if isinstance(stopped, str) and stopped:
                raise RuntimeStopped(stopped)
            if stopped is True:
                raise RuntimeStopped("run stop requested")

    async def _invoke(
        self,
        adapter: ProviderAdapter,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
        cancellation_event: asyncio.Event | None,
    ) -> ProviderOutput:
        provider_task = asyncio.create_task(adapter.complete(request))
        cancellation_task: asyncio.Task[bool] | None = None
        try:
            wait_tasks: set[asyncio.Task[Any]] = {provider_task}
            if cancellation_event is not None:
                cancellation_task = asyncio.create_task(cancellation_event.wait())
                wait_tasks.add(cancellation_task)
            done, _ = await asyncio.wait(
                wait_tasks,
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                provider_task.cancel()
                await asyncio.gather(provider_task, return_exceptions=True)
                raise asyncio.TimeoutError
            if cancellation_task is not None and cancellation_task in done and cancellation_event.is_set():
                provider_task.cancel()
                await asyncio.gather(provider_task, return_exceptions=True)
                raise RuntimeCancelled("run cancellation requested")
            return await provider_task
        finally:
            if cancellation_task is not None:
                cancellation_task.cancel()
                await asyncio.gather(cancellation_task, return_exceptions=True)

    @staticmethod
    def _validate_output(request: AgentRunRequest, output: ProviderOutput) -> None:
        if _contains_sensitive_material(output.model_dump(mode="json")):
            raise RuntimePolicyError("provider output contains secret or wallet material")
        if len(output.tool_calls) > request.max_tool_calls:
            raise RuntimePolicyError("provider tool call count exceeds the request limit")
        allowed = set(request.allowed_tools)
        unauthorized = {call.name for call in output.tool_calls if call.name not in allowed}
        if unauthorized:
            raise RuntimePolicyError(
                "provider requested tools outside the allowlist: "
                + ", ".join(sorted(unauthorized))
            )
        usage = _effective_usage(request, output)
        if usage.input_tokens > request.max_input_tokens:
            raise RuntimePolicyError("provider input token usage exceeds the request limit")
        if usage.output_tokens > request.max_output_tokens:
            raise RuntimePolicyError("provider output token usage exceeds the request limit")
        if usage.total_tokens > request.max_total_tokens:
            raise RuntimePolicyError("provider total token usage exceeds the request limit")
        if usage.cost_microusd > request.max_cost_microusd:
            raise RuntimePolicyError("provider cost exceeds the request limit")
        for action in output.proposed_actions:
            name = action.get("action")
            if not isinstance(name, str) or not name.strip():
                raise RuntimePolicyError("each proposed action requires an action name")
            if action.get("execute") is True or action.get("auto_execute") is True:
                raise RuntimePolicyError("provider output cannot request automatic execution")
            if not isinstance(action.get("arguments", {}), dict):
                raise RuntimePolicyError("proposed action arguments must be an object")
        if any(not item.strip() for item in output.evidence_refs):
            raise RuntimePolicyError("provider evidence_refs must contain non-empty strings")

    @staticmethod
    def _success_result(
        request: AgentRunRequest,
        output: ProviderOutput,
        attempts: int,
        started_tick: float,
    ) -> AgentRunResult:
        usage = _effective_usage(request, output)
        return AgentRunResult(
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            trace_id=request.trace_id,
            model=request.model,
            prompt_version=request.prompt_version,
            output=output.text,
            structured_output=output.structured_output,
            proposed_actions=output.proposed_actions,
            evidence_refs=output.evidence_refs,
            tool_calls=output.tool_calls,
            provider=request.provider,
            status=AgentRunStatus.SUCCEEDED,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            cost_microusd=usage.cost_microusd,
            attempts=attempts,
            duration_ms=max(0, int((time.monotonic() - started_tick) * 1000)),
        )

    @staticmethod
    def _error_result(
        request: AgentRunRequest,
        status: AgentRunStatus,
        error_code: str,
        error_message: str,
        attempts: int,
        started_tick: float,
    ) -> AgentRunResult:
        return AgentRunResult(
            run_id=request.run_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            trace_id=request.trace_id,
            model=request.model,
            prompt_version=request.prompt_version,
            output="",
            provider=request.provider,
            status=status,
            attempts=attempts,
            duration_ms=max(0, int((time.monotonic() - started_tick) * 1000)),
            error_code=error_code,
            error_message=error_message,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _contains_sensitive_material(value: Any, *, key: str | None = None) -> bool:
    normalized_key = re.sub(r"[^a-z0-9]+", "_", (key or "").lower())
    if any(
        token in normalized_key
        for token in (
            "private_key",
            "seed_phrase",
            "mnemonic",
            "password",
            "api_key",
            "access_token",
            "bearer",
            "wallet",
            "signer",
        )
    ):
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_sensitive_material(child, key=str(child_key))
            for child_key, child in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_material(child) for child in value)
    if isinstance(value, str):
        return bool(
            re.search(
                r"(?i)\b(?:private[ _-]?key|seed[ _-]?phrase|mnemonic|api[ _-]?key|access[ _-]?token|bearer\s+[A-Za-z0-9._~+/=-]+)\b",
                value,
            )
        )
    return False


def runtime_integration_notes() -> dict[str, str]:
    return {
        "openai": "Use OpenAIResponsesAdapter or OpenAIAgentsAdapter with explicit OPENAI_API egress and a sandbox.",
        "responses": "Use Responses API when R-CAO owns the custom loop and routes structured proposals.",
        "codex": "Use CodexMcpAdapter for coding-focused proposals; repository, merge, and deploy callbacks are excluded.",
        "local_slm": "Use OpenAICompatibleSlmAdapter with an explicitly injected transport for a local vLLM or llama.cpp endpoint.",
        "test": "Use DeterministicTestRuntime for offline, reproducible CI runs.",
        "policy": "Provider output is proposal-only; Policy, Task, Audit, Ledger, Treasury, and Owner approval remain outside the provider.",
    }


SafeAgentRuntime = PolicyBoundAgentRuntime
AgentRuntimeExecutor = PolicyBoundAgentRuntime
