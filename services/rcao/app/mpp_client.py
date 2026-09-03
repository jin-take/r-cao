"""MPP HTTP 402 client and provider adapter boundary.

The client turns an HTTP 402 response into the versioned, structured MPP
Challenge used by the R-CAO Policy Engine.  A Challenge is an untrusted
payment request, never an authorization.  The client therefore validates the
wire schema, Payment Profile, Phase, Task correlation, and idempotency before
asking the policy-bound Signer Gateway for a public payment proof.

The reference flow is intentionally provider-neutral:

``402 -> parse/validate -> Profile/Policy -> optional Owner approval ->
Signer Gateway -> proof/receipt retry``

Only the same Challenge and idempotency key may be retried.  The client has no
key store and never receives a signed transaction or private credential.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Protocol
from uuid import uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from .mpp_policy import MppPolicyEngine
from .payment_boundary import PaymentNetwork, PaymentPurpose, ServicePaymentRequest
from .payment_profile import AgentPaymentProfile
from .policy import Phase, PolicyDecision
from .signer import (
    PolicyBoundSignerGateway,
    SignerNetwork,
    SignerReceipt,
    SignerRequest,
    SignerRequestStatus,
    SignerResult,
    SignerWallet,
    SOLANA_TOKEN_PROGRAM_ID,
)


MPP_CHALLENGE_SCHEMA_VERSION = "rcao-mpp-profile-v1"
MPP_PROOF_SCHEMA_VERSION = "rcao-mpp-proof-v1"
MPP_RECEIPT_SCHEMA_VERSION = "rcao-mpp-receipt-v1"
_DECIMAL_AMOUNT = re.compile(r"^[1-9][0-9]*$")
_FORBIDDEN_TOKENS = frozenset(
    {"SOL", "VIRTUAL", "VIRTUAL_REWARD", "REWARD", "TREASURY"}
)


class MppClientError(ValueError):
    """Base error for MPP Client and provider adapter violations."""


class MppChallengeError(MppClientError):
    """An HTTP 402 Challenge is malformed, expired, or tampered."""


class MppProviderError(MppClientError):
    """A provider adapter is missing or returned an invalid response."""


class MppIdempotencyError(MppClientError):
    """A Challenge/idempotency key was reused for another payment."""


class MppApprovalError(MppClientError):
    """An Owner approval resume operation is invalid."""


class MppPaymentMethod(str, Enum):
    LOCAL_TEST = "LOCAL_TEST"
    SPL_TOKEN = "SPL_TOKEN"


class MppClientStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    DENIED = "DENIED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class MppHttpResponse(BaseModel):
    """Small HTTP response contract passed from an HTTP adapter to the client."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    status_code: StrictInt = Field(ge=100, le=599)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any


