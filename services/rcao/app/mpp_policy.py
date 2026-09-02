"""MPP Policy, budget reservation, approval, and signer capability boundary.

The MPP policy engine decides whether a Service Payment is allowed to reach
the application service.  It never signs, sends, or transfers an asset.  A
successful decision may create a short-lived, non-secret authorization record
that a separately deployed Signer can verify.

The module has a pure/in-memory implementation for deterministic tests and a
PostgreSQL adapter for the durable control plane.  Budget counters and
reservations are locked in a stable order so two concurrent requests cannot
reserve the same Task or daily capacity twice.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter
from .observability import StopController, StopTarget
from .payment_profile import (
    AgentPaymentProfile,
    AgentPaymentProfilePolicy,
    PaymentProfileRotationState,
    PaymentProfileStatus,
)
from .policy import (
    Phase,
    PhaseCapability,
    PolicyDecision,
    PolicyViolation,
    require_phase_capability,
)
from .repository import RepositoryTransaction


MPP_ENGINE_POLICY_VERSION = "mpp-policy-engine-v1"
MPP_SIGNER_AUTHORIZATION_TTL_SECONDS = 60
MPP_DAILY_PERIOD = "UTC"


class MppPolicyError(ValueError):
    """Base error for an MPP policy-bound operation."""


class MppBudgetError(MppPolicyError):
    """A budget reservation cannot be created or changed."""


class MppBudgetExceededError(MppBudgetError):
    """The Task or daily MPP budget has no remaining capacity."""


class MppApprovalError(MppPolicyError):
    """An Owner approval is missing or has an invalid state."""


class MppSignerAuthorizationError(MppPolicyError):
    """A Signer capability cannot be issued or used."""


class DirectSignerCallError(MppSignerAuthorizationError):
    """A caller attempted to bypass the Policy authorization boundary."""


class MppReservationStatus(str, Enum):
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class MppSignerAuthorizationStatus(str, Enum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


def _utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MppPolicyError("MPP timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _policy_result(decision: PolicyDecision) -> str:
    if decision is PolicyDecision.REQUIRE_OWNER_APPROVAL:
        return "OWNER_APPROVAL_REQUIRED"
    return decision.value.upper()


def _is_internal_recipient(recipient: str) -> bool:
    lowered = recipient.casefold()
    return lowered.startswith(
        (
            "agent:",
            "agent-",
            "owner:",
            "owner-",
            "treasury:",
            "treasury-",
            "ledger:",
            "ledger-",
        )
    )


class MppPolicyInput(BaseModel):
    """Secret-free, correlation-complete input to the MPP Policy Engine."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    payment_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)
    agent_id: str = Field(min_length=1, max_length=200)
    service_id: str = Field(min_length=1, max_length=300)
    program_id: str | None = Field(default=None, min_length=1, max_length=300)
    recipient: str = Field(min_length=1, max_length=300)
    recipient_kind: str = Field(default="SERVICE", min_length=1, max_length=40)
    network: str = Field(min_length=1, max_length=80)
    token: str = Field(min_length=1, max_length=100)
    amount_units: StrictInt = Field(gt=0)
    purpose: str = Field(min_length=1, max_length=80)
    expires_at: datetime
    phase: Phase = Phase.DEVNET
    profile: AgentPaymentProfile | None = None
    service_registered: bool = True
    profile_allows: bool = True
    task_bound: bool = True
    run_bound: bool = True
    agent_status: str = "ACTIVE"
    provider_id: str | None = Field(default=None, min_length=1, max_length=200)
    provider_status: str = "ACTIVE"
    mpp_status: str = "ACTIVE"
    signer_status: str = "ACTIVE"
    task_spent_units: StrictInt = Field(default=0, ge=0)
    daily_spent_units: StrictInt = Field(default=0, ge=0)
    budget_available_units: StrictInt | None = Field(default=None, ge=0)
    force_owner_approval: bool = False
    profile_change_requested: bool = False
    wallet_change_requested: bool = False
    network_change_requested: bool = False
    program_upgrade_requested: bool = False
    owner_authority_operation: bool = False
    reward_or_treasury_operation: bool = False
    owner_approval_id: str | None = Field(default=None, min_length=1, max_length=200)
    approval_verified: bool = False
    allowlist_exception_requires_approval: bool = True
    over_limit_requires_approval: bool = True

    @field_validator(
        "payment_id",
        "idempotency_key",
        "task_id",
        "run_id",
        "trace_id",
        "correlation_id",
        "agent_id",
        "service_id",
        "program_id",
        "recipient",
        "recipient_kind",
        "network",
        "token",
        "purpose",
        "provider_id",
        "agent_status",
        "provider_status",
        "mpp_status",
        "signer_status",
        "owner_approval_id",
    )
    @classmethod
    def reject_control_whitespace(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("MPP identifiers cannot contain control characters")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _utc(value)

    @classmethod
    def from_request(
        cls,
        request: Any,
        *,
        profile: AgentPaymentProfile | None,
        **overrides: Any,
    ) -> "MppPolicyInput":
        values = {
            "payment_id": request.payment_id,
            "idempotency_key": request.idempotency_key,
            "task_id": request.task_id,
            "run_id": request.run_id,
            "trace_id": request.trace_id,
            "correlation_id": request.correlation_id,
            "agent_id": request.agent_id,
            "service_id": request.service_id,
            "program_id": getattr(request, "program_id", None),
            "recipient": request.recipient,
            "recipient_kind": getattr(request, "recipient_kind", "SERVICE"),
            "network": getattr(request.network, "value", request.network),
            "token": request.token,
            "amount_units": request.amount_units,
            "purpose": getattr(request.purpose, "value", request.purpose),
            "expires_at": request.expires_at,
            "profile": profile,
        }
        values.update(overrides)
        return cls(**values)


class MppPolicyEvaluation(BaseModel):
    """An auditable Policy decision; it is not an execution command."""

    model_config = ConfigDict(extra="forbid")

    payment_id: str
    decision: PolicyDecision
    reason: str = Field(min_length=1, max_length=2_000)
    policy_version: str = MPP_ENGINE_POLICY_VERSION
    correlation_id: str
    task_id: str
    run_id: str
    trace_id: str
    agent_id: str
    profile_id: str | None = None
    profile_version: int | None = None
    decision_id: str | None = None
    approval_id: str | None = None
    reservation_id: str | None = None
    signer_authorization_id: str | None = None
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def can_issue_signer_authorization(self) -> bool:
        return self.decision is PolicyDecision.ALLOW


class MppBudgetCounter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    scope_type: str
    scope_id: str
    period_key: str
    limit_units: int = Field(gt=0)
    reserved_units: int = Field(default=0, ge=0)
    consumed_units: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)

    @property
    def available_units(self) -> int:
        return self.limit_units - self.reserved_units - self.consumed_units


class MppBudgetReservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_id: str
    idempotency_key: str
    payment_id: str
    agent_id: str
    task_id: str
    profile_id: str
    profile_version: int
    amount_units: int = Field(gt=0)
    daily_period: str
    status: MppReservationStatus
    correlation_id: str
    created_at: datetime
    updated_at: datetime


class MppBudgetReservationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation: MppBudgetReservation
    replayed: bool = False


class MppSignerAuthorization(BaseModel):
    """A short-lived verification record, never a private signing secret."""

    model_config = ConfigDict(extra="forbid")

    authorization_id: str
    payment_id: str
    policy_decision_id: str
    approval_id: str | None = None
    authorization_hash: str = Field(min_length=64, max_length=64)
    issued_by: str
    issued_at: datetime
    expires_at: datetime
    status: MppSignerAuthorizationStatus = MppSignerAuthorizationStatus.ISSUED

    def assert_usable(self, *, now: datetime | None = None) -> None:
        current = _utc(now or datetime.now(timezone.utc))
        if self.status is not MppSignerAuthorizationStatus.ISSUED:
            raise MppSignerAuthorizationError(
                f"Signer authorization is {self.status.value}"
            )
        if self.expires_at <= current:
            raise MppSignerAuthorizationError("Signer authorization has expired")


class MppApprovalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    payment_id: str
    decision: str
    payment_status: str
    reservation_status: MppReservationStatus | None = None
    comment: str = ""


