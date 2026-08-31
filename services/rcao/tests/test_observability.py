import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.agent_runtime import AgentRunRequest, PolicyBoundAgentRuntime
from app.models import AgentRole
from app.observability import (
    ExecutionGate,
    IncidentManager,
    IncidentSeverity,
    IncidentStatus,
    InMemoryObservability,
    InMemoryStopControlBackend,
    StructuredLogEvent,
    StopAuthorizationError,
    StopBlockedError,
    StopController,
    StopTarget,
)
from app.policy import Phase


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def actor(
    actor_id: str,
    *,
    actor_type: str,
    role: AgentRole,
) -> SimpleNamespace:
    return SimpleNamespace(
        actor_id=actor_id,
        subject=actor_id,
        name=actor_id,
        role=role,
        actor_type=actor_type,
        phase=Phase.PHASE_1_OFFCHAIN,
        token_id=f"token-{actor_id}",
        issued_at=1,
        expires_at=9_999_999_999,
        identity_version=1,
    )


OWNER = actor("owner-local", actor_type="OWNER", role=AgentRole.OWNER)
AGENT = actor("agent-builder", actor_type="AGENT", role=AgentRole.BUILDER)


def test_owner_only_stop_is_an_execution_boundary_and_runtime_checker() -> None:
    controller = StopController(
        owner_id="owner-local",
        backend=InMemoryStopControlBackend(),
        clock=lambda: NOW,
    )
    with pytest.raises(StopAuthorizationError):
        controller.stop(
            AGENT,
            StopTarget.AGENT,
            target_id="agent-builder",
            reason="agent test stop",
        )

    stopped = controller.stop(
        OWNER,
        StopTarget.AGENT,
        target_id="agent-builder",
        reason="Owner paused the agent for investigation",
    )
    assert stopped.stopped is True
    assert controller.is_stopped(StopTarget.AGENT, "agent-builder") is True

    gate = ExecutionGate(controller)
    with pytest.raises(StopBlockedError):
        gate.assert_allowed(StopTarget.AGENT, "agent-builder")

    runtime = PolicyBoundAgentRuntime()
    result = asyncio.run(
        runtime.run(
            AgentRunRequest(
                run_id="run-stopped",
                task_id="T-001",
                agent_id="agent-builder",
                provider="TEST",
                model="deterministic",
                input="proposal",
            ),
            stop_checker=controller.runtime_checker(
                agent_id="agent-builder", provider="TEST", run_id="run-stopped"
            ),
        )
    )
    assert result.status.value == "STOPPED"

    resumed = controller.resume(
        OWNER,
        StopTarget.AGENT,
        target_id="agent-builder",
        reason="Owner completed the investigation",
    )
    assert resumed.stopped is False
    gate.assert_allowed(StopTarget.AGENT, "agent-builder")


def test_global_and_provider_stops_cover_payment_and_mpp_boundaries() -> None:
    controller = StopController(owner_id="owner-local", clock=lambda: NOW)
    controller.stop(OWNER, StopTarget.GLOBAL, reason="Emergency halt")
    for target in (StopTarget.COMMAND, StopTarget.PAYMENT, StopTarget.MPP, StopTarget.SIGNER):
        assert controller.is_stopped(target) is True
    controller.resume(OWNER, StopTarget.GLOBAL, reason="Owner approved recovery")
    controller.stop(OWNER, StopTarget.PROVIDER, target_id="OPENAI", reason="Provider incident")
    assert controller.stop_reason(StopTarget.PROVIDER, "OPENAI") == "Provider incident"
    assert controller.stop_reason(StopTarget.PROVIDER, "TEST") is None


def test_metrics_track_runs_payments_budget_and_sanitize_logs() -> None:
    telemetry = InMemoryObservability()
    telemetry.record_run(
        request_id="req-1", run_id="run-1", trace_id="trace-1", status="FAILED",
        duration_ms=20, input_tokens=2, output_tokens=3, total_tokens=5,
        cost_microusd=10, attempts=2,
    )
    telemetry.record_run(
        request_id="req-2", run_id="run-2", trace_id="trace-2", status="FAILED",
        duration_ms=25, input_tokens=2, output_tokens=3, total_tokens=5,
        cost_microusd=10, attempts=2,
    )
    telemetry.record_run(
        request_id="req-3", run_id="run-3", trace_id="trace-3", status="FAILED",
        duration_ms=30, input_tokens=2, output_tokens=3, total_tokens=5,
        cost_microusd=10, attempts=2,
    )
    for index in range(5):
        telemetry.record_payment(
            request_id=f"payment-{index}", trace_id=f"trace-payment-{index}", allowed=False
        )
    telemetry.record_budget(
        agent_id="agent-builder", spent_microusd=101, budget_microusd=100, trace_id="trace-budget"
    )
    safe = telemetry.record(
        StructuredLogEvent(event_name="test", metadata={"api_key": "secret"})
    )

    snapshot = telemetry.snapshot()
    assert snapshot["counters"]["runs_total"] == 3
    assert snapshot["counters"]["run_retries_total"] == 3
    assert snapshot["payment_rejection_rate"] == 1.0
    assert any(alert.alert_type == "BUDGET_OVERRUN" for alert in telemetry.alerts)
    assert safe.metadata["api_key"] == "[REDACTED]"


def test_incident_timeline_requires_owner_for_recovery_completion() -> None:
    manager = IncidentManager(owner_id="owner-local", clock=lambda: NOW)
    incident = manager.open(
        incident_id="incident-1",
        title="Provider failure",
        severity=IncidentSeverity.HIGH,
        summary="Provider error rate exceeded threshold",
        opened_by="system",
        correlation_id="trace-incident",
    )
    assert incident.status is IncidentStatus.OPEN
    with pytest.raises(StopAuthorizationError):
        manager.resolve(AGENT, "incident-1", reason="agent cannot close incident")
    manager.acknowledge(OWNER, "incident-1", note="Owner started recovery")
    resolved = manager.resolve(OWNER, "incident-1", reason="Offline validation passed")
    assert resolved.status is IncidentStatus.RESOLVED
    assert len(manager.get_timeline("incident-1")) == 3
    assert resolved.recovery_steps
