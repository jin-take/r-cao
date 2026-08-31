"""Operational stop controls, structured telemetry, and incident timelines.

The stop controller is a policy boundary, not a dashboard flag.  Callers must
pass its checker to Agent Runtime or call ``ExecutionGate.assert_allowed``
before starting a Command, Run, Payment, MPP, Signer, Provider, or Agent
operation.  Stop and resume decisions are Owner-only and auditable.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter, sanitize
from .models import AgentRole
from .policy import POLICY_VERSION, PolicyAction, PolicyDecision, evaluate_policy
from .repository import PostgresRepository, RepositoryTransaction

if TYPE_CHECKING:
    from .auth import ActorContext
else:
    ActorContext = Any


class ObservabilityError(ValueError):
    """Base operational control error."""


class StopAuthorizationError(ObservabilityError):
    """The actor is not authorized to change a stop state."""


class StopConflictError(ObservabilityError):
    """A stop state cannot make the requested transition."""


class StopBlockedError(ObservabilityError):
    """An operation was rejected by an active stop state."""


class IncidentError(ObservabilityError):
    """An incident operation is invalid."""


class StopTarget(str, Enum):
    GLOBAL = "GLOBAL"
    COMMAND = "COMMAND"
    RUN = "RUN"
    AGENT = "AGENT"
    PROVIDER = "PROVIDER"
    MPP = "MPP"
    SIGNER = "SIGNER"
    PAYMENT = "PAYMENT"


class StopAction(str, Enum):
    STOP = "STOP"
    RESUME = "RESUME"


class StopState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: StopTarget
    target_id: str = "*"
    stopped: bool
    reason: str = Field(min_length=1, max_length=1_000)
    requested_by: str = Field(min_length=1)
    policy_version: str = POLICY_VERSION
    version: int = Field(ge=1)
    changed_at: datetime


class StopTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: StopAction
    actor_id: str = Field(min_length=1)
    actor_type: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1_000)
    policy_version: str = POLICY_VERSION
    correlation_id: str = Field(min_length=1)
    changed_at: datetime


class StopControlBackend(Protocol):
    def get(self, target: StopTarget, target_id: str) -> StopState | None: ...

    def apply(
        self,
        state: StopState,
        previous: StopState | None,
        transition: StopTransition,
    ) -> StopState: ...


@dataclass
class InMemoryStopControlBackend:
    states: dict[tuple[StopTarget, str], StopState] = field(default_factory=dict)
    history: list[StopTransition] = field(default_factory=list)

    def get(self, target: StopTarget, target_id: str) -> StopState | None:
        state = self.states.get((target, target_id))
        return state.model_copy(deep=True) if state is not None else None

    def apply(
        self,
        state: StopState,
        previous: StopState | None,
        transition: StopTransition,
    ) -> StopState:
        key = (state.target, state.target_id)
        current = self.states.get(key)
        if (current is None) != (previous is None):
            raise StopConflictError("stop state changed concurrently")
        if current is not None and previous is not None and current.version != previous.version:
            raise StopConflictError("stop state version changed concurrently")
        self.states[key] = state.model_copy(deep=True)
        self.history.append(transition)
        return state.model_copy(deep=True)


class StopController:
    """Owner-controlled stop state used by every execution boundary."""

    def __init__(
        self,
        *,
        owner_id: str = "owner-local",
        backend: StopControlBackend | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.owner_id = owner_id
        self.backend = backend or InMemoryStopControlBackend()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def stop(
        self,
        actor: ActorContext,
        target: StopTarget,
        *,
        target_id: str = "*",
        reason: str,
        correlation_id: str | None = None,
    ) -> StopState:
        self._authorize_owner(actor, PolicyAction.EMERGENCY_STOP)
        if not reason.strip():
            raise ValueError("stop reason is required")
        normalized_id = self._target_id(target, target_id)
        previous = self.backend.get(target, normalized_id)
        if previous is not None and previous.stopped:
            raise StopConflictError("target is already stopped")
        timestamp = _utc(self._clock())
        state = StopState(
            target=target,
            target_id=normalized_id,
            stopped=True,
            reason=reason,
            requested_by=actor.actor_id,
            version=(previous.version + 1 if previous else 1),
            changed_at=timestamp,
        )
        transition = StopTransition(
            action=StopAction.STOP,
            actor_id=actor.actor_id,
            actor_type=_actor_type_value(actor.actor_type),
            reason=reason,
            correlation_id=correlation_id or f"stop:{uuid4().hex}",
            changed_at=timestamp,
        )
        return self.backend.apply(state, previous, transition)

    def resume(
        self,
        actor: ActorContext,
        target: StopTarget,
        *,
        target_id: str = "*",
        reason: str,
        correlation_id: str | None = None,
    ) -> StopState:
        self._authorize_owner(actor, PolicyAction.RESUME_STOP)
        if not reason.strip():
            raise ValueError("resume reason is required")
        normalized_id = self._target_id(target, target_id)
        previous = self.backend.get(target, normalized_id)
        if previous is None or not previous.stopped:
            raise StopConflictError("target is not stopped")
        timestamp = _utc(self._clock())
        state = StopState(
            target=target,
            target_id=normalized_id,
            stopped=False,
            reason=reason,
            requested_by=actor.actor_id,
            version=previous.version + 1,
            changed_at=timestamp,
        )
        transition = StopTransition(
            action=StopAction.RESUME,
            actor_id=actor.actor_id,
            actor_type=_actor_type_value(actor.actor_type),
            reason=reason,
            correlation_id=correlation_id or f"resume:{uuid4().hex}",
            changed_at=timestamp,
        )
        return self.backend.apply(state, previous, transition)

    def get(self, target: StopTarget, target_id: str = "*") -> StopState | None:
        return self.backend.get(target, self._target_id(target, target_id))

    def stop_reason(self, target: StopTarget, target_id: str = "*") -> str | None:
        """Return a blocking reason, including GLOBAL and wildcard target stops."""

        checks = [(StopTarget.GLOBAL, "*")]
        normalized_id = self._target_id(target, target_id)
        if target is not StopTarget.GLOBAL:
            checks.extend(((target, normalized_id), (target, "*")))
        for check_target, check_id in checks:
            state = self.backend.get(check_target, check_id)
            if state is not None and state.stopped:
                return state.reason
        return None

    def is_stopped(self, target: StopTarget, target_id: str = "*") -> bool:
        return self.stop_reason(target, target_id) is not None

    def runtime_checker(
        self,
        *,
        agent_id: str,
        provider: str,
        run_id: str | None = None,
    ) -> Callable[[], str | None]:
        """Build the zero-argument callback accepted by PolicyBoundAgentRuntime."""

        def check() -> str | None:
            for target, target_id in (
                (StopTarget.RUN, run_id or "*"),
                (StopTarget.AGENT, agent_id),
                (StopTarget.PROVIDER, provider),
            ):
                reason = self.stop_reason(target, target_id)
                if reason:
                    return reason
            return self.stop_reason(StopTarget.GLOBAL)

        return check

    @staticmethod
    def _target_id(target: StopTarget, target_id: str) -> str:
        if target is StopTarget.GLOBAL:
            return "*"
        if not target_id.strip():
            raise ValueError("target_id is required")
        return target_id

    def _authorize_owner(self, actor: ActorContext, action: PolicyAction) -> None:
        if (
            _actor_type_value(actor.actor_type) != "OWNER"
            or actor.actor_id != self.owner_id
            or actor.role is not AgentRole.OWNER
        ):
            raise StopAuthorizationError("Owner authority is required")
        decision = evaluate_policy(actor.role, action, phase=actor.phase)
        if decision is not PolicyDecision.ALLOW:
            raise StopAuthorizationError(f"{action.value} is not allowed by Policy")


class ExecutionGate:
    """Small reusable guard for command and side-effect entry points."""

    def __init__(self, controller: StopController) -> None:
        self.controller = controller

    def assert_allowed(self, target: StopTarget, target_id: str = "*") -> None:
        reason = self.controller.stop_reason(target, target_id)
        if reason is not None:
            raise StopBlockedError(
                f"{target.value} operation is stopped for {target_id}: {reason}"
            )


class StructuredLogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: f"log-{uuid4().hex}")
    event_name: str = Field(min_length=1)
    request_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    actor_id: str | None = None
    status: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_microusd: int | None = Field(default=None, ge=0)
    attempts: int | None = Field(default=None, ge=0)
    metric_value: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def sanitize_metadata(self) -> "StructuredLogEvent":
        safe = sanitize(self.metadata)
        self.metadata = safe if isinstance(safe, dict) else {}
        return self


class OperationalAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(default_factory=lambda: f"alert-{uuid4().hex}")
    alert_type: str = Field(min_length=1)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    message: str = Field(min_length=1)
    observed_value: float
    threshold: float
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class InMemoryObservability:
    """Deterministic structured logs, counters, and anomaly alerts."""

    logs: list[StructuredLogEvent] = field(default_factory=list)
    alerts: list[OperationalAlert] = field(default_factory=list)
    counters: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    gauges: dict[str, float] = field(default_factory=dict)
    _latencies_ms: list[int] = field(default_factory=list)

    def record(self, event: StructuredLogEvent) -> StructuredLogEvent:
        safe = StructuredLogEvent.model_validate(event.model_dump(mode="json"))
        self.logs.append(safe)
        return safe

    def record_run(
        self,
        *,
        request_id: str,
        run_id: str,
        trace_id: str,
        status: str,
        duration_ms: int,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost_microusd: int,
        attempts: int,
    ) -> StructuredLogEvent:
        self.counters["runs_total"] += 1
        self.counters[f"runs_status_total:{status}"] += 1
        self.counters["run_retries_total"] += max(attempts - 1, 0)
        if attempts > 1:
            self.counters["runs_with_retry_total"] += 1
        if status not in {"SUCCEEDED", "COMPLETED"}:
            self.counters["runs_failed_total"] += 1
        self._latencies_ms.append(duration_ms)
        self.counters["tokens_total"] += total_tokens
        self.counters["cost_microusd_total"] += cost_microusd
        event = self.record(
            StructuredLogEvent(
                event_name="agent.run",
                request_id=request_id,
                run_id=run_id,
                trace_id=trace_id,
                status=status,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost_microusd=cost_microusd,
                attempts=attempts,
            )
        )
        retry_rate = self.counters["runs_with_retry_total"] / self.counters["runs_total"]
        if self.counters["runs_total"] >= 3 and retry_rate > 0.5:
            self._alert("ABNORMAL_RETRY_RATE", "HIGH", retry_rate, 0.5, "retry rate exceeded threshold", trace_id)
        return event

    def record_payment(
        self,
        *,
        request_id: str,
        trace_id: str,
        allowed: bool,
        amount_microusd: int = 0,
    ) -> StructuredLogEvent:
        self.counters["payments_total"] += 1
        if not allowed:
            self.counters["payment_rejections_total"] += 1
        self.counters["payment_amount_microusd_total"] += max(amount_microusd, 0)
        event = self.record(
            StructuredLogEvent(
                event_name="payment.decision",
                request_id=request_id,
                trace_id=trace_id,
                status="ALLOW" if allowed else "DENY",
                cost_microusd=max(amount_microusd, 0),
            )
        )
        rejection_rate = self.counters["payment_rejections_total"] / self.counters["payments_total"]
        if self.counters["payments_total"] >= 5 and rejection_rate > 0.5:
            self._alert("ABNORMAL_PAYMENT_REJECTION_RATE", "HIGH", rejection_rate, 0.5, "payment rejection rate exceeded threshold", trace_id)
        return event

    def record_budget(
        self,
        *,
        agent_id: str,
        spent_microusd: int,
        budget_microusd: int,
        trace_id: str | None = None,
    ) -> StructuredLogEvent:
        self.gauges[f"budget_spent_microusd:{agent_id}"] = float(spent_microusd)
        self.gauges[f"budget_limit_microusd:{agent_id}"] = float(budget_microusd)
        event = self.record(
            StructuredLogEvent(
                event_name="budget.usage",
                trace_id=trace_id,
                actor_id=agent_id,
                status="OVERRUN" if spent_microusd > budget_microusd else "WITHIN_LIMIT",
                metric_value=float(spent_microusd),
                metadata={"budget_microusd": budget_microusd},
            )
        )
        if spent_microusd > budget_microusd:
            self._alert("BUDGET_OVERRUN", "CRITICAL", float(spent_microusd), float(budget_microusd), "budget overrun detected", trace_id)
        return event

    def snapshot(self) -> dict[str, Any]:
        runs = self.counters["runs_total"]
        payments = self.counters["payments_total"]
        return {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "run_failure_rate": self.counters["runs_failed_total"] / runs if runs else 0.0,
            "run_retry_rate": self.counters["runs_with_retry_total"] / runs if runs else 0.0,
            "payment_rejection_rate": self.counters["payment_rejections_total"] / payments if payments else 0.0,
            "latency_ms_avg": sum(self._latencies_ms) / len(self._latencies_ms) if self._latencies_ms else 0.0,
            "alert_count": len(self.alerts),
        }

    def _alert(
        self,
        alert_type: str,
        severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        observed: float,
        threshold: float,
        message: str,
        trace_id: str | None,
    ) -> None:
        self.alerts.append(
            OperationalAlert(
                alert_type=alert_type,
                severity=severity,
                observed_value=observed,
                threshold=threshold,
                message=message,
                trace_id=trace_id,
            )
        )


class IncidentSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class IncidentTimelineEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(default_factory=lambda: f"incident-event-{uuid4().hex}")
    incident_id: str
    event_type: str
    actor_id: str
    note: str = Field(min_length=1, max_length=2_000)
    correlation_id: str
    created_at: datetime


class IncidentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    title: str = Field(min_length=1, max_length=300)
    severity: IncidentSeverity
    status: IncidentStatus = IncidentStatus.OPEN
    summary: str = Field(min_length=1, max_length=2_000)
    opened_by: str
    correlation_id: str
    created_at: datetime
    updated_at: datetime
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    recovery_steps: list[str] = Field(default_factory=list)


class IncidentManager:
    """In-memory incident timeline with Owner-only recovery completion."""

    def __init__(
        self,
        *,
        owner_id: str = "owner-local",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.owner_id = owner_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.incidents: dict[str, IncidentRecord] = {}
        self.timeline: dict[str, list[IncidentTimelineEntry]] = defaultdict(list)

    def open(
        self,
        *,
        incident_id: str,
        title: str,
        severity: IncidentSeverity,
        summary: str,
        opened_by: str,
        correlation_id: str,
        recovery_steps: list[str] | None = None,
    ) -> IncidentRecord:
        if incident_id in self.incidents:
            raise IncidentError("incident_id is already open")
        timestamp = _utc(self._clock())
        record = IncidentRecord(
            incident_id=incident_id,
            title=title,
            severity=severity,
            summary=summary,
            opened_by=opened_by,
            correlation_id=correlation_id,
            created_at=timestamp,
            updated_at=timestamp,
            recovery_steps=recovery_steps or default_recovery_procedure(),
        )
        self.incidents[incident_id] = record
        self.add_timeline(incident_id, event_type="OPENED", actor_id=opened_by, note=summary, correlation_id=correlation_id)
        return record.model_copy(deep=True)

    def acknowledge(self, actor: ActorContext, incident_id: str, *, note: str) -> IncidentRecord:
        self._require_owner(actor, PolicyAction.RESOLVE_INCIDENT)
        record = self._get_open(incident_id)
        updated = record.model_copy(update={"status": IncidentStatus.ACKNOWLEDGED, "updated_at": _utc(self._clock())})
        self.incidents[incident_id] = updated
        self.add_timeline(incident_id, event_type="ACKNOWLEDGED", actor_id=actor.actor_id, note=note, correlation_id=record.correlation_id)
        return updated.model_copy(deep=True)

    def resolve(self, actor: ActorContext, incident_id: str, *, reason: str) -> IncidentRecord:
        self._require_owner(actor, PolicyAction.RESOLVE_INCIDENT)
        if not reason.strip():
            raise ValueError("incident resolution reason is required")
        record = self._get_open(incident_id)
        timestamp = _utc(self._clock())
        updated = record.model_copy(
            update={
                "status": IncidentStatus.RESOLVED,
                "resolved_by": actor.actor_id,
                "resolved_at": timestamp,
                "updated_at": timestamp,
            }
        )
        self.incidents[incident_id] = updated
        self.add_timeline(incident_id, event_type="RESOLVED", actor_id=actor.actor_id, note=reason, correlation_id=record.correlation_id)
        return updated.model_copy(deep=True)

    def add_timeline(
        self,
        incident_id: str,
        *,
        event_type: str,
        actor_id: str,
        note: str,
        correlation_id: str,
    ) -> IncidentTimelineEntry:
        if incident_id not in self.incidents:
            raise IncidentError("incident does not exist")
        entry = IncidentTimelineEntry(
            incident_id=incident_id,
            event_type=event_type,
            actor_id=actor_id,
            note=note,
            correlation_id=correlation_id,
            created_at=_utc(self._clock()),
        )
        self.timeline[incident_id].append(entry)
        return entry.model_copy(deep=True)

    def get(self, incident_id: str) -> IncidentRecord:
        return self._get_open(incident_id).model_copy(deep=True)

    def get_timeline(self, incident_id: str) -> tuple[IncidentTimelineEntry, ...]:
        if incident_id not in self.incidents:
            raise IncidentError("incident does not exist")
        return tuple(item.model_copy(deep=True) for item in self.timeline[incident_id])

    def _get_open(self, incident_id: str) -> IncidentRecord:
        try:
            record = self.incidents[incident_id]
        except KeyError as exc:
            raise IncidentError("incident does not exist") from exc
        if record.status is IncidentStatus.RESOLVED:
            raise IncidentError("incident is already resolved")
        return record

    def _require_owner(self, actor: ActorContext, action: PolicyAction) -> None:
        if (
            _actor_type_value(actor.actor_type) != "OWNER"
            or actor.actor_id != self.owner_id
            or actor.role is not AgentRole.OWNER
        ):
            raise StopAuthorizationError("Owner authority is required")
        if evaluate_policy(actor.role, action, phase=actor.phase) is not PolicyDecision.ALLOW:
            raise StopAuthorizationError(f"{action.value} is not allowed by Policy")


def default_recovery_procedure() -> list[str]:
    return [
        "Confirm the incident scope and preserve the request/run/trace correlation.",
        "Keep affected Command, Provider, Payment, MPP, Signer, or Agent targets stopped.",
        "Inspect sanitized Audit, Outbox, structured logs, metrics, and budget alerts.",
        "Validate the fix in the offline or devnet environment before resuming.",
        "Have the Owner record the recovery reason and resume only the required scope.",
    ]


class PersistentStopControlBackend:
    """PostgreSQL stop state/history with Audit and Outbox in one transaction."""

    def __init__(self, transaction: RepositoryTransaction) -> None:
        self.transaction = transaction

    def get(self, target: StopTarget, target_id: str) -> StopState | None:
        row = self.transaction.fetch_one(
            """
            SELECT target, target_id, stopped, reason, requested_by,
                   policy_version, version, changed_at
            FROM mvp_stop_controls
            WHERE target = %s AND target_id = %s
            """,
            (target.value, target_id),
        )
        return _stop_state_from_row(row) if row is not None else None

    def apply(
        self,
        state: StopState,
        previous: StopState | None,
        transition: StopTransition,
    ) -> StopState:
        expected_version = previous.version if previous else 0
        if previous is None:
            row = self.transaction.fetch_one(
                """
                INSERT INTO mvp_stop_controls
                  (target, target_id, stopped, reason, requested_by,
                   policy_version, version, changed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (target, target_id) DO NOTHING
                RETURNING target, target_id, stopped, reason, requested_by,
                          policy_version, version, changed_at
                """,
                _stop_params(state),
            )
        else:
            row = self.transaction.fetch_one(
                """
                UPDATE mvp_stop_controls
                SET stopped = %s, reason = %s, requested_by = %s,
                    policy_version = %s, version = %s, changed_at = %s,
                    updated_at = now()
                WHERE target = %s AND target_id = %s AND version = %s
                RETURNING target, target_id, stopped, reason, requested_by,
                          policy_version, version, changed_at
                """,
                (
                    state.stopped,
                    state.reason,
                    state.requested_by,
                    state.policy_version,
                    state.version,
                    state.changed_at,
                    state.target.value,
                    state.target_id,
                    expected_version,
                ),
            )
        if row is None:
            raise StopConflictError("stop state changed concurrently")
        updated = _stop_state_from_row(row)
        self.transaction.execute(
            """
            INSERT INTO mvp_stop_control_history
              (id, target, target_id, action, actor_id, actor_type, reason,
               policy_version, correlation_id, version, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"stop-history-{uuid4().hex}",
                updated.target.value,
                updated.target_id,
                transition.action.value,
                transition.actor_id,
                transition.actor_type,
                transition.reason,
                transition.policy_version,
                transition.correlation_id,
                updated.version,
                transition.changed_at,
            ),
        )
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                event_version=1,
                event_type="STOP_CONTROL",
                actor_id=transition.actor_id,
                actor_type=transition.actor_type,
                action=f"{transition.action.value}_{updated.target.value}",
                target_type=updated.target.value,
                target_id=updated.target_id,
                before_state=previous.model_dump(mode="json") if previous else {},
                after_state=updated.model_dump(mode="json"),
                policy_result="ALLOW",
                reason=transition.reason,
                correlation_id=transition.correlation_id,
            ),
        )
        OutboxWriter.enqueue(
            self.transaction,
            OutboxEvent(
                event_id=f"outbox-{uuid4().hex}",
                aggregate_type="STOP_CONTROL",
                aggregate_id=f"{updated.target.value}:{updated.target_id}",
                event_type=f"{transition.action.value}_{updated.target.value}",
                idempotency_key=f"stop-control:{updated.target.value}:{updated.target_id}:{updated.version}",
                payload={"state": updated.model_dump(mode="json"), "correlation_id": transition.correlation_id},
                transaction_id=transition.correlation_id,
            ),
        )
        return updated