class MppChallenge(BaseModel):
    """Strict, untrusted representation of an HTTP 402 payment challenge."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )

    schema_version: Literal[MPP_CHALLENGE_SCHEMA_VERSION] = MPP_CHALLENGE_SCHEMA_VERSION
    payment_id: str | None = Field(default=None, min_length=1, max_length=200)
    challenge_id: str = Field(min_length=1, max_length=200)
    service_id: str = Field(min_length=1, max_length=300)
    task_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    nonce: str = Field(min_length=1, max_length=200)
    payment_method: MppPaymentMethod = Field(
        validation_alias=AliasChoices("payment_method", "method")
    )
    network: PaymentNetwork
    cluster: str | None = Field(default=None, min_length=1, max_length=80)
    token: str = Field(min_length=1, max_length=100)
    token_mint: str | None = Field(default=None, min_length=1, max_length=120)
    program_id: str | None = Field(default=None, min_length=1, max_length=200)
    recipient: str = Field(min_length=1, max_length=300)
    recipient_kind: Literal["SERVICE"] = "SERVICE"
    source_token_account: str | None = Field(default=None, min_length=1, max_length=120)
    recipient_token_account: str | None = Field(default=None, min_length=1, max_length=120)
    amount_units: StrictStr
    purpose: Literal["SERVICE_PAYMENT"] = "SERVICE_PAYMENT"
    expires_at: datetime

    @field_validator(
        "payment_id",
        "challenge_id",
        "service_id",
        "task_id",
        "run_id",
        "trace_id",
        "correlation_id",
        "idempotency_key",
        "nonce",
        "cluster",
        "token",
        "token_mint",
        "program_id",
        "recipient",
        "source_token_account",
        "recipient_token_account",
    )
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("MPP Challenge identifiers cannot contain control characters")
        return value

    @field_validator("amount_units")
    @classmethod
    def require_decimal_amount(cls, value: str) -> str:
        if not _DECIMAL_AMOUNT.fullmatch(value):
            raise ValueError("amount_units must be a positive decimal string")
        amount = int(value)
        if amount > (1 << 63) - 1:
            raise ValueError("amount_units exceeds BIGINT")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_expiry_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Challenge expires_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_wire_contract(self) -> "MppChallenge":
        expected_network = (
            PaymentNetwork.LOCAL
            if self.payment_method is MppPaymentMethod.LOCAL_TEST
            else PaymentNetwork.SOLANA_DEVNET
        )
        if self.network is not expected_network:
            raise ValueError("MPP payment method and network do not match")
        expected_cluster = "LOCAL" if self.network is PaymentNetwork.LOCAL else "DEVNET"
        if self.cluster is None:
            self.cluster = expected_cluster
        elif self.cluster.upper() != expected_cluster:
            raise ValueError("MPP Challenge cluster does not match network")
        if self.token.upper() in _FORBIDDEN_TOKENS:
            raise ValueError("Reward, Treasury, and SOL assets cannot be MPP payment tokens")
        lowered_recipient = self.recipient.casefold()
        if lowered_recipient.startswith(
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
        ):
            raise ValueError("MPP Challenge recipient must be an external Service")
        if self.network is PaymentNetwork.LOCAL:
            if not self.token.upper().startswith("LOCAL_TEST_"):
                raise ValueError("LOCAL Challenges must use LOCAL_TEST_ tokens")
            if self.token_mint or self.source_token_account or self.recipient_token_account:
                raise ValueError("LOCAL Challenges cannot carry SPL account fields")
        else:
            if not self.token.upper().startswith("SPL_TEST_"):
                raise ValueError("devnet Challenges must use SPL_TEST_ tokens")
            if self.program_id is not None and self.program_id != SOLANA_TOKEN_PROGRAM_ID:
                raise ValueError("devnet Challenge Program is not the SPL Token Program")
            if not self.token_mint or not self.source_token_account or not self.recipient_token_account:
                raise ValueError("devnet Challenges require mint and token accounts")
        return self

    @property
    def amount(self) -> int:
        return int(self.amount_units)

    def canonical_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True, by_alias=False)
        payload["amount_units"] = self.amount_units
        payload["expires_at"] = self.expires_at.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        return payload

    def challenge_hash(self) -> str:
        import hashlib

        encoded = json.dumps(
            self.canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_http_response(cls, response: MppHttpResponse) -> "MppChallenge":
        if response.status_code != 402:
            raise MppChallengeError("MPP Challenge must come from HTTP 402")
        content_type = next(
            (value for key, value in response.headers.items() if key.casefold() == "content-type"),
            "",
        )
        if content_type and "json" not in content_type.casefold():
            raise MppChallengeError("MPP 402 Challenge must use a JSON content type")
        body = _json_object(response.body, "MPP Challenge")
        try:
            return cls.model_validate(body)
        except ValueError as exc:
            raise MppChallengeError(str(exc)) from exc


class MppPaymentProof(BaseModel):
    """Public proof sent on the single HTTP retry; it contains no signed bytes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[MPP_PROOF_SCHEMA_VERSION] = MPP_PROOF_SCHEMA_VERSION
    payment_id: str
    challenge_id: str
    idempotency_key: str
    signer_request_id: str
    signer_result_id: str
    signer_receipt_id: str
    request_hash: str = Field(min_length=64, max_length=64)
    external_signature: str = Field(min_length=1, max_length=500)
    network: PaymentNetwork
    token: str
    amount_units: StrictInt = Field(gt=0, le=(1 << 63) - 1)

    @classmethod
    def from_signer(
        cls,
        *,
        payment: ServicePaymentRequest,
        result: SignerResult,
        receipt: SignerReceipt,
    ) -> "MppPaymentProof":
        if result.status not in {SignerRequestStatus.SUBMITTED, SignerRequestStatus.CONFIRMED}:
            raise MppProviderError("only a submitted/confirmed Signer result can become proof")
        if not result.external_signature or not result.receipt_id:
            raise MppProviderError("Signer result has no public payment proof")
        if (
            result.payment_id != payment.payment_id
            or receipt.payment_id != payment.payment_id
            or receipt.challenge_id != payment.challenge_id
            or receipt.task_id != payment.task_id
            or receipt.run_id != payment.run_id
            or receipt.trace_id != payment.trace_id
            or receipt.correlation_id != payment.correlation_id
            or receipt.network.value != payment.network.value
            or receipt.token != payment.token
            or receipt.amount_units != payment.amount_units
            or receipt.request_hash != result.request_hash
            or receipt.result_id != result.result_id
            or receipt.request_id != result.request_id
            or receipt.receipt_id != result.receipt_id
            or receipt.status is not result.status
            or result.network.value != payment.network.value
        ):
            raise MppProviderError("Signer receipt does not match Payment")
        return cls(
            payment_id=payment.payment_id,
            challenge_id=payment.challenge_id,
            idempotency_key=payment.idempotency_key,
            signer_request_id=result.request_id,
            signer_result_id=result.result_id,
            signer_receipt_id=receipt.receipt_id,
            request_hash=result.request_hash,
            external_signature=result.external_signature,
            network=payment.network,
            token=payment.token,
            amount_units=payment.amount_units,
        )


