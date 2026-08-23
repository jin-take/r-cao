from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field


class ModelProvider(str, Enum):
    OPENAI = "OPENAI"
    CODEX = "CODEX"
    LOCAL_SLM = "LOCAL_SLM"
    TEST = "TEST"


class AgentRunRequest(BaseModel):
    run_id: str
    task_id: str
    agent_id: str
    provider: ModelProvider
    model: str
    input: str
    allowed_tools: list[str] = Field(default_factory=list)


class AgentRunResult(BaseModel):
    run_id: str
    output: str
    proposed_actions: list[dict] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    provider: ModelProvider


class AgentRuntime(Protocol):
    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Run one bounded Agent turn and return a structured proposal."""


class DeterministicTestRuntime:
    """Offline runtime used by tests; it never calls a model or external tool."""

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(
            run_id=request.run_id,
            output=f"TEST_OUTPUT:{request.agent_id}:{request.input}",
            provider=ModelProvider.TEST,
        )


def runtime_integration_notes() -> dict[str, str]:
    return {
        "openai": "Use openai-agents for bounded specialist Agents and handoffs.",
        "responses": "Use Responses API when R-CAO owns the custom loop and routing.",
        "codex": "Use Codex SDK or Codex MCP for coding-focused Agent work.",
        "local_slm": "Use an OpenAI-compatible vLLM or llama.cpp endpoint.",
        "policy": "Never let model output bypass the Control Plane Policy Engine.",
    }