class PersistentObservabilityStore:
    """Transactional storage for sanitized structured operational events."""

    def __init__(self, repository: PostgresRepository) -> None:
        self.repository = repository

    def append(self, event: StructuredLogEvent) -> StructuredLogEvent:
        self.repository.run(lambda tx: self._append(tx, event))
        return event

    @staticmethod
    def _append(transaction: RepositoryTransaction, event: StructuredLogEvent) -> None:
        transaction.execute(
            """
            INSERT INTO mvp_observability_events
              (id, event_name, request_id, run_id, trace_id, actor_id, status,
               duration_ms, input_tokens, output_tokens, total_tokens,
               cost_microusd, attempts, metric_value, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s::jsonb, %s)
            """,
            (
                event.event_id,
                event.event_name,
                event.request_id,
                event.run_id,
                event.trace_id,
                event.actor_id,
                event.status,
                event.duration_ms,
                event.input_tokens,
                event.output_tokens,
                event.total_tokens,
                event.cost_microusd,
                event.attempts,
                event.metric_value,
                json.dumps(sanitize(event.metadata), ensure_ascii=False, sort_keys=True),
                event.created_at,
            ),
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _actor_type_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _stop_params(state: StopState) -> tuple[Any, ...]:
    return (
        state.target.value,
        state.target_id,
        state.stopped,
        state.reason,
        state.requested_by,
        state.policy_version,
        state.version,
        state.changed_at,
    )


def _stop_state_from_row(row: Any) -> StopState:
    if isinstance(row, Mapping):
        values = row
    else:
        values = dict(zip(("target", "target_id", "stopped", "reason", "requested_by", "policy_version", "version", "changed_at"), row, strict=True))
    return StopState(
        target=StopTarget(str(values["target"])),
        target_id=str(values["target_id"]),
        stopped=bool(values["stopped"]),
        reason=str(values["reason"]),
        requested_by=str(values["requested_by"]),
        policy_version=str(values["policy_version"]),
        version=int(values["version"]),
        changed_at=values["changed_at"],
    )