class MppProviderReceipt(BaseModel):
    """Strict provider response body returned after a proof retry."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[MPP_RECEIPT_SCHEMA_VERSION] = MPP_RECEIPT_SCHEMA_VERSION
    receipt_id: str = Field(min_length=1, max_length=200)
    payment_id: str = Field(min_length=1, max_length=200)
    challenge_id: str = Field(min_length=1, max_length=200)
    signer_request_id: str = Field(min_length=1, max_length=200)
    signer_result_id: str = Field(min_length=1, max_length=200)
    external_signature: str = Field(min_length=1, max_length=500)
    status: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def require_success_status(self) -> "MppProviderReceipt":
        if self.status not in {"PAID", "CONFIRMED"}:
            raise ValueError("MPP provider receipt is not a successful payment")
        return self


class MppPaymentReceipt(BaseModel):
    """Client receipt combining provider confirmation and Signer proof."""

    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    provider_id: str
    payment_id: str
    challenge_id: str
    signer_request_id: str
    signer_result_id: str
    external_signature: str
    response_status: StrictInt = Field(ge=200, le=299)
    received_at: datetime


class MppProviderAdapter(Protocol):
    provider_id: str

    def parse_challenge(self, response: MppHttpResponse) -> MppChallenge: ...

    def submit_payment(
        self,
        challenge: MppChallenge,
        proof: MppPaymentProof,
    ) -> MppHttpResponse: ...


@dataclass
class MppProviderRegistry:
    """Explicit provider adapter registry; unknown providers fail closed."""

    adapters: dict[str, MppProviderAdapter] = field(default_factory=dict)

    def register(self, adapter: MppProviderAdapter) -> None:
        provider_id = str(adapter.provider_id).strip()
        if not provider_id or any(ord(character) < 32 for character in provider_id):
            raise MppProviderError("provider_id is required and cannot contain controls")
        if provider_id in self.adapters:
            raise MppProviderError(f"provider adapter is already registered: {provider_id}")
        self.adapters[provider_id] = adapter

    def require(self, provider_id: str) -> MppProviderAdapter:
        try:
            return self.adapters[provider_id]
        except KeyError as exc:
            raise MppProviderError(f"provider adapter is not registered: {provider_id}") from exc


@dataclass
class MockMppService:
    """Deterministic 402 -> proof -> receipt service used by local tests."""

    challenge: MppChallenge
    requests: list[dict[str, Any]] = field(default_factory=list)
    _receipts: dict[str, MppProviderReceipt] = field(default_factory=dict, init=False, repr=False)

    def challenge_response(self) -> MppHttpResponse:
        return MppHttpResponse(
            status_code=402,
            headers={"content-type": "application/json"},
            body=self.challenge.model_dump(mode="json", exclude_none=True),
        )

    def submit(self, challenge: MppChallenge, proof: MppPaymentProof) -> MppHttpResponse:
        self.requests.append(
            {
                "challenge_id": challenge.challenge_id,
                "idempotency_key": proof.idempotency_key,
                "proof": proof.model_dump(mode="json"),
            }
        )
        if proof.payment_id != (challenge.payment_id or f"mpp-payment-{challenge.challenge_id}"):
            return MppHttpResponse(status_code=400, body={"error": "payment mismatch"})
        if proof.challenge_id != challenge.challenge_id or proof.amount_units != challenge.amount:
            return MppHttpResponse(status_code=400, body={"error": "challenge mismatch"})
        existing = self._receipts.get(proof.idempotency_key)
        if existing is None:
            existing = MppProviderReceipt(
                receipt_id=f"provider-receipt-{uuid4().hex}",
                payment_id=proof.payment_id,
                challenge_id=proof.challenge_id,
                signer_request_id=proof.signer_request_id,
                signer_result_id=proof.signer_result_id,
                external_signature=proof.external_signature,
                status="PAID",
            )
            self._receipts[proof.idempotency_key] = existing
        return MppHttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=existing.model_dump(mode="json"),
        )


@dataclass
class MockMppProviderAdapter:
    """Provider adapter for the deterministic local MPP service."""

    service: MockMppService
    provider_id: str = "mock-mpp"

    def parse_challenge(self, response: MppHttpResponse) -> MppChallenge:
        return MppChallenge.from_http_response(response)

    def submit_payment(
        self,
        challenge: MppChallenge,
        proof: MppPaymentProof,
    ) -> MppHttpResponse:
        return self.service.submit(challenge, proof)


@dataclass
class MppPaymentResult:
    status: MppClientStatus
    payment: ServicePaymentRequest | None
    policy_decision: PolicyDecision | None
    challenge_hash: str
    response_status: int | None
    attempts: int
    reason: str
    proof: MppPaymentProof | None = None
    payment_receipt: MppPaymentReceipt | None = None
    signer_result: SignerResult | None = None
    signer_receipt: SignerReceipt | None = None
    replayed: bool = False


class MppClientPersistence(Protocol):
    """Public attempt history kept separate from Signer secrets."""

    def record_attempt(
        self,
        *,
        provider_id: str,
        payment: ServicePaymentRequest | None,
        challenge_hash: str,
        result: MppPaymentResult,
        sequence_number: int,
    ) -> None: ...


@dataclass
class PostgresMppClientPersistence:
    """Append MPP Client result/retry metadata through an existing transaction."""

    transaction: Any

    def record_attempt(
        self,
        *,
        provider_id: str,
        payment: ServicePaymentRequest | None,
        challenge_hash: str,
        result: MppPaymentResult,
        sequence_number: int,
    ) -> None:
        if payment is None:
            raise MppProviderError("cannot persist an MPP attempt without a Payment")
        proof_hash = None
        if result.proof is not None:
            proof_hash = hashlib.sha256(
                json.dumps(
                    result.proof.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        self.transaction.execute(
            """
            INSERT INTO mvp_mpp_client_attempts
              (id, provider_id, payment_id, challenge_id, challenge_hash,
               idempotency_key, nonce, task_id, run_id, trace_id,
               correlation_id, attempt_number, response_status, status,
               signer_request_id, signer_result_id, signer_receipt_id,
               provider_receipt_id, proof_hash, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key, attempt_number) DO NOTHING
            """,
            (
                f"mpp-client-attempt-{uuid4().hex}",
                provider_id,
                payment.payment_id,
                payment.challenge_id,
                challenge_hash,
                payment.idempotency_key,
                payment.nonce,
                payment.task_id,
                payment.run_id,
                payment.trace_id,
                payment.correlation_id,
                sequence_number,
                result.response_status,
                result.status.value,
                result.signer_result.request_id if result.signer_result else None,
                result.signer_result.result_id if result.signer_result else None,
                result.signer_receipt.receipt_id if result.signer_receipt else None,
                result.payment_receipt.receipt_id if result.payment_receipt else None,
                proof_hash,
                result.reason,
            ),
        )


@dataclass
class _MppSession:
    challenge: MppChallenge
    challenge_hash: str
    payment: ServicePaymentRequest | None
    evaluation: Any | None = None
    result: MppPaymentResult | None = None
    persistence_sequence: int = 0


@dataclass
class MppClient:
    """Policy-bound MPP Client with one proof retry and replay protection."""

    profile: AgentPaymentProfile
    wallet: SignerWallet
    policy_engine: MppPolicyEngine
    signer_gateway: PolicyBoundSignerGateway
    provider_registry: MppProviderRegistry
    provider_id: str
    phase: Phase = Phase.DEVNET
    service_registered: bool = True
    task_allowlist: tuple[str, ...] = ()
    run_allowlist: tuple[str, ...] = ()
    persistence: MppClientPersistence | None = None
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc)
    )
    _sessions: dict[str, _MppSession] = field(default_factory=dict, init=False, repr=False)
    _challenge_keys: dict[tuple[str, str], str] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def pay(self, response: MppHttpResponse) -> MppPaymentResult:
        adapter = self.provider_registry.require(self.provider_id)
        challenge = adapter.parse_challenge(response)
        return self.pay_challenge(challenge)

    def pay_response(self, response: MppHttpResponse) -> MppPaymentResult:
        """Compatibility alias for callers that name the HTTP boundary explicitly."""

        return self.pay(response)

    def pay_challenge(self, challenge: MppChallenge) -> MppPaymentResult:
        # Hold the client lock across the complete side-effecting flow. This
        # prevents two concurrent HTTP workers from both observing an absent
        # session and submitting the same proof twice.
        with self._lock:
            return self._pay_challenge(challenge)

    def _pay_challenge(self, challenge: MppChallenge) -> MppPaymentResult:
        challenge_hash = challenge.challenge_hash()
        with self._lock:
            existing = self._sessions.get(challenge.idempotency_key)
            if existing is not None:
                if existing.challenge_hash != challenge_hash:
                    raise MppIdempotencyError(
                        "MPP idempotency key is bound to a different Challenge"
                    )
                if existing.result is None:
                    raise MppClientError("MPP session has no result")
                return self._replayed(existing.result)
            challenge_key = (challenge.challenge_id, challenge.nonce)
            existing_idempotency = self._challenge_keys.get(challenge_key)
            if existing_idempotency is not None and existing_idempotency != challenge.idempotency_key:
                raise MppIdempotencyError("MPP Challenge nonce was already used")
            self._challenge_keys[challenge_key] = challenge.idempotency_key

        payment = self._payment_from_challenge(challenge)
        session = _MppSession(challenge=challenge, challenge_hash=challenge_hash, payment=payment)
        self._sessions[challenge.idempotency_key] = session

        if self.phase is not Phase.DEVNET:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.DENIED,
                    session=session,
                    policy_decision=PolicyDecision.DENY,
                    attempts=1,
                    reason="MPP Client is enabled only in the DEVNET phase",
                ),
            )
        if not self.service_registered:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.DENIED,
                    session=session,
                    policy_decision=PolicyDecision.DENY,
                    attempts=1,
                    reason="MPP provider is not registered as an approved Service",
                ),
            )
        profile_reason = self._profile_mismatch_reason(challenge)
        if profile_reason is not None:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.DENIED,
                    session=session,
                    policy_decision=PolicyDecision.DENY,
                    attempts=1,
                    reason=profile_reason,
                ),
            )
        scope_reason = self._scope_mismatch_reason(challenge)
        if scope_reason is not None:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.DENIED,
                    session=session,
                    policy_decision=PolicyDecision.DENY,
                    attempts=1,
                    reason=scope_reason,
                ),
            )

        evaluation = self.policy_engine.evaluate(
            payment=payment,
            profile=self.profile,
            phase=self.phase,
            now=_utc(self.clock()),
            service_registered=True,
            profile_allows=True,
        )
        session.evaluation = evaluation
        if evaluation.decision is PolicyDecision.DENY:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.DENIED,
                    session=session,
                    policy_decision=evaluation.decision,
                    attempts=1,
                    reason=evaluation.reason,
                ),
            )
        if evaluation.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.PENDING_APPROVAL,
                    session=session,
                    policy_decision=evaluation.decision,
                    attempts=1,
                    reason=evaluation.reason,
                ),
            )
        return self._execute_and_retry(session, evaluation)

    def resume(
        self,
        idempotency_key: str,
        *,
        approved: bool,
        approval_id: str | None = None,
    ) -> MppPaymentResult:
        # Approval callbacks may race with one another; serialize resume with
        # pay_challenge so a single pending session can produce one proof.
        with self._lock:
            return self._resume(
                idempotency_key,
                approved=approved,
                approval_id=approval_id,
            )

    def _resume(
        self,
        idempotency_key: str,
        *,
        approved: bool,
        approval_id: str | None = None,
    ) -> MppPaymentResult:
        with self._lock:
            session = self._sessions.get(idempotency_key)
        if session is None or session.result is None:
            raise MppApprovalError("MPP approval session is not registered")
        if session.result.status is not MppClientStatus.PENDING_APPROVAL:
            return self._replayed(session.result)
        if not approved:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.DENIED,
                    session=session,
                    policy_decision=PolicyDecision.DENY,
                    attempts=session.result.attempts,
                    reason="Owner approval was not granted",
                ),
            )
        if not approval_id:
            raise MppApprovalError("approved MPP payment requires approval_id")
        if session.payment is None:
            raise MppApprovalError("approval session has no Payment")
        now = _utc(self.clock())
        evaluation = self.policy_engine.evaluate(
            payment=session.payment,
            profile=self.profile,
            phase=self.phase,
            now=now,
            service_registered=self.service_registered,
            profile_allows=True,
            owner_approval_id=approval_id,
            approval_verified=True,
        )
        session.evaluation = evaluation
        if evaluation.decision is not PolicyDecision.ALLOW:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.DENIED,
                    session=session,
                    policy_decision=evaluation.decision,
                    attempts=session.result.attempts,
                    reason=evaluation.reason,
                ),
            )
        return self._execute_and_retry(session, evaluation)

    def _execute_and_retry(self, session: _MppSession, evaluation: Any) -> MppPaymentResult:
        if session.payment is None:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.FAILED,
                    session=session,
                    policy_decision=evaluation.decision,
                    attempts=1,
                    reason="MPP session has no Payment",
                ),
            )
        try:
            authorization = self.policy_engine.issue_signer_authorization(
                evaluation,
                now=_utc(self.clock()),
                approval_verified=True,
            )
            self.signer_gateway.register_authorization(authorization)
            signer_request = SignerRequest.from_payment(
                session.payment,
                profile=self.profile,
                authorization=authorization,
                wallet=self.wallet,
                program_id=session.challenge.program_id,
                token_mint=session.challenge.token_mint,
                source_token_account=session.challenge.source_token_account,
                recipient_token_account=session.challenge.recipient_token_account,
            )
            signer_result, signer_receipt = self.signer_gateway.execute(
                signer_request,
                authorization,
            )
        except Exception as exc:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.FAILED,
                    session=session,
                    policy_decision=evaluation.decision,
                    attempts=1,
                    reason=f"Signer request was rejected: {str(exc)[:500]}",
                ),
            )
        if signer_result.status is SignerRequestStatus.STOPPED:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.STOPPED,
                    session=session,
                    policy_decision=evaluation.decision,
                    attempts=1,
                    reason=signer_result.failure_message or "Signer stop control is active",
                    signer_result=signer_result,
                    signer_receipt=signer_receipt,
                ),
            )
        if signer_result.status not in {
            SignerRequestStatus.SUBMITTED,
            SignerRequestStatus.CONFIRMED,
        } or signer_receipt is None:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.FAILED,
                    session=session,
                    policy_decision=evaluation.decision,
                    attempts=1,
                    reason=signer_result.failure_message or "Signer did not produce a payment proof",
                    signer_result=signer_result,
                    signer_receipt=signer_receipt,
                ),
            )

        response_status: int | None = None
        try:
            proof = MppPaymentProof.from_signer(
                payment=session.payment,
                result=signer_result,
                receipt=signer_receipt,
            )
            response = self.provider_registry.require(self.provider_id).submit_payment(
                session.challenge,
                proof,
            )
            response_status = response.status_code
            if not 200 <= response.status_code <= 299:
                raise MppProviderError(
                    f"provider rejected payment proof with HTTP {response.status_code}"
                )
            provider_receipt = MppProviderReceipt.model_validate(
                _json_object(response.body, "MPP provider receipt")
            )
            if (
                provider_receipt.payment_id != proof.payment_id
                or provider_receipt.challenge_id != proof.challenge_id
                or provider_receipt.signer_request_id != proof.signer_request_id
                or provider_receipt.signer_result_id != proof.signer_result_id
                or provider_receipt.external_signature != proof.external_signature
            ):
                raise MppProviderError("provider receipt does not match the Signer proof")
            payment_receipt = MppPaymentReceipt(
                receipt_id=provider_receipt.receipt_id,
                provider_id=self.provider_id,
                payment_id=proof.payment_id,
                challenge_id=proof.challenge_id,
                signer_request_id=proof.signer_request_id,
                signer_result_id=proof.signer_result_id,
                external_signature=proof.external_signature,
                response_status=response.status_code,
                received_at=_utc(self.clock()),
            )
        except Exception as exc:
            return self._finish(
                session,
                self._result(
                    status=MppClientStatus.FAILED,
                    session=session,
                    policy_decision=evaluation.decision,
                    attempts=2,
                    reason=f"MPP proof retry failed: {str(exc)[:500]}",
                    response_status=response_status,
                    signer_result=signer_result,
                    signer_receipt=signer_receipt,
                ),
            )
        return self._finish(
            session,
            self._result(
                status=MppClientStatus.SUCCEEDED,
                session=session,
                policy_decision=evaluation.decision,
                attempts=2,
                reason="MPP payment confirmed by provider receipt",
                proof=proof,
                payment_receipt=payment_receipt,
                signer_result=signer_result,
                signer_receipt=signer_receipt,
            ),
        )

    def _payment_from_challenge(self, challenge: MppChallenge) -> ServicePaymentRequest:
        payment_id = challenge.payment_id or f"mpp-payment-{challenge.challenge_id}"
        return ServicePaymentRequest(
            payment_id=payment_id,
            idempotency_key=challenge.idempotency_key,
            challenge_id=challenge.challenge_id,
            nonce=challenge.nonce,
            task_id=challenge.task_id,
            run_id=challenge.run_id,
            trace_id=challenge.trace_id,
            correlation_id=challenge.correlation_id,
            agent_id=self.profile.agent_id,
            service_id=challenge.service_id,
            program_id=challenge.program_id,
            profile_id=self.profile.profile_id,
            profile_version=self.profile.version,
            recipient=challenge.recipient,
            recipient_kind=challenge.recipient_kind,
            network=challenge.network,
            token=challenge.token,
            amount_units=challenge.amount,
            purpose=PaymentPurpose.SERVICE_PAYMENT,
            expires_at=challenge.expires_at,
        )

    def _profile_mismatch_reason(self, challenge: MppChallenge) -> str | None:
        if challenge.network.value != self.profile.network.value:
            return "Challenge network is outside the Payment Profile"
        if challenge.service_id != self.profile.service_id or challenge.service_id not in self.profile.service_allowlist:
            return "Challenge Service is outside the Payment Profile"
        if challenge.recipient != self.profile.recipient or challenge.recipient not in self.profile.recipient_allowlist:
            return "Challenge recipient is outside the Payment Profile"
        if challenge.token not in self.profile.token_allowlist:
            return "Challenge token is outside the Payment Profile"
        if challenge.program_id is not None:
            if not self.profile.program_allowlist:
                return "Challenge Program is not allowlisted by the Payment Profile"
            if challenge.program_id not in self.profile.program_allowlist:
                return "Challenge Program is outside the Payment Profile"
        if challenge.network is PaymentNetwork.SOLANA_DEVNET:
            if not self.profile.mint_allowlist or challenge.token_mint not in self.profile.mint_allowlist:
                return "Challenge token mint is outside the Payment Profile"
            if challenge.program_id is None:
                return "devnet Challenge must identify the SPL Token Program"
        return None

    def _scope_mismatch_reason(self, challenge: MppChallenge) -> str | None:
        if self.task_allowlist and challenge.task_id not in self.task_allowlist:
            return "Challenge Task is outside the MPP Client Task scope"
        if self.run_allowlist and challenge.run_id not in self.run_allowlist:
            return "Challenge Run is outside the MPP Client Run scope"
        return None

    def _result(
        self,
        *,
        status: MppClientStatus,
        session: _MppSession,
        policy_decision: PolicyDecision | None,
        attempts: int,
        reason: str,
        response_status: int | None = None,
        proof: MppPaymentProof | None = None,
        payment_receipt: MppPaymentReceipt | None = None,
        signer_result: SignerResult | None = None,
        signer_receipt: SignerReceipt | None = None,
    ) -> MppPaymentResult:
        return MppPaymentResult(
            status=status,
            payment=session.payment,
            policy_decision=policy_decision,
            challenge_hash=session.challenge_hash,
            response_status=(
                response_status
                if response_status is not None
                else payment_receipt.response_status
                if payment_receipt
                else None
            ),
            attempts=attempts,
            reason=reason,
            proof=proof,
            payment_receipt=payment_receipt,
            signer_result=signer_result,
            signer_receipt=signer_receipt,
        )

    def _finish(self, session: _MppSession, result: MppPaymentResult) -> MppPaymentResult:
        session.result = result
        session.persistence_sequence += 1
        if self.persistence is not None:
            self.persistence.record_attempt(
                provider_id=self.provider_id,
                payment=session.payment,
                challenge_hash=session.challenge_hash,
                result=result,
                sequence_number=session.persistence_sequence,
            )
        return result

    @staticmethod
    def _replayed(result: MppPaymentResult) -> MppPaymentResult:
        return MppPaymentResult(
            status=result.status,
            payment=result.payment,
            policy_decision=result.policy_decision,
            challenge_hash=result.challenge_hash,
            response_status=result.response_status,
            attempts=result.attempts,
            reason=result.reason,
            proof=result.proof,
            payment_receipt=result.payment_receipt,
            signer_result=result.signer_result,
            signer_receipt=result.signer_receipt,
            replayed=True,
        )


def _json_object(value: Any, label: str) -> Mapping[str, Any]:
    if isinstance(value, (bytes, bytearray)):
        try:
            value = json.loads(value.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MppChallengeError(f"{label} body is not valid JSON") from exc
    elif isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MppChallengeError(f"{label} body is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise MppChallengeError(f"{label} body must be a JSON object")
    return value


def _utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MppClientError("MPP timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


__all__ = [
    "MPP_CHALLENGE_SCHEMA_VERSION",
    "MPP_PROOF_SCHEMA_VERSION",
    "MPP_RECEIPT_SCHEMA_VERSION",
    "MockMppProviderAdapter",
    "MockMppService",
    "MppApprovalError",
    "MppChallenge",
    "MppChallengeError",
    "MppClient",
    "MppClientError",
    "MppClientStatus",
    "MppClientPersistence",
    "MppHttpResponse",
    "MppIdempotencyError",
    "MppPaymentMethod",
    "MppPaymentProof",
    "MppPaymentReceipt",
    "MppPaymentResult",
    "PostgresMppClientPersistence",
    "MppProviderAdapter",
    "MppProviderError",
    "MppProviderReceipt",
    "MppProviderRegistry",
]
