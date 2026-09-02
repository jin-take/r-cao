"""MPP Service Payment boundary.

This module deliberately has no dependency on the Virtual Reward Ledger,
Treasury, Wallet, Signer, or network clients.  It validates a structured
service-payment proposal and persists only the payment proposal, its Audit
event, and its post-commit Outbox intent.  Execution is a later, separately
gated concern.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter
from .auth import ActorContext, ActorType
from .policy import (
    MPP_POLICY_VERSION,
    Phase,
    PhaseCapability,
    PolicyAction,
    PolicyDecision,
    PolicyViolation,
    evaluate_policy,
    require_phase_capability,
)
from .payment_profile import AgentPaymentProfileRepository
from .repository import RepositoryTransaction


MAX_SIGNED_BIGINT = (1 << 63) - 1
SERVICE_RECIPIENT_KIND = "SERVICE"
FORBIDDEN_PAYMENT_TOKENS = frozenset(
    {
        "SOL",
        "VIRTUAL",
        "VIRTUAL_REWARD",
        "REWARD",
        "TREASURY",
    }
)
ALLOWED_PAYMENT_NETWORKS = frozenset({"LOCAL", "SOLANA_DEVNET"})


class PaymentBoundaryError(ValueError):
    """Base error for malformed or unsafe Service Payment proposals."""


class PaymentPolicyError(PaymentBoundaryError):
    """A Service Payment cannot pass the current Policy or Phase Gate."""


class PaymentIdempotencyConflict(PaymentBoundaryError):
    """An idempotency key was reused for a different Payment request."""


class DirectAgentTransferError(PaymentBoundaryError):
    """MPP cannot be used for Agent-to-Agent Reward or asset transfers."""


class PaymentPurpose(str, Enum):
    SERVICE_PAYMENT = "SERVICE_PAYMENT"


class PaymentNetwork(str, Enum):
    LOCAL = "LOCAL"
    SOLANA_DEVNET = "SOLANA_DEVNET"


class ServicePaymentStatus(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVED = "APPROVED"
    SIGNER_REQUESTED = "SIGNER_REQUESTED"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    DENIED = "DENIED"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


# Keep the boundary vocabulary discoverable without creating a second Policy
# decision enum that could drift from the constitutional Policy module.
PaymentDecision = PolicyDecision


class ServicePaymentRequest(BaseModel):
    """The only request shape accepted by the Service Payment boundary.

    ``extra='forbid'`` is intentional: a caller cannot smuggle a wallet,
    Reward, Treasury, or transfer field through a generic payment payload.
    Amounts are strict integers and are stored in the database as BIGINT base
    units; the canonical challenge representation uses a decimal string.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    payment_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    challenge_id: str = Field(min_length=1, max_length=200)
    nonce: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)
    agent_id: str = Field(min_length=1, max_length=200)
    service_id: str = Field(min_length=1, max_length=300)
    program_id: str | None = Field(default=None, min_length=1, max_length=300)
    profile_id: str | None = Field(default=None, min_length=1, max_length=200)
    profile_version: StrictInt | None = Field(default=None, ge=1)
    recipient: str = Field(min_length=1, max_length=300)
    recipient_kind: Literal["SERVICE"] = SERVICE_RECIPIENT_KIND
    network: PaymentNetwork
    token: str = Field(min_length=1, max_length=100)
    amount_units: StrictInt = Field(gt=0, le=MAX_SIGNED_BIGINT)
    purpose: PaymentPurpose
    expires_at: datetime

    @field_validator(
        "payment_id",
        "idempotency_key",
        "challenge_id",
        "nonce",
        "task_id",
        "run_id",
        "trace_id",
        "correlation_id",
        "agent_id",
        "service_id",
        "program_id",
        "profile_id",
        "recipient",
        "token",
    )
    @classmethod
    def reject_control_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ord(character) < 32 for character in value):
            raise ValueError("payment identifiers cannot contain control characters")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_profile_snapshot_pair(self) -> "ServicePaymentRequest":
        if (self.profile_id is None) != (self.profile_version is None):
            raise ValueError("profile_id and profile_version must be supplied together")
        return self

    def canonical_payload(self) -> dict[str, str | int]:
        """Return the stable, secret-free challenge payload for hashing."""

        payload: dict[str, str | int] = {
            "schema_version": "rcao-mpp-profile-v1",
            "payment_id": self.payment_id,
            "idempotency_key": self.idempotency_key,
            "challenge_id": self.challenge_id,
            "nonce": self.nonce,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "agent_id": self.agent_id,
            "service_id": self.service_id,
            "recipient": self.recipient,
            "recipient_kind": self.recipient_kind,
            "network": self.network.value,
            "token": self.token,
            "amount_units": str(self.amount_units),
            "purpose": self.purpose.value,
            "expires_at": self.expires_at.astimezone(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
        }
        # These fields were introduced with the versioned Payment Profile
        # contract.  Omitting absent values preserves hashes for pre-profile
        # Service Payment records while binding them for new requests.
        if self.program_id is not None:
            payload["program_id"] = self.program_id
        if self.profile_id is not None:
            payload["profile_id"] = self.profile_id
            payload["profile_version"] = str(self.profile_version)
        return payload

    def challenge_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ServicePaymentEvaluation:
    decision: PolicyDecision
    reason: str
    policy_version: str = MPP_POLICY_VERSION


def _reject_agent_or_internal_recipient(request: ServicePaymentRequest) -> None:
    if request.recipient == request.agent_id:
        raise DirectAgentTransferError(
            "MPP Service Payment cannot pay the requesting Agent directly"
        )
    lowered_recipient = request.recipient.casefold()
    if lowered_recipient.startswith(
        ("agent:", "agent-", "owner:", "owner-", "treasury:", "treasury-", "ledger:", "ledger-")
    ):
        raise DirectAgentTransferError(
            "MPP Service Payment recipient must be a registered external Service"
        )


def evaluate_service_payment(
    request: ServicePaymentRequest,
    *,
    phase: Phase,
    now: datetime | None = None,
    service_registered: bool = True,
    profile_allows: bool = True,
    owner_approval_required: bool = False,
    stopped: bool = False,
) -> ServicePaymentEvaluation:
    """Evaluate a proposal without executing a payment side effect."""

    if request.purpose is not PaymentPurpose.SERVICE_PAYMENT:
        raise PaymentPolicyError("only SERVICE_PAYMENT is accepted by the MPP boundary")
    if request.recipient_kind != SERVICE_RECIPIENT_KIND:
        raise DirectAgentTransferError("only SERVICE recipients are supported")
    _reject_agent_or_internal_recipient(request)

    if request.token.upper() in FORBIDDEN_PAYMENT_TOKENS:
        raise PaymentPolicyError(
            "SOL is fee-only and Reward/Treasury assets cannot be MPP payment tokens"
        )
    if request.network.value not in ALLOWED_PAYMENT_NETWORKS:
        raise PaymentPolicyError("MPP payments are limited to local/devnet fixtures")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must include a timezone")
    if request.expires_at <= current.astimezone(timezone.utc):
        return ServicePaymentEvaluation(
            PolicyDecision.DENY,
            "Payment Challenge has expired",
        )
    if stopped:
        return ServicePaymentEvaluation(
            PolicyDecision.DENY,
            "MPP or Payment stop control is active",
        )
    try:
        require_phase_capability(phase, PhaseCapability.MPP_DEVNET)
    except PolicyViolation:
        return ServicePaymentEvaluation(
            PolicyDecision.DENY,
            f"MPP Service Payment is unavailable in {phase.value}",
        )
    if not service_registered:
        return ServicePaymentEvaluation(
            PolicyDecision.DENY,
            "recipient Service is not registered",
        )
    if not profile_allows:
        return ServicePaymentEvaluation(
            PolicyDecision.DENY,
            "Payment Profile does not allow this Service Payment",
        )
    if owner_approval_required:
        return ServicePaymentEvaluation(
            PolicyDecision.REQUIRE_OWNER_APPROVAL,
            "Payment Profile requires explicit Owner approval",
        )
    return ServicePaymentEvaluation(PolicyDecision.ALLOW, "bounded Service Payment allowed")


class ServicePaymentPolicy:
    """Named facade for callers that prefer an object-level policy boundary."""

    evaluate = staticmethod(evaluate_service_payment)


@dataclass(frozen=True)
class ServicePaymentRecord:
    request: ServicePaymentRequest
    policy_decision: PolicyDecision
    status: ServicePaymentStatus
    policy_version: str
    policy_decision_id: str | None = None
    owner_approval_id: str | None = None
    budget_reservation_id: str | None = None
    created_at: datetime | str | None = None

    @property
    def payment_id(self) -> str:
        return self.request.payment_id

    def to_payload(self) -> dict[str, Any]:
        return {
            **self.request.canonical_payload(),
            "challenge_hash": self.request.challenge_hash(),
            "policy_decision": self.policy_decision.value,
            "status": self.status.value,
            "policy_version": self.policy_version,
            "policy_decision_id": self.policy_decision_id,
            "owner_approval_id": self.owner_approval_id,
            "budget_reservation_id": self.budget_reservation_id,
            "created_at": (
                self.created_at.isoformat()
                if isinstance(self.created_at, datetime)
                else self.created_at
            ),
        }


@dataclass(frozen=True)
class ServicePaymentResult:
    payment: ServicePaymentRecord
    replayed: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {"payment": self.payment.to_payload(), "replayed": self.replayed}


PAYMENT_RECORD_COLUMNS = (
    "id",
    "idempotency_key",
    "challenge_id",
    "nonce",
    "task_id",
    "run_id",
    "trace_id",
    "correlation_id",
    "agent_id",
    "service_id",
    "program_id",
    "profile_id",
    "profile_version",
    "policy_decision_id",
    "budget_reservation_id",
    "owner_approval_id",
    "recipient",
    "recipient_kind",
    "network",
    "token",
    "amount_units",
    "purpose",
    "expires_at",
    "challenge_hash",
    "policy_version",
    "policy_decision",
    "status",
    "created_at",
)


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _as_utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _record_from_row(row: Any) -> ServicePaymentRecord:
    values = (
        dict(row)
        if isinstance(row, Mapping)
        else dict(zip(PAYMENT_RECORD_COLUMNS, row, strict=True))
    )
    request = ServicePaymentRequest(
        payment_id=str(values["id"]),
        idempotency_key=str(values["idempotency_key"]),
        challenge_id=str(values["challenge_id"]),
        nonce=str(values["nonce"]),
        task_id=str(values["task_id"]),
        run_id=str(values["run_id"]),
        trace_id=str(values["trace_id"]),
        correlation_id=str(values["correlation_id"]),
        agent_id=str(values["agent_id"]),
        service_id=str(values["service_id"]),
        program_id=(str(values["program_id"]) if values.get("program_id") else None),
        profile_id=(str(values["profile_id"]) if values.get("profile_id") else None),
        profile_version=(
            int(values["profile_version"])
            if values.get("profile_version") is not None
            else None
        ),
        recipient=str(values["recipient"]),
        recipient_kind=str(values["recipient_kind"]),
        network=PaymentNetwork(str(values["network"])),
        token=str(values["token"]),
        amount_units=int(values["amount_units"]),
        purpose=PaymentPurpose(str(values["purpose"])),
        expires_at=_as_utc(values["expires_at"]),
    )
    if request.challenge_hash() != str(values["challenge_hash"]):
        raise PaymentBoundaryError("persisted Payment Challenge hash does not match its request")
    return ServicePaymentRecord(
        request=request,
        policy_decision=PolicyDecision(str(values["policy_decision"])),
        status=ServicePaymentStatus(str(values["status"])),
        policy_version=str(values["policy_version"]),
        policy_decision_id=(
            str(values["policy_decision_id"])
            if values.get("policy_decision_id")
            else None
        ),
        owner_approval_id=(
            str(values["owner_approval_id"])
            if values.get("owner_approval_id")
            else None
        ),
        budget_reservation_id=(
            str(values["budget_reservation_id"])
            if values.get("budget_reservation_id")
            else None
        ),
        created_at=values.get("created_at"),
    )


class ServicePaymentRepository:
    """Persist proposals without importing or invoking Reward/Treasury code."""

    def __init__(self, transaction: RepositoryTransaction) -> None:
        self.transaction = transaction

    def _find_by_idempotency(self, idempotency_key: str) -> ServicePaymentRecord | None:
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(PAYMENT_RECORD_COLUMNS)}
            FROM mvp_service_payments
            WHERE idempotency_key = %s
            FOR UPDATE
            """,
            (idempotency_key,),
        )
        return _record_from_row(row) if row is not None else None

    def _find_by_challenge_or_nonce(
        self, request: ServicePaymentRequest
    ) -> ServicePaymentRecord | None:
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(PAYMENT_RECORD_COLUMNS)}
            FROM mvp_service_payments
            WHERE challenge_id = %s OR nonce = %s
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            FOR UPDATE
            """,
            (request.challenge_id, request.nonce),
        )
        return _record_from_row(row) if row is not None else None

    @staticmethod
    def _assert_same_request(
        existing: ServicePaymentRecord, request: ServicePaymentRequest
    ) -> None:
        if existing.request.challenge_hash() != request.challenge_hash():
            raise PaymentIdempotencyConflict(
                "idempotency key is already bound to a different Service Payment request"
            )

    def _ensure_task_scope(self, request: ServicePaymentRequest) -> None:
        row = self.transaction.fetch_one(
            """
            SELECT t.id
            FROM mvp_tasks AS t
            WHERE t.id = %s
              AND (
                t.assigned_executive_agent_id = %s
                OR EXISTS (
                  SELECT 1
                  FROM mvp_agent_memberships AS membership
                  WHERE membership.task_id = t.id
                    AND membership.agent_id = %s
                )
              )
            """,
            (request.task_id, request.agent_id, request.agent_id),
        )
        if row is None:
            raise PaymentPolicyError("Agent is outside the requested Task scope")

    def propose(
        self,
        request: ServicePaymentRequest,
        *,
        actor: ActorContext,
        owner_approval_required: bool = False,
        service_registered: bool = True,
        profile_allows: bool = True,
        stopped: bool = False,
        now: datetime | None = None,
        record_denied: bool = False,
    ) -> ServicePaymentResult:
        """Persist a proposal and its Audit/Outbox intent atomically.

        This method has no execution path.  In particular it never calls a
        Wallet, Signer, network adapter, Reward Ledger, or Treasury API.
        """

        if actor.actor_type is ActorType.AGENT and actor.actor_id != request.agent_id:
            raise PaymentPolicyError("authenticated Agent must match payment agent_id")
        if actor.actor_type not in {ActorType.AGENT, ActorType.OWNER}:
            raise PaymentPolicyError("only an Owner or Task-bound Agent may propose a payment")
        if actor.actor_type is ActorType.AGENT and request.task_id not in actor.task_ids:
            raise PaymentPolicyError("authenticated Agent is outside the requested Task scope")
        actor_decision = evaluate_policy(
            actor.role,
            PolicyAction.REQUEST_SERVICE_PAYMENT,
            phase=actor.phase,
        )
        if actor_decision is not PolicyDecision.ALLOW:
            raise PaymentPolicyError(
                f"{PolicyAction.REQUEST_SERVICE_PAYMENT.value} is not allowed for this actor"
            )

        existing = self._find_by_idempotency(request.idempotency_key)
        if existing is not None:
            self._assert_same_request(existing, request)
            return ServicePaymentResult(existing, replayed=True)
        existing_challenge = self._find_by_challenge_or_nonce(request)
        if existing_challenge is not None:
            self._assert_same_request(existing_challenge, request)
            return ServicePaymentResult(existing_challenge, replayed=True)

        self._ensure_task_scope(request)
        if request.profile_id is None or request.profile_version is None:
            raise PaymentPolicyError("an active Payment Profile snapshot is required")
        profile = AgentPaymentProfileRepository(self.transaction).require(request.profile_id)
        if profile.agent_id != request.agent_id:
            raise PaymentPolicyError("Payment Profile Agent does not match the request")
        if profile.version != request.profile_version:
            raise PaymentPolicyError("Payment Profile version does not match the request snapshot")
        # #14 is the single application-service policy boundary.  The
        # profile remains policy input; the engine additionally checks stops,
        # correlation, current Agent/Provider state, and atomic budget state.
        from .mpp_policy import (
            MppPolicyEngine,
            MppPolicyInput,
            MppPolicyRepository,
        )

        policy_repository = MppPolicyRepository(self.transaction)
        task_spent_units, daily_spent_units = policy_repository.current_spend(
            profile_id=profile.profile_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            now=now,
        )
        provider_id, provider_run_status = policy_repository.provider_status(request.run_id)
        provider_status = (
            "STOPPED"
            if provider_run_status.upper() in {"STOPPED", "CANCELLED", "REJECTED"}
            else "ACTIVE"
        )
        context = MppPolicyInput.from_request(
            request,
            profile=profile,
            phase=actor.phase,
            service_registered=service_registered,
            profile_allows=profile_allows,
            mpp_status="STOPPED" if stopped else "ACTIVE",
            agent_status=policy_repository.agent_status(request.agent_id),
            provider_id=provider_id,
            provider_status=provider_status,
            task_spent_units=task_spent_units,
            daily_spent_units=daily_spent_units,
            force_owner_approval=owner_approval_required,
        )
        evaluation = MppPolicyEngine(
            stop_checker=policy_repository.stop_reason,
            clock=(lambda: now) if now is not None else None,
        ).evaluate(context)
        if evaluation.decision is PolicyDecision.DENY:
            if record_denied:
                decision_id = f"mpp-decision-{request.payment_id}"
                policy_repository.record_decision(
                    context=context,
                    evaluation=evaluation,
                    actor_id=actor.actor_id,
                    actor_type=actor.actor_type.value,
                    decision_id=decision_id,
                )
                return ServicePaymentResult(
                    ServicePaymentRecord(
                        request=request,
                        policy_decision=PolicyDecision.DENY,
                        status=ServicePaymentStatus.DENIED,
                        policy_version=evaluation.policy_version,
                        policy_decision_id=decision_id,
                    )
                )
            raise PaymentPolicyError(evaluation.reason)

        status = (
            ServicePaymentStatus.APPROVAL_REQUIRED
            if evaluation.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
            else ServicePaymentStatus.PROPOSED
        )
        correlation_id = request.correlation_id
        reservation_id: str | None = None
        if (
            request.amount_units <= profile.per_payment_limit_units
            and task_spent_units + request.amount_units <= profile.per_task_limit_units
            and daily_spent_units + request.amount_units <= profile.daily_limit_units
        ):
            reservation = policy_repository.reserve_budget(
                profile=profile,
                payment_id=request.payment_id,
                idempotency_key=request.idempotency_key,
                task_id=request.task_id,
                agent_id=request.agent_id,
                amount_units=request.amount_units,
                correlation_id=correlation_id,
                profile_version=request.profile_version,
                now=now,
            )
            reservation_id = reservation.reservation.reservation_id
        approval_id: str | None = None
        if evaluation.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL:
            approval_id = policy_repository.enqueue_owner_approval(
                payment_id=request.payment_id,
                requested_by=actor.actor_id,
            )
        decision_id = f"mpp-decision-{request.payment_id}"
        after = {
            "payment_id": request.payment_id,
            "purpose": request.purpose.value,
            "network": request.network.value,
            "token": request.token,
            "amount_units": request.amount_units,
            "task_id": request.task_id,
            "run_id": request.run_id,
            "trace_id": request.trace_id,
            "service_id": request.service_id,
            "program_id": request.program_id,
            "profile_id": request.profile_id,
            "profile_version": request.profile_version,
            "recipient": request.recipient,
            "policy_decision": evaluation.decision.value,
            "status": status.value,
            "challenge_hash": request.challenge_hash(),
            "policy_decision_id": decision_id,
            "owner_approval_id": approval_id,
            "budget_reservation_id": reservation_id,
        }
        self.transaction.execute(
            """
            INSERT INTO mvp_service_payments
              (id, idempotency_key, challenge_id, nonce, task_id, run_id,
               trace_id, correlation_id, agent_id, service_id, program_id,
               profile_id, profile_version, policy_decision_id,
               budget_reservation_id, owner_approval_id, recipient,
               recipient_kind, network, token, amount_units, purpose,
               expires_at, challenge_hash, policy_version, policy_decision,
               status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s)
            """,
            (
                request.payment_id,
                request.idempotency_key,
                request.challenge_id,
                request.nonce,
                request.task_id,
                request.run_id,
                request.trace_id,
                request.correlation_id,
                request.agent_id,
                request.service_id,
                request.program_id or "",
                request.profile_id,
                request.profile_version,
                decision_id,
                reservation_id,
                approval_id,
                request.recipient,
                request.recipient_kind,
                request.network.value,
                request.token,
                request.amount_units,
                request.purpose.value,
                request.expires_at,
                request.challenge_hash(),
                evaluation.policy_version,
                evaluation.decision.value,
                status.value,
                actor.actor_id,
            ),
        )
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-{request.payment_id}",
                event_version=1,
                event_type="SERVICE_PAYMENT_PROPOSED",
                actor_id=actor.actor_id,
                actor_type=actor.actor_type.value,
                action="PROPOSE_SERVICE_PAYMENT",
                target_type="SERVICE_PAYMENT",
                target_id=request.payment_id,
                before_state={},
                after_state=after,
                policy_result=(
                    "OWNER_APPROVAL_REQUIRED"
                    if evaluation.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
                    else evaluation.decision.value.upper()
                ),
                reason=evaluation.reason,
                correlation_id=correlation_id,
                transaction_id=correlation_id,
                task_id=request.task_id,
                run_id=request.run_id,
                payment_id=request.payment_id,
            ),
        )
        OutboxWriter.enqueue(
            self.transaction,
            OutboxEvent(
                event_id=f"outbox-{request.payment_id}",
                aggregate_type="SERVICE_PAYMENT",
                aggregate_id=request.payment_id,
                event_type="SERVICE_PAYMENT_PROPOSED",
                idempotency_key=request.idempotency_key,
                payload=after,
                event_version=1,
                transaction_id=correlation_id,
            ),
        )
        policy_repository.record_decision(
            context=context,
            evaluation=evaluation,
            actor_id=actor.actor_id,
            actor_type=actor.actor_type.value,
            decision_id=decision_id,
            approval_id=approval_id,
            reservation_id=reservation_id,
        )
        policy_repository._payment_event(
            payment_id=request.payment_id,
            event_type=(
                "APPROVAL_REQUIRED"
                if evaluation.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
                else "PROPOSED"
            ),
            correlation_id=correlation_id,
            idempotency_key=f"mpp-payment:{request.idempotency_key}",
            payload=after,
        )
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(PAYMENT_RECORD_COLUMNS)}
            FROM mvp_service_payments
            WHERE id = %s
            """,
            (request.payment_id,),
        )
        if row is None:
            raise PaymentBoundaryError("Service Payment proposal was not persisted")
        return ServicePaymentResult(_record_from_row(row))

    def decide_owner_approval(
        self,
        *,
        payment_id: str,
        approval_id: str,
        actor: ActorContext,
        decision: str,
        comment: str = "",
    ) -> Any:
        """Record an Owner decision without granting Signer execution."""

        from .mpp_policy import MppPolicyRepository

        return MppPolicyRepository(self.transaction).decide_owner_approval(
            payment_id=payment_id,
            approval_id=approval_id,
            actor_id=actor.actor_id,
            actor_type=actor.actor_type.value,
            decision=decision,
            comment=comment,
        )

    def issue_signer_authorization(
        self,
        *,
        payment_id: str,
        policy_decision_id: str,
        issued_by: str = "mpp-policy-engine",
    ) -> Any:
        """Issue a verifiable short-lived capability, never a signature."""

        from .mpp_policy import MppPolicyRepository

        return MppPolicyRepository(self.transaction).issue_signer_authorization(
            payment_id=payment_id,
            policy_decision_id=policy_decision_id,
            issued_by=issued_by,
        )

    def cancel(
        self,
        *,
        payment_id: str,
        actor: ActorContext,
        reason: str,
    ) -> Any:
        from .mpp_policy import MppPolicyRepository

        return MppPolicyRepository(self.transaction).cancel_payment(
            payment_id=payment_id,
            actor_id=actor.actor_id,
            actor_type=actor.actor_type.value,
            reason=reason,
        )
