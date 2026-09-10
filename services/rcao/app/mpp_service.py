"""MPP paid Service Agent boundary for local/devnet validation.

This module is the receiving-side counterpart to :mod:`app.mpp_client`.  It is
intentionally limited to local/devnet fixtures and never converts received
payments into Reward, Treasury, salary, or Agent-to-Agent transfers.

The boundary provides four things required by Issue #8:

* an HTTP-402-compatible Challenge generator;
* strict correlation/cluster/hash/proof validation;
* idempotent receipt/replay protection;
* a Task-bound Service result carrying a secret-free audit payload.

Real chain confirmation is deliberately abstracted behind ``PaymentConfirmationVerifier``.
The built-in verifier is only for local fixture tests and MUST NOT be used for
mainnet or real assets.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from .mpp_client import MppChallenge, MppPaymentMethod, MppPaymentProof
from .payment_boundary import PaymentNetwork


class MppServiceError(ValueError):
    """Base error for paid Service Agent validation failures."""


class MppServiceNotFound(MppServiceError):
    """The requested Service is not registered in the allowlist."""


class MppServicePaymentRejected(MppServiceError):
    """A payment proof, receipt, cluster, or Challenge failed validation."""


class MppServiceReplayError(MppServicePaymentRejected):
    """A receipt/proof was reused for another Service execution."""


class MppServiceChallengeRequest(BaseModel):
    """Task correlation needed to issue a paid Service Challenge."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("agent_id", "task_id", "run_id", "trace_id", "correlation_id", "idempotency_key")
    @classmethod
    def reject_controls(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("Service identifiers cannot contain control characters")
        return value


class MppServicePaymentSubmission(BaseModel):
    """Public proof supplied on the single paid retry."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    challenge_id: str = Field(min_length=1, max_length=200)
    challenge_hash: StrictStr = Field(min_length=64, max_length=64)
    cluster: str = Field(min_length=1, max_length=80)
    proof: MppPaymentProof
    receipt_id: str = Field(min_length=1, max_length=200)
    confirmed: StrictBool


class MppServiceExecutionResult(BaseModel):
    """Paid Service result returned only after validation succeeds."""

    model_config = ConfigDict(extra="forbid")

    service_id: str
    payment_id: str
    challenge_id: str
    receipt_id: str
    task_id: str
    run_id: str
    trace_id: str
    correlation_id: str
    status: str = "SUCCEEDED"
    result: Any
    audit: dict[str, Any]
    replayed: bool = False


@dataclass(frozen=True)
class MppServiceDefinition:
    """Allowlisted, Task-bound paid Service configuration."""

    service_id: str
    recipient: str
    token: str
    amount_units: int
    network: PaymentNetwork = PaymentNetwork.LOCAL
    cluster: str = "LOCAL"
    payment_method: MppPaymentMethod = MppPaymentMethod.LOCAL_TEST
    expires_seconds: int = 300
    allowed_tasks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.service_id.strip() or not self.recipient.strip():
            raise MppServiceError("service_id and recipient are required")
        if self.amount_units <= 0:
            raise MppServiceError("amount_units must be positive")
        if self.expires_seconds <= 0 or self.expires_seconds > 3600:
            raise MppServiceError("expires_seconds must be between 1 and 3600")
        if self.network is PaymentNetwork.LOCAL:
            if self.cluster.upper() != "LOCAL":
                raise MppServiceError("LOCAL Service must use LOCAL cluster")
            if self.payment_method is not MppPaymentMethod.LOCAL_TEST:
                raise MppServiceError("LOCAL Service must use LOCAL_TEST payment method")
            if not self.token.upper().startswith("LOCAL_TEST_"):
                raise MppServiceError("LOCAL Service must use LOCAL_TEST_ token")
        elif self.network is PaymentNetwork.SOLANA_DEVNET:
            if self.cluster.upper() != "DEVNET":
                raise MppServiceError("devnet Service must use DEVNET cluster")
            if self.payment_method is not MppPaymentMethod.SPL_TOKEN:
                raise MppServiceError("devnet Service must use SPL_TOKEN payment method")
            if not self.token.upper().startswith("SPL_TEST_"):
                raise MppServiceError("devnet Service must use SPL_TEST_ token")
        else:
            raise MppServiceError("paid Service is restricted to local/devnet")


class PaymentConfirmationVerifier(Protocol):
    """Trusted boundary for chain/provider confirmation checks."""

    def verify(
        self,
        *,
        service: MppServiceDefinition,
        challenge: MppChallenge,
        submission: MppServicePaymentSubmission,
    ) -> None: ...


@dataclass
class LocalFixturePaymentVerifier:
    """Structural confirmation verifier for deterministic local tests only."""

    def verify(
        self,
        *,
        service: MppServiceDefinition,
        challenge: MppChallenge,
        submission: MppServicePaymentSubmission,
    ) -> None:
        if service.network is not PaymentNetwork.LOCAL:
            raise MppServicePaymentRejected(
                "LocalFixturePaymentVerifier cannot confirm devnet payments"
            )
        if not submission.confirmed:
            raise MppServicePaymentRejected("payment is not confirmed")
        proof = submission.proof
        if not proof.external_signature.strip():
            raise MppServicePaymentRejected("payment proof has no external signature")
        if len(proof.request_hash) != 64:
            raise MppServicePaymentRejected("payment proof request hash is invalid")


@dataclass
class _IssuedChallenge:
    request: MppServiceChallengeRequest
    challenge: MppChallenge
    challenge_hash: str


@dataclass
class MppPaidServiceAgent:
    """Receiving-side paid Service Agent with fail-closed replay protection."""

    service: MppServiceDefinition
    executor: Callable[[MppServiceChallengeRequest], Any]
    verifier: PaymentConfirmationVerifier = field(default_factory=LocalFixturePaymentVerifier)
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc)
    )
    _issued: dict[str, _IssuedChallenge] = field(default_factory=dict, init=False, repr=False)
    _idempotency: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _used_receipts: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _used_signatures: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _results: dict[str, MppServiceExecutionResult] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def issue_challenge(self, request: MppServiceChallengeRequest) -> MppChallenge:
        if self.service.allowed_tasks and request.task_id not in self.service.allowed_tasks:
            raise MppServicePaymentRejected("Task is not allowlisted for this Service")
        with self._lock:
            existing_challenge_id = self._idempotency.get(request.idempotency_key)
            if existing_challenge_id is not None:
                issued = self._issued[existing_challenge_id]
                if issued.request != request:
                    raise MppServiceReplayError(
                        "idempotency key is already bound to a different Service request"
                    )
                return issued.challenge

            challenge_id = f"mpp-service-challenge-{uuid4().hex}"
            challenge = MppChallenge(
                payment_id=f"mpp-service-payment-{uuid4().hex}",
                challenge_id=challenge_id,
                service_id=self.service.service_id,
                task_id=request.task_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                correlation_id=request.correlation_id,
                idempotency_key=request.idempotency_key,
                nonce=uuid4().hex,
                payment_method=self.service.payment_method,
                network=self.service.network,
                cluster=self.service.cluster,
                token=self.service.token,
                recipient=self.service.recipient,
                amount_units=str(self.service.amount_units),
                expires_at=self.clock().astimezone(timezone.utc)
                + timedelta(seconds=self.service.expires_seconds),
            )
            issued = _IssuedChallenge(
                request=request,
                challenge=challenge,
                challenge_hash=challenge.challenge_hash(),
            )
            self._issued[challenge_id] = issued
            self._idempotency[request.idempotency_key] = challenge_id
            return challenge

    def execute(
        self,
        submission: MppServicePaymentSubmission,
    ) -> MppServiceExecutionResult:
        with self._lock:
            issued = self._issued.get(submission.challenge_id)
            if issued is None:
                raise MppServicePaymentRejected("unknown Service Challenge")
            challenge = issued.challenge
            request = issued.request
            now = self.clock().astimezone(timezone.utc)
            if challenge.expires_at <= now:
                raise MppServicePaymentRejected("Service Challenge has expired")
            if submission.challenge_hash != issued.challenge_hash:
                raise MppServicePaymentRejected("Service Challenge hash mismatch")
            if submission.cluster.upper() != (challenge.cluster or "").upper():
                raise MppServicePaymentRejected("payment cluster does not match Challenge")

            proof = submission.proof
            expected_payment_id = challenge.payment_id or f"mpp-payment-{challenge.challenge_id}"
            if (
                proof.payment_id != expected_payment_id
                or proof.challenge_id != challenge.challenge_id
                or proof.idempotency_key != challenge.idempotency_key
                or proof.network is not challenge.network
                or proof.token != challenge.token
                or proof.amount_units != challenge.amount
            ):
                raise MppServicePaymentRejected("payment proof does not match Challenge")

            existing_receipt = self._used_receipts.get(submission.receipt_id)
            if existing_receipt is not None and existing_receipt != challenge.challenge_id:
                raise MppServiceReplayError("receipt was already used by another Challenge")
            existing_signature = self._used_signatures.get(proof.external_signature)
            if existing_signature is not None and existing_signature != challenge.challenge_id:
                raise MppServiceReplayError("payment proof was already used by another Challenge")

            existing_result = self._results.get(challenge.idempotency_key)
            if existing_result is not None:
                if existing_result.receipt_id != submission.receipt_id:
                    raise MppServiceReplayError(
                        "idempotent retry must reuse the original receipt"
                    )
                return existing_result.model_copy(update={"replayed": True})

            self.verifier.verify(
                service=self.service,
                challenge=challenge,
                submission=submission,
            )
            service_result = self.executor(request)
            audit = {
                "event_type": "MPP_SERVICE_EXECUTED",
                "service_id": self.service.service_id,
                "payment_id": expected_payment_id,
                "challenge_id": challenge.challenge_id,
                "challenge_hash": issued.challenge_hash,
                "receipt_id": submission.receipt_id,
                "payer_agent_id": request.agent_id,
                "task_id": request.task_id,
                "run_id": request.run_id,
                "trace_id": request.trace_id,
                "correlation_id": request.correlation_id,
                "network": challenge.network.value,
                "cluster": challenge.cluster,
                "token": challenge.token,
                "amount_units": challenge.amount,
                "status": "SUCCEEDED",
            }
            result = MppServiceExecutionResult(
                service_id=self.service.service_id,
                payment_id=expected_payment_id,
                challenge_id=challenge.challenge_id,
                receipt_id=submission.receipt_id,
                task_id=request.task_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                correlation_id=request.correlation_id,
                result=service_result,
                audit=audit,
            )
            self._used_receipts[submission.receipt_id] = challenge.challenge_id
            self._used_signatures[proof.external_signature] = challenge.challenge_id
            self._results[challenge.idempotency_key] = result
            return result


@dataclass
class MppServiceRegistry:
    """Explicit allowlist of MPP-capable paid Services."""

    services: dict[str, MppPaidServiceAgent] = field(default_factory=dict)

    def register(self, agent: MppPaidServiceAgent) -> None:
        service_id = agent.service.service_id
        if service_id in self.services:
            raise MppServiceError(f"Service is already registered: {service_id}")
        self.services[service_id] = agent

    def require(self, service_id: str) -> MppPaidServiceAgent:
        try:
            return self.services[service_id]
        except KeyError as exc:
            raise MppServiceNotFound(f"Service is not registered: {service_id}") from exc