class MppPolicyEngine:
    """Pure policy boundary with optional operational stop checking."""

    def __init__(
        self,
        *,
        stop_controller: StopController | None = None,
        stop_checker: Callable[[StopTarget, str], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        signer_ttl_seconds: int = MPP_SIGNER_AUTHORIZATION_TTL_SECONDS,
    ) -> None:
        if stop_controller is not None and stop_checker is not None:
            raise ValueError("provide stop_controller or stop_checker, not both")
        if signer_ttl_seconds < 1 or signer_ttl_seconds > 900:
            raise ValueError("signer_ttl_seconds must be between 1 and 900")
        self._stop_checker = (
            stop_checker
            or (
                lambda target, target_id: stop_controller.stop_reason(target, target_id)
                if stop_controller is not None
                else None
            )
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.signer_ttl_seconds = signer_ttl_seconds

    def evaluate(
        self,
        context: MppPolicyInput | Any | None = None,
        *,
        payment: Any | None = None,
        profile: AgentPaymentProfile | None = None,
        now: datetime | None = None,
        **values: Any,
    ) -> MppPolicyEvaluation:
        if context is not None and not isinstance(context, MppPolicyInput):
            if payment is not None:
                raise TypeError("payment was supplied twice")
            payment = context
            context = None
        if context is None:
            if payment is None:
                raise TypeError("MppPolicyInput or payment is required")
            context = MppPolicyInput.from_request(
                payment,
                profile=profile,
                **values,
            )
        elif profile is not None or payment is not None or values:
            raise TypeError("context cannot be combined with keyword policy input")

        current = _utc(now or self._clock())

        def result(
            decision: PolicyDecision,
            reason: str,
        ) -> MppPolicyEvaluation:
            return MppPolicyEvaluation(
                payment_id=context.payment_id,
                decision=decision,
                reason=reason,
                correlation_id=context.correlation_id,
                task_id=context.task_id,
                run_id=context.run_id,
                trace_id=context.trace_id,
                agent_id=context.agent_id,
                profile_id=context.profile.profile_id if context.profile else None,
                profile_version=context.profile.version if context.profile else None,
                decision_id=f"mpp-decision-{context.payment_id}",
                approval_id=context.owner_approval_id,
                evaluated_at=current,
            )

        if not context.task_bound:
            return result(PolicyDecision.DENY, "Payment is not bound to the requested Task")
        if not context.run_bound:
            return result(PolicyDecision.DENY, "Payment is not bound to the Agent Run")
        if not context.correlation_id or not context.trace_id:
            return result(PolicyDecision.DENY, "Task, Run, Trace, and correlation are required")
        if context.recipient_kind != "SERVICE":
            return result(PolicyDecision.DENY, "MPP accepts SERVICE recipients only")
        if context.recipient == context.agent_id or _is_internal_recipient(context.recipient):
            return result(
                PolicyDecision.DENY,
                "MPP Service Payment cannot target an Agent or internal authority",
            )
        if context.purpose != "SERVICE_PAYMENT":
            return result(PolicyDecision.DENY, "MPP accepts SERVICE_PAYMENT purpose only")
        if context.network not in {"LOCAL", "SOLANA_DEVNET"}:
            return result(
                PolicyDecision.DENY,
                "MPP payments are limited to local/devnet fixtures",
            )
        try:
            require_phase_capability(context.phase, PhaseCapability.MPP_DEVNET)
        except PolicyViolation:
            return result(
                PolicyDecision.DENY,
                f"MPP Service Payment is unavailable in {context.phase.value}",
            )
        if _utc(context.expires_at) <= current:
            return result(PolicyDecision.DENY, "Payment Challenge has expired")
        for field_name, status in (
            ("Agent", context.agent_status),
            ("Provider", context.provider_status),
            ("MPP", context.mpp_status),
            ("Signer", context.signer_status),
        ):
            if status.upper() != "ACTIVE":
                return result(PolicyDecision.DENY, f"{field_name} capability is stopped: {status}")
        if not context.service_registered:
            return result(
                PolicyDecision.REQUIRE_OWNER_APPROVAL,
                "Service is not registered; Owner approval is required",
            )
        for target, target_id in (
            (StopTarget.MPP, "*"),
            (StopTarget.PAYMENT, context.payment_id),
            (StopTarget.AGENT, context.agent_id),
            (StopTarget.RUN, context.run_id),
            (StopTarget.PROVIDER, context.provider_id or "*"),
            (StopTarget.SIGNER, "*"),
        ):
            reason = self._stop_checker(target, target_id)
            if reason:
                return result(
                    PolicyDecision.DENY,
                    f"{target.value} stop control is active: {reason}",
                )
        if not context.profile_allows:
            return result(
                PolicyDecision.REQUIRE_OWNER_APPROVAL,
                "Service registry requires explicit Owner approval",
            )
        if context.profile is None:
            return result(PolicyDecision.DENY, "an active Payment Profile is required")
        if context.profile.status is not PaymentProfileStatus.ACTIVE:
            return result(
                PolicyDecision.DENY,
                f"Payment Profile is not active: {context.profile.status.value}",
            )
        if context.profile.rotation_state is not PaymentProfileRotationState.CURRENT:
            return result(
                PolicyDecision.DENY,
                f"Payment Profile rotation state is not current: {context.profile.rotation_state.value}",
            )

        profile_evaluation = AgentPaymentProfilePolicy.evaluate(
            context.profile,
            agent_id=context.agent_id,
            service_id=context.service_id,
            recipient=context.recipient,
            network=context.network,
            token=context.token,
            amount_units=context.amount_units,
            purpose=context.purpose,
            expires_at=context.expires_at,
            program_id=context.program_id,
            task_spent_units=context.task_spent_units,
            daily_spent_units=context.daily_spent_units,
            now=current,
        )
        if profile_evaluation.decision is PolicyDecision.DENY:
            reason_lower = profile_evaluation.reason.casefold()
            exception_candidate = (
                "outside" in reason_lower
                or "exceeds" in reason_lower
                or "limit" in reason_lower
            )
            if (
                exception_candidate
                and (
                    context.allowlist_exception_requires_approval
                    or context.over_limit_requires_approval
                )
            ):
                if not context.approval_verified:
                    return result(PolicyDecision.REQUIRE_OWNER_APPROVAL, profile_evaluation.reason)
            else:
                return result(PolicyDecision.DENY, profile_evaluation.reason)

        approval_required = (
            context.force_owner_approval
            or context.profile_change_requested
            or context.wallet_change_requested
            or context.network_change_requested
            or context.program_upgrade_requested
            or context.owner_authority_operation
            or context.reward_or_treasury_operation
            or context.profile.risk_level.value in {"HIGH", "CRITICAL"}
            or profile_evaluation.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
        )
        if (
            context.budget_available_units is not None
            and context.amount_units > context.budget_available_units
        ):
            if not context.approval_verified:
                return result(
                    PolicyDecision.REQUIRE_OWNER_APPROVAL,
                    "MPP budget capacity requires explicit Owner approval",
                )
            approval_required = False

        if approval_required and not context.approval_verified:
            return result(
                PolicyDecision.REQUIRE_OWNER_APPROVAL,
                "MPP payment requires explicit Owner approval",
            )
        if context.approval_verified and context.owner_approval_id is None:
            return result(
                PolicyDecision.DENY,
                "verified Owner approval must include an approval identifier",
            )
        return result(PolicyDecision.ALLOW, "MPP payment is within the Policy boundary")

    def issue_signer_authorization(
        self,
        evaluation: MppPolicyEvaluation,
        *,
        now: datetime | None = None,
        issued_by: str = "mpp-policy-engine",
        approval_verified: bool = False,
    ) -> MppSignerAuthorization:
        """Create a capability handle only after an allow decision.

        This method returns metadata that a Signer may verify.  It does not
        contain a key, seed, signature, network client, or send operation.
        """

        if evaluation.decision is not PolicyDecision.ALLOW:
            raise MppSignerAuthorizationError(
                "Signer authorization is issued only for an allow decision"
            )
        current = _utc(now or self._clock())
        if evaluation.approval_id is not None and not approval_verified:
            raise MppSignerAuthorizationError(
                "Owner approval must be verified before issuing authorization"
            )
        expires_at = current + timedelta(seconds=self.signer_ttl_seconds)
        authorization_id = f"mpp-auth-{uuid4().hex}"
        payload = {
            "authorization_id": authorization_id,
            "payment_id": evaluation.payment_id,
            "policy_version": evaluation.policy_version,
            "correlation_id": evaluation.correlation_id,
            "expires_at": expires_at.isoformat(),
        }
        authorization_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        authorization = MppSignerAuthorization(
            authorization_id=authorization_id,
            payment_id=evaluation.payment_id,
            policy_decision_id=evaluation.decision_id or f"mpp-decision-{evaluation.payment_id}",
            approval_id=evaluation.approval_id,
            authorization_hash=authorization_hash,
            issued_by=issued_by,
            issued_at=current,
            expires_at=expires_at,
        )
        authorization.assert_usable(now=current)
        return authorization

    @staticmethod
    def reject_direct_signer_call(*, operation: str = "sign") -> None:
        raise DirectSignerCallError(
            f"direct Signer {operation} is forbidden; use an allow Policy decision"
        )


@dataclass
class InMemoryMppBudgetRepository:
    """Thread-safe reference budget repository used by unit tests."""

    counters: dict[tuple[str, str, str, str], MppBudgetCounter] = field(default_factory=dict)
    reservations: dict[str, MppBudgetReservation] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @staticmethod
    def _period(now: datetime | None = None) -> str:
        return _utc(now or datetime.now(timezone.utc)).date().isoformat()

    def current_spend(
        self,
        *,
        profile_id: str,
        task_id: str,
        agent_id: str,
        now: datetime | None = None,
    ) -> tuple[int, int]:
        period = self._period(now)
        with self._lock:
            task = self.counters.get((profile_id, "TASK", task_id, MPP_DAILY_PERIOD))
            daily = self.counters.get((profile_id, "DAILY", agent_id, period))
            return (
                (task.reserved_units + task.consumed_units) if task else 0,
                (daily.reserved_units + daily.consumed_units) if daily else 0,
            )

    def reserve(
        self,
        *,
        profile: AgentPaymentProfile,
        payment_id: str,
        idempotency_key: str,
        task_id: str,
        agent_id: str,
        amount_units: int,
        correlation_id: str,
        profile_version: int | None = None,
        now: datetime | None = None,
    ) -> MppBudgetReservationResult:
        if amount_units <= 0:
            raise MppBudgetError("budget reservation amount must be positive")
        period = self._period(now)
        profile_version = profile.version if profile_version is None else profile_version
        with self._lock:
            existing = self.reservations.get(idempotency_key)
            if existing is not None:
                self._assert_same(existing, payment_id, task_id, agent_id, amount_units, profile.profile_id)
                return MppBudgetReservationResult(reservation=existing, replayed=True)
            task_key = (profile.profile_id, "TASK", task_id, MPP_DAILY_PERIOD)
            daily_key = (profile.profile_id, "DAILY", agent_id, period)
            task = self.counters.setdefault(
                task_key,
                MppBudgetCounter(
                    profile_id=profile.profile_id,
                    scope_type="TASK",
                    scope_id=task_id,
                    period_key=MPP_DAILY_PERIOD,
                    limit_units=profile.per_task_limit_units,
                ),
            )
            daily = self.counters.setdefault(
                daily_key,
                MppBudgetCounter(
                    profile_id=profile.profile_id,
                    scope_type="DAILY",
                    scope_id=agent_id,
                    period_key=period,
                    limit_units=profile.daily_limit_units,
                ),
            )
            task.limit_units = profile.per_task_limit_units
            daily.limit_units = profile.daily_limit_units
            if amount_units > profile.per_payment_limit_units:
                raise MppBudgetExceededError("payment exceeds the per-payment Profile limit")
            if task.available_units < amount_units:
                raise MppBudgetExceededError("Task MPP budget is exhausted")
            if daily.available_units < amount_units:
                raise MppBudgetExceededError("daily MPP budget is exhausted")
            timestamp = _utc(now or datetime.now(timezone.utc))
            task.reserved_units += amount_units
            task.version += 1
            daily.reserved_units += amount_units
            daily.version += 1
            reservation = MppBudgetReservation(
                reservation_id=f"mpp-reservation-{uuid4().hex}",
                idempotency_key=idempotency_key,
                payment_id=payment_id,
                agent_id=agent_id,
                task_id=task_id,
                profile_id=profile.profile_id,
                profile_version=profile_version,
                amount_units=amount_units,
                daily_period=period,
                status=MppReservationStatus.RESERVED,
                correlation_id=correlation_id,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self.reservations[idempotency_key] = reservation
            return MppBudgetReservationResult(reservation=reservation)

    def consume(self, reservation_id: str, *, now: datetime | None = None) -> MppBudgetReservation:
        with self._lock:
            reservation = self._find(reservation_id)
            if reservation.status is MppReservationStatus.CONSUMED:
                return reservation
            if reservation.status is not MppReservationStatus.RESERVED:
                raise MppBudgetError(f"reservation is {reservation.status.value}")
            task = self.counters[(reservation.profile_id, "TASK", reservation.task_id, MPP_DAILY_PERIOD)]
            daily = self.counters[(reservation.profile_id, "DAILY", reservation.agent_id, reservation.daily_period)]
            task.reserved_units -= reservation.amount_units
            task.consumed_units += reservation.amount_units
            task.version += 1
            daily.reserved_units -= reservation.amount_units
            daily.consumed_units += reservation.amount_units
            daily.version += 1
            updated = reservation.model_copy(
                update={
                    "status": MppReservationStatus.CONSUMED,
                    "updated_at": _utc(now or datetime.now(timezone.utc)),
                }
            )
            self.reservations[reservation.idempotency_key] = updated
            return updated

    def release(
        self,
        reservation_id: str,
        *,
        cancelled: bool = False,
        now: datetime | None = None,
    ) -> MppBudgetReservation:
        with self._lock:
            reservation = self._find(reservation_id)
            if reservation.status in {
                MppReservationStatus.RELEASED,
                MppReservationStatus.CANCELLED,
            }:
                return reservation
            if reservation.status is not MppReservationStatus.RESERVED:
                raise MppBudgetError(f"reservation is {reservation.status.value}")
            task = self.counters[(reservation.profile_id, "TASK", reservation.task_id, MPP_DAILY_PERIOD)]
            daily = self.counters[(reservation.profile_id, "DAILY", reservation.agent_id, reservation.daily_period)]
            task.reserved_units -= reservation.amount_units
            task.version += 1
            daily.reserved_units -= reservation.amount_units
            daily.version += 1
            updated = reservation.model_copy(
                update={
                    "status": (
                        MppReservationStatus.CANCELLED
                        if cancelled
                        else MppReservationStatus.RELEASED
                    ),
                    "updated_at": _utc(now or datetime.now(timezone.utc)),
                }
            )
            self.reservations[reservation.idempotency_key] = updated
            return updated

    def _find(self, reservation_id: str) -> MppBudgetReservation:
        for reservation in self.reservations.values():
            if reservation.reservation_id == reservation_id:
                return reservation
        raise MppBudgetError(f"budget reservation is not registered: {reservation_id}")

    @staticmethod
    def _assert_same(
        existing: MppBudgetReservation,
        payment_id: str,
        task_id: str,
        agent_id: str,
        amount_units: int,
        profile_id: str,
    ) -> None:
        if (
            existing.payment_id != payment_id
            or existing.task_id != task_id
            or existing.agent_id != agent_id
            or existing.amount_units != amount_units
            or existing.profile_id != profile_id
        ):
            raise MppBudgetError("budget idempotency key is bound to a different payment")


class MppPolicyRepository:
    """Durable MPP decisions, reservations, approvals, and capabilities."""

    def __init__(
        self,
        transaction: RepositoryTransaction,
        *,
        owner_id: str = "owner-local",
    ) -> None:
        self.transaction = transaction
        self.owner_id = owner_id

    def agent_status(self, agent_id: str) -> str:
        row = self.transaction.fetch_one(
            "SELECT status FROM mvp_agents WHERE id = %s",
            (agent_id,),
        )
        return str(_value(row, "status", 0)) if row is not None else "MISSING"

    def provider_status(self, run_id: str) -> tuple[str | None, str]:
        row = self.transaction.fetch_one(
            "SELECT provider, status FROM mvp_agent_runs WHERE id = %s",
            (run_id,),
        )
        if row is None:
            return None, "ACTIVE"
        run_status = str(_value(row, "status", 1)).upper()
        return (
            str(_value(row, "provider", 0)),
            "STOPPED" if run_status in {"STOPPED", "CANCELLED", "REJECTED"} else "ACTIVE",
        )

    def stop_reason(self, target: StopTarget, target_id: str = "*") -> str | None:
        if target is StopTarget.GLOBAL:
            checks = [("GLOBAL", "*")]
        else:
            checks = [
                ("GLOBAL", "*"),
                (target.value, target_id),
                (target.value, "*"),
            ]
        clauses = " OR ".join(
            "(target = %s AND target_id = %s)" for _ in checks
        )
        row = self.transaction.fetch_one(
            f"""
            SELECT reason
            FROM mvp_stop_controls
            WHERE stopped AND ({clauses})
            ORDER BY changed_at DESC
            LIMIT 1
            """,
            tuple(item for check in checks for item in check),
        )
        return str(_value(row, "reason", 0)) if row is not None else None

    def current_spend(
        self,
        *,
        profile_id: str,
        task_id: str,
        agent_id: str,
        now: datetime | None = None,
    ) -> tuple[int, int]:
        period = _utc(now or datetime.now(timezone.utc)).date().isoformat()
        task = self.transaction.fetch_one(
            """
            SELECT reserved_units + consumed_units
                   AS spent_units
            FROM mvp_mpp_budget_counters
            WHERE profile_id = %s AND scope_type = 'TASK'
              AND scope_id = %s AND period_key = %s
            """,
            (profile_id, task_id, MPP_DAILY_PERIOD),
        )
        daily = self.transaction.fetch_one(
            """
            SELECT reserved_units + consumed_units AS spent_units
            FROM mvp_mpp_budget_counters
            WHERE profile_id = %s AND scope_type = 'DAILY'
              AND scope_id = %s AND period_key = %s
            """,
            (profile_id, agent_id, period),
        )
        return (
            int(_value(task, "spent_units", 0)) if task else 0,
            int(_value(daily, "spent_units", 0)) if daily else 0,
        )

    def reserve_budget(
        self,
        *,
        profile: AgentPaymentProfile,
        payment_id: str,
        idempotency_key: str,
        task_id: str,
        agent_id: str,
        amount_units: int,
        correlation_id: str,
        profile_version: int | None = None,
        now: datetime | None = None,
    ) -> MppBudgetReservationResult:
        if amount_units <= 0:
            raise MppBudgetError("budget reservation amount must be positive")
        if amount_units > profile.per_payment_limit_units:
            raise MppBudgetExceededError("payment exceeds the per-payment Profile limit")
        current = _utc(now or datetime.now(timezone.utc))
        period = current.date().isoformat()
        profile_version = profile.version if profile_version is None else profile_version
        scopes = (
            ("DAILY", agent_id, period, profile.daily_limit_units),
            ("TASK", task_id, MPP_DAILY_PERIOD, profile.per_task_limit_units),
        )
        for scope_type, scope_id, period_key, limit_units in scopes:
            self.transaction.execute(
                """
                INSERT INTO mvp_mpp_budget_counters
                  (profile_id, scope_type, scope_id, period_key, limit_units)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (profile_id, scope_type, scope_id, period_key)
                DO NOTHING
                """,
                (profile.profile_id, scope_type, scope_id, period_key, limit_units),
            )
        rows = self.transaction.fetch_all(
            """
            SELECT profile_id, scope_type, scope_id, period_key,
                   limit_units, reserved_units, consumed_units, version
            FROM mvp_mpp_budget_counters
            WHERE profile_id = %s
              AND (
                (scope_type = 'TASK' AND scope_id = %s AND period_key = %s)
                OR (scope_type = 'DAILY' AND scope_id = %s AND period_key = %s)
              )
            ORDER BY scope_type
            FOR UPDATE
            """,
            (
                profile.profile_id,
                task_id,
                MPP_DAILY_PERIOD,
                agent_id,
                period,
            ),
        )
        if len(rows) != 2:
            raise MppBudgetError("MPP budget counters could not be locked")
        by_scope = {str(_value(row, "scope_type", 1)): row for row in rows}
        task = by_scope["TASK"]
        daily = by_scope["DAILY"]
        existing = self._reservation_by_idempotency(idempotency_key, for_update=True)
        if existing is not None:
            InMemoryMppBudgetRepository._assert_same(
                existing,
                payment_id,
                task_id,
                agent_id,
                amount_units,
                profile.profile_id,
            )
            return MppBudgetReservationResult(reservation=existing, replayed=True)
        self.transaction.execute(
            """
            UPDATE mvp_mpp_budget_counters
            SET limit_units = CASE
              WHEN scope_type = 'TASK' THEN %s
              ELSE %s
            END
            WHERE profile_id = %s
              AND (
                (scope_type = 'TASK' AND scope_id = %s AND period_key = %s)
                OR (scope_type = 'DAILY' AND scope_id = %s AND period_key = %s)
              )
            """,
            (
                profile.per_task_limit_units,
                profile.daily_limit_units,
                profile.profile_id,
                task_id,
                MPP_DAILY_PERIOD,
                agent_id,
                period,
            ),
        )
        if (
            int(_value(task, "reserved_units", 5))
            + int(_value(task, "consumed_units", 6))
            + amount_units
            > profile.per_task_limit_units
        ):
            raise MppBudgetExceededError("Task MPP budget is exhausted")
        if (
            int(_value(daily, "reserved_units", 5))
            + int(_value(daily, "consumed_units", 6))
            + amount_units
            > profile.daily_limit_units
        ):
            raise MppBudgetExceededError("daily MPP budget is exhausted")

        reservation_id = f"mpp-reservation-{uuid4().hex}"
        self.transaction.execute(
            """
            UPDATE mvp_mpp_budget_counters
            SET reserved_units = reserved_units + %s,
                version = version + 1,
                updated_at = now()
            WHERE profile_id = %s
              AND (
                (scope_type = 'TASK' AND scope_id = %s AND period_key = %s)
                OR (scope_type = 'DAILY' AND scope_id = %s AND period_key = %s)
              )
            """,
            (
                amount_units,
                profile.profile_id,
                task_id,
                MPP_DAILY_PERIOD,
                agent_id,
                period,
            ),
        )
        self.transaction.execute(
            """
            INSERT INTO mvp_mpp_budget_reservations
              (id, idempotency_key, payment_id, agent_id, task_id, profile_id,
               profile_version, amount_units, daily_period, status,
               correlation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'RESERVED', %s)
            """,
            (
                reservation_id,
                idempotency_key,
                payment_id,
                agent_id,
                task_id,
                profile.profile_id,
                profile_version,
                amount_units,
                period,
                correlation_id,
            ),
        )
        reservation = self._reservation_by_id(reservation_id)
        if reservation is None:
            raise MppBudgetError("MPP budget reservation was not persisted")
        return MppBudgetReservationResult(reservation=reservation)

    def consume_reservation(
        self,
        reservation_id: str,
        *,
        now: datetime | None = None,
    ) -> MppBudgetReservation:
        return self._move_reservation(
            reservation_id,
            target_status=MppReservationStatus.CONSUMED,
            now=now,
        )

    def release_reservation(
        self,
        reservation_id: str,
        *,
        cancelled: bool = False,
        now: datetime | None = None,
    ) -> MppBudgetReservation:
        return self._move_reservation(
            reservation_id,
            target_status=(
                MppReservationStatus.CANCELLED
                if cancelled
                else MppReservationStatus.RELEASED
            ),
            now=now,
        )

    def _move_reservation(
        self,
        reservation_id: str,
        *,
        target_status: MppReservationStatus,
        now: datetime | None,
    ) -> MppBudgetReservation:
        reservation = self._reservation_by_id(reservation_id, for_update=True)
        if reservation is None:
            raise MppBudgetError(f"budget reservation is not registered: {reservation_id}")
        if reservation.status is target_status:
            return reservation
        if reservation.status is not MppReservationStatus.RESERVED:
            raise MppBudgetError(f"reservation is {reservation.status.value}")
        period = reservation.daily_period
        self.transaction.fetch_all(
            """
            SELECT scope_type
            FROM mvp_mpp_budget_counters
            WHERE profile_id = %s
              AND (
                (scope_type = 'TASK' AND scope_id = %s AND period_key = %s)
                OR (scope_type = 'DAILY' AND scope_id = %s AND period_key = %s)
              )
            ORDER BY scope_type
            FOR UPDATE
            """,
            (
                reservation.profile_id,
                reservation.task_id,
                MPP_DAILY_PERIOD,
                reservation.agent_id,
                period,
            ),
        )
        if target_status is MppReservationStatus.CONSUMED:
            self.transaction.execute(
                """
                UPDATE mvp_mpp_budget_counters
                SET reserved_units = reserved_units - %s,
                    consumed_units = consumed_units + %s,
                    version = version + 1,
                    updated_at = now()
                WHERE profile_id = %s
                  AND (
                    (scope_type = 'TASK' AND scope_id = %s AND period_key = %s)
                    OR (scope_type = 'DAILY' AND scope_id = %s AND period_key = %s)
                  )
                """,
                (
                    reservation.amount_units,
                    reservation.amount_units,
                    reservation.profile_id,
                    reservation.task_id,
                    MPP_DAILY_PERIOD,
                    reservation.agent_id,
                    period,
                ),
            )
        else:
            self.transaction.execute(
                """
                UPDATE mvp_mpp_budget_counters
                SET reserved_units = reserved_units - %s,
                    version = version + 1,
                    updated_at = now()
                WHERE profile_id = %s
                  AND (
                    (scope_type = 'TASK' AND scope_id = %s AND period_key = %s)
                    OR (scope_type = 'DAILY' AND scope_id = %s AND period_key = %s)
                  )
                """,
                (
                    reservation.amount_units,
                    reservation.profile_id,
                    reservation.task_id,
                    MPP_DAILY_PERIOD,
                    reservation.agent_id,
                    period,
                ),
            )
        self.transaction.execute(
            """
            UPDATE mvp_mpp_budget_reservations
            SET status = %s, updated_at = %s
            WHERE id = %s AND status = 'RESERVED'
            """,
            (target_status.value, _utc(now or datetime.now(timezone.utc)), reservation_id),
        )
        updated = self._reservation_by_id(reservation_id)
        if updated is None:
            raise MppBudgetError("MPP reservation transition was not persisted")
        return updated

    def record_decision(
        self,
        *,
        context: MppPolicyInput,
        evaluation: MppPolicyEvaluation,
        actor_id: str,
        actor_type: str,
        decision_id: str | None = None,
        approval_id: str | None = None,
        reservation_id: str | None = None,
    ) -> str:
        decision_id = decision_id or f"mpp-decision-{uuid4().hex}"
        inserted = self.transaction.fetch_one(
            """
            INSERT INTO mvp_mpp_policy_decisions
              (id, payment_id, idempotency_key, task_id, run_id, trace_id,
               correlation_id, agent_id, profile_id, profile_version,
               decision, reason, policy_version, approval_id, reservation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key, payment_id) DO NOTHING
            RETURNING id
            """,
            (
                decision_id,
                context.payment_id,
                context.idempotency_key,
                context.task_id,
                context.run_id,
                context.trace_id,
                context.correlation_id,
                context.agent_id,
                context.profile.profile_id if context.profile else None,
                context.profile.version if context.profile else None,
                evaluation.decision.value,
                evaluation.reason,
                evaluation.policy_version,
                approval_id,
                reservation_id,
            ),
        )
        if inserted is None:
            existing = self.transaction.fetch_one(
                """
                SELECT id, decision
                FROM mvp_mpp_policy_decisions
                WHERE idempotency_key = %s AND payment_id = %s
                """,
                (context.idempotency_key, context.payment_id),
            )
            if existing is None:
                raise MppPolicyError("MPP Policy decision is not available")
            if str(_value(existing, "decision", 1)) != evaluation.decision.value:
                raise MppPolicyError("MPP Policy decision idempotency conflict")
            return str(_value(existing, "id", 0))
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-{decision_id}",
                event_version=1,
                event_type="MPP_POLICY_DECISION",
                actor_id=actor_id,
                actor_type=actor_type,
                action="EVALUATE_MPP_PAYMENT",
                target_type="SERVICE_PAYMENT",
                target_id=context.payment_id,
                before_state={},
                after_state={
                    "decision_id": decision_id,
                    "decision": evaluation.decision.value,
                    "policy_version": evaluation.policy_version,
                    "approval_id": approval_id,
                    "reservation_id": reservation_id,
                },
                policy_result=_policy_result(evaluation.decision),
                reason=evaluation.reason,
                correlation_id=context.correlation_id,
                transaction_id=context.correlation_id,
                task_id=context.task_id,
                run_id=context.run_id,
                payment_id=context.payment_id,
            ),
        )
        return decision_id

    def enqueue_owner_approval(
        self,
        *,
        payment_id: str,
        requested_by: str,
        approval_id: str | None = None,
    ) -> str:
        approval_id = approval_id or f"approval-mpp-{payment_id}"
        self.transaction.execute(
            """
            INSERT INTO approval_requests
              (id, approval_type, target_id, requested_by)
            VALUES (%s, 'POLICY_EXCEPTION'::mvp_approval_type, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (approval_id, payment_id, requested_by),
        )
        return approval_id

    def decide_owner_approval(
        self,
        *,
        payment_id: str,
        approval_id: str,
        actor_id: str,
        actor_type: str,
        decision: str,
        comment: str = "",
        correlation_id: str | None = None,
    ) -> MppApprovalResult:
        if actor_id != self.owner_id or actor_type != "OWNER":
            raise MppApprovalError("Owner authority is required for MPP approval")
        if decision not in {"APPROVE", "REJECT", "REQUEST_CHANGES", "HOLD"}:
            raise MppApprovalError("unsupported Owner approval decision")
        approval = self.transaction.fetch_one(
            """
            SELECT id, target_id, requested_by, owner_decision
            FROM approval_requests
            WHERE id = %s AND approval_type = 'POLICY_EXCEPTION'
            FOR UPDATE
            """,
            (approval_id,),
        )
        if approval is None or str(_value(approval, "target_id", 1)) != payment_id:
            raise MppApprovalError("MPP Owner approval is not registered for this payment")
        if _value(approval, "owner_decision", 3) is not None:
            raise MppApprovalError("MPP Owner approval already has a decision")
        payment = self.transaction.fetch_one(
            """
            SELECT task_id, run_id, trace_id, correlation_id, status,
                   budget_reservation_id
            FROM mvp_service_payments
            WHERE id = %s
            FOR UPDATE
            """,
            (payment_id,),
        )
        if payment is None:
            raise MppApprovalError("MPP Payment is not registered")
        reservation_id = _value(payment, "budget_reservation_id", 5)
        reservation_status: MppReservationStatus | None = None
        payment_status = str(_value(payment, "status", 4))
        if decision == "APPROVE":
            payment_status = "APPROVED"
        elif decision in {"REJECT", "REQUEST_CHANGES"}:
            payment_status = "CANCELLED"
            if reservation_id:
                reservation = self.release_reservation(
                    str(reservation_id),
                    cancelled=True,
                )
                reservation_status = reservation.status
        self.transaction.execute(
            """
            UPDATE approval_requests
            SET owner_decision = %s::mvp_approval_decision,
                comment = %s,
                decided_at = now()
            WHERE id = %s
            """,
            (decision, comment, approval_id),
        )
        if payment_status != str(_value(payment, "status", 4)):
            self.transaction.execute(
                """
                UPDATE mvp_service_payments
                SET status = %s, updated_at = now()
                WHERE id = %s
                """,
                (payment_status, payment_id),
            )
        event_type = (
            "APPROVED"
            if decision == "APPROVE"
            else "CANCELLED"
            if decision in {"REJECT", "REQUEST_CHANGES"}
            else "APPROVAL_REQUIRED"
        )
        self._payment_event(
            payment_id=payment_id,
            event_type=event_type,
            correlation_id=correlation_id or str(_value(payment, "correlation_id", 3)),
            idempotency_key=f"mpp-approval:{approval_id}",
            payload={"approval_id": approval_id, "decision": decision, "comment": comment},
        )
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-mpp-approval-{approval_id}",
                event_version=1,
                event_type="MPP_OWNER_APPROVAL",
                actor_id=actor_id,
                actor_type=actor_type,
                action="DECIDE_MPP_PAYMENT_APPROVAL",
                target_type="SERVICE_PAYMENT",
                target_id=payment_id,
                before_state={"status": str(_value(payment, "status", 4))},
                after_state={
                    "status": payment_status,
                    "approval_id": approval_id,
                    "decision": decision,
                    "reservation_id": reservation_id,
                },
                policy_result="ALLOW",
                reason=comment or f"Owner decided {decision}",
                correlation_id=correlation_id or str(_value(payment, "correlation_id", 3)),
                transaction_id=correlation_id or str(_value(payment, "correlation_id", 3)),
                task_id=str(_value(payment, "task_id", 0)),
                run_id=str(_value(payment, "run_id", 1)),
                payment_id=payment_id,
            ),
        )
        return MppApprovalResult(
            approval_id=approval_id,
            payment_id=payment_id,
            decision=decision,
            payment_status=payment_status,
            reservation_status=reservation_status,
            comment=comment,
        )

    def issue_signer_authorization(
        self,
        *,
        payment_id: str,
        policy_decision_id: str,
        issued_by: str = "mpp-policy-engine",
        ttl_seconds: int = MPP_SIGNER_AUTHORIZATION_TTL_SECONDS,
        now: datetime | None = None,
    ) -> MppSignerAuthorization:
        if ttl_seconds < 1 or ttl_seconds > 900:
            raise MppSignerAuthorizationError("invalid Signer authorization TTL")
        current = _utc(now or datetime.now(timezone.utc))
        payment = self.transaction.fetch_one(
            """
            SELECT policy_decision, status, owner_approval_id, expires_at,
                   task_id, run_id, trace_id, correlation_id, agent_id
            FROM mvp_service_payments
            WHERE id = %s
            FOR UPDATE
            """,
            (payment_id,),
        )
        if payment is None:
            raise MppSignerAuthorizationError("MPP Payment is not registered")
        decision = str(_value(payment, "policy_decision", 0))
        status = str(_value(payment, "status", 1))
        approval_id = _value(payment, "owner_approval_id", 2)
        decision_row = self.transaction.fetch_one(
            """
            SELECT decision
            FROM mvp_mpp_policy_decisions
            WHERE id = %s AND payment_id = %s
            """,
            (policy_decision_id, payment_id),
        )
        if decision_row is None or str(_value(decision_row, "decision", 0)) != decision:
            raise MppSignerAuthorizationError(
                "Signer authorization does not match the persisted Policy decision"
            )
        if self.agent_status(str(_value(payment, "agent_id", 8))).upper() != "ACTIVE":
            raise MppSignerAuthorizationError("Agent capability is stopped")
        profile_row = self.transaction.fetch_one(
            """
            SELECT p.status, p.rotation_state, p.expires_at
            FROM mvp_service_payments AS payment
            JOIN mvp_agent_payment_profiles AS p ON p.id = payment.profile_id
            WHERE payment.id = %s
            """,
            (payment_id,),
        )
        if profile_row is None or str(_value(profile_row, "status", 0)) != "ACTIVE":
            raise MppSignerAuthorizationError("Payment Profile is not active")
        if str(_value(profile_row, "rotation_state", 1)) != "CURRENT":
            raise MppSignerAuthorizationError("Payment Profile rotation is not current")
        if _utc(_value(profile_row, "expires_at", 2)) <= current:
            raise MppSignerAuthorizationError("Payment Profile has expired")
        if decision == PolicyDecision.DENY.value:
            raise MppSignerAuthorizationError("denied MPP Payment cannot reach Signer")
        if decision == PolicyDecision.REQUIRE_OWNER_APPROVAL.value:
            if status != "APPROVED" or approval_id is None:
                raise MppSignerAuthorizationError(
                    "Owner approval is required before Signer authorization"
                )
            approval = self.transaction.fetch_one(
                """
                SELECT owner_decision
                FROM approval_requests
                WHERE id = %s AND owner_decision = 'APPROVE'
                """,
                (approval_id,),
            )
            if approval is None:
                raise MppSignerAuthorizationError(
                    "Owner approval has not been verified"
                )
        elif decision != PolicyDecision.ALLOW.value:
            raise MppSignerAuthorizationError("unknown Policy decision cannot reach Signer")
        if _utc(_value(payment, "expires_at", 3)) <= current:
            raise MppSignerAuthorizationError("Payment Challenge has expired")
        for target, target_id in (
            (StopTarget.MPP, "*"),
            (StopTarget.PAYMENT, payment_id),
            (StopTarget.AGENT, str(_value(payment, "agent_id", 8))),
            (StopTarget.RUN, str(_value(payment, "run_id", 5))),
            (StopTarget.SIGNER, "*"),
        ):
            if self.stop_reason(target, target_id):
                raise MppSignerAuthorizationError(
                    f"{target.value} stop control is active"
                )
        authorization_id = f"mpp-auth-{uuid4().hex}"
        expires_at = min(
            _utc(_value(payment, "expires_at", 3)),
            current + timedelta(seconds=ttl_seconds),
        )
        authorization_hash = hashlib.sha256(
            json.dumps(
                {
                    "authorization_id": authorization_id,
                    "payment_id": payment_id,
                    "policy_decision_id": policy_decision_id,
                    "expires_at": expires_at.isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.transaction.execute(
            """
            INSERT INTO mvp_mpp_signer_authorizations
              (id, payment_id, policy_decision_id, approval_id,
               authorization_hash, issued_by, issued_at, expires_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ISSUED')
            ON CONFLICT (payment_id) DO NOTHING
            """,
            (
                authorization_id,
                payment_id,
                policy_decision_id,
                approval_id,
                authorization_hash,
                issued_by,
                current,
                expires_at,
            ),
        )
        row = self.transaction.fetch_one(
            """
            SELECT id, payment_id, policy_decision_id, approval_id,
                   authorization_hash, issued_by, issued_at, expires_at, status
            FROM mvp_mpp_signer_authorizations
            WHERE payment_id = %s
            """,
            (payment_id,),
        )
        if row is None:
            raise MppSignerAuthorizationError("Signer authorization was not persisted")
        authorization = MppSignerAuthorization(
            authorization_id=str(_value(row, "id", 0)),
            payment_id=str(_value(row, "payment_id", 1)),
            policy_decision_id=str(_value(row, "policy_decision_id", 2)),
            approval_id=_value(row, "approval_id", 3),
            authorization_hash=str(_value(row, "authorization_hash", 4)),
            issued_by=str(_value(row, "issued_by", 5)),
            issued_at=_utc(_value(row, "issued_at", 6)),
            expires_at=_utc(_value(row, "expires_at", 7)),
            status=MppSignerAuthorizationStatus(str(_value(row, "status", 8))),
        )
        authorization.assert_usable(now=current)
        self.transaction.execute(
            """
            UPDATE mvp_service_payments
            SET status = 'SIGNER_REQUESTED', signer_request_id = %s,
                updated_at = now()
            WHERE id = %s AND status IN ('PROPOSED', 'APPROVED', 'SIGNER_REQUESTED')
            """,
            (authorization.authorization_id, payment_id),
        )
        self._payment_event(
            payment_id=payment_id,
            event_type="SIGNER_REQUESTED",
            correlation_id=str(_value(payment, "correlation_id", 7)),
            idempotency_key=f"mpp-signer:{authorization.authorization_id}",
            payload={
                "authorization_id": authorization.authorization_id,
                "expires_at": authorization.expires_at.isoformat(),
            },
        )
        return authorization

    def cancel_payment(
        self,
        *,
        payment_id: str,
        actor_id: str,
        actor_type: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> MppApprovalResult:
        if actor_id != self.owner_id or actor_type != "OWNER":
            raise MppApprovalError("Owner authority is required to cancel an MPP Payment")
        if not reason.strip():
            raise MppPolicyError("cancellation reason is required")
        row = self.transaction.fetch_one(
            """
            SELECT task_id, run_id, correlation_id, status, budget_reservation_id
            FROM mvp_service_payments
            WHERE id = %s
            FOR UPDATE
            """,
            (payment_id,),
        )
        if row is None:
            raise MppPolicyError("MPP Payment is not registered")
        reservation_id = _value(row, "budget_reservation_id", 4)
        reservation_status = None
        if reservation_id:
            reservation = self.release_reservation(str(reservation_id), cancelled=True)
            reservation_status = reservation.status
        self.transaction.execute(
            """
            UPDATE mvp_service_payments
            SET status = 'CANCELLED', updated_at = now()
            WHERE id = %s
            """,
            (payment_id,),
        )
        correlation = correlation_id or str(_value(row, "correlation_id", 2))
        self._payment_event(
            payment_id=payment_id,
            event_type="CANCELLED",
            correlation_id=correlation,
            idempotency_key=f"mpp-cancel:{payment_id}",
            payload={"reason": reason},
        )
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-mpp-cancel-{payment_id}",
                event_version=1,
                event_type="MPP_PAYMENT_CANCELLED",
                actor_id=actor_id,
                actor_type=actor_type,
                action="CANCEL_MPP_PAYMENT",
                target_type="SERVICE_PAYMENT",
                target_id=payment_id,
                before_state={"status": str(_value(row, "status", 3))},
                after_state={"status": "CANCELLED", "reservation_id": reservation_id},
                policy_result="ALLOW",
                reason=reason,
                correlation_id=correlation,
                transaction_id=correlation,
                task_id=str(_value(row, "task_id", 0)),
                run_id=str(_value(row, "run_id", 1)),
                payment_id=payment_id,
            ),
        )
        return MppApprovalResult(
            approval_id="",
            payment_id=payment_id,
            decision="CANCEL",
            payment_status="CANCELLED",
            reservation_status=reservation_status,
            comment=reason,
        )

    def _payment_event(
        self,
        *,
        payment_id: str,
        event_type: str,
        correlation_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.transaction.execute(
            """
            INSERT INTO mvp_service_payment_events
              (id, payment_id, event_type, idempotency_key, correlation_id, payload)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (
                f"mpp-event-{uuid4().hex}",
                payment_id,
                event_type,
                idempotency_key,
                correlation_id,
                json.dumps(dict(payload), default=str, sort_keys=True),
            ),
        )
        OutboxWriter.enqueue(
            self.transaction,
            OutboxEvent(
                event_id=f"outbox-mpp-{uuid4().hex}",
                aggregate_type="SERVICE_PAYMENT",
                aggregate_id=payment_id,
                event_type=f"MPP_{event_type}",
                idempotency_key=f"outbox:{idempotency_key}",
                payload=dict(payload),
                transaction_id=correlation_id,
            ),
        )

    def _reservation_by_idempotency(
        self,
        idempotency_key: str,
        *,
        for_update: bool = False,
    ) -> MppBudgetReservation | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.transaction.fetch_one(
            f"""
            SELECT id, idempotency_key, payment_id, agent_id, task_id,
                   profile_id, profile_version, amount_units, daily_period,
                   status, correlation_id, created_at, updated_at
            FROM mvp_mpp_budget_reservations
            WHERE idempotency_key = %s{suffix}
            """,
            (idempotency_key,),
        )
        return self._reservation(row)

    def _reservation_by_id(
        self,
        reservation_id: str,
        *,
        for_update: bool = False,
    ) -> MppBudgetReservation | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self.transaction.fetch_one(
            f"""
            SELECT id, idempotency_key, payment_id, agent_id, task_id,
                   profile_id, profile_version, amount_units, daily_period,
                   status, correlation_id, created_at, updated_at
            FROM mvp_mpp_budget_reservations
            WHERE id = %s{suffix}
            """,
            (reservation_id,),
        )
        return self._reservation(row)

    @staticmethod
    def _reservation(row: Any) -> MppBudgetReservation | None:
        if row is None:
            return None
        return MppBudgetReservation(
            reservation_id=str(_value(row, "id", 0)),
            idempotency_key=str(_value(row, "idempotency_key", 1)),
            payment_id=str(_value(row, "payment_id", 2)),
            agent_id=str(_value(row, "agent_id", 3)),
            task_id=str(_value(row, "task_id", 4)),
            profile_id=str(_value(row, "profile_id", 5)),
            profile_version=int(_value(row, "profile_version", 6)),
            amount_units=int(_value(row, "amount_units", 7)),
            daily_period=str(_value(row, "daily_period", 8)),
            status=MppReservationStatus(str(_value(row, "status", 9))),
            correlation_id=str(_value(row, "correlation_id", 10)),
            created_at=_utc(_value(row, "created_at", 11)),
            updated_at=_utc(_value(row, "updated_at", 12)),
        )


__all__ = [
    "DirectSignerCallError",
    "InMemoryMppBudgetRepository",
    "MPP_ENGINE_POLICY_VERSION",
    "MppApprovalError",
    "MppApprovalResult",
    "MppBudgetCounter",
    "MppBudgetError",
    "MppBudgetExceededError",
    "MppBudgetReservation",
    "MppBudgetReservationResult",
    "MppPolicyEngine",
    "MppPolicyEvaluation",
    "MppPolicyError",
    "MppPolicyInput",
    "MppPolicyRepository",
    "MppReservationStatus",
    "MppSignerAuthorization",
    "MppSignerAuthorizationError",
    "MppSignerAuthorizationStatus",
]
