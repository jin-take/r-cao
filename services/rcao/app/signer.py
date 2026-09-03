"""Policy-bound local/devnet signing boundary for MPP Service Payments.

The Signer is deliberately a small, separate capability boundary.  Callers
can construct a secret-free :class:`SignerRequest`, but they cannot obtain a
private key or invoke the signing implementation directly.  Only a
``PolicyBoundSignerGateway`` holding an internal capability can reach the
private execution method.

The reference implementation supports two deterministic environments:

* ``LOCAL`` uses an in-process transport for tests and local development.
* ``SOLANA_DEVNET`` builds a legacy SPL-token transfer transaction and sends
  it through an injected devnet-only JSON-RPC transport.

No mainnet, testnet, Reward, Treasury, Agent-to-Agent, or public Internet
signing path is provided here.  Durable request/result/receipt tables are
created by migration 0016; the in-memory stores in this module make the
boundary deterministic and easy to exercise without a network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .audit import AuditEvent, sanitize
from .mpp_policy import (
    DirectSignerCallError,
    MPP_ENGINE_POLICY_VERSION,
    MppSignerAuthorization,
    MppSignerAuthorizationError,
)
from .observability import StopController, StopTarget
from .payment_boundary import PaymentNetwork, ServicePaymentRequest
from .payment_profile import AgentPaymentProfile, PaymentProfileNetwork


MAX_SIGNED_BIGINT = (1 << 63) - 1
SOLANA_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
LOCAL_PROGRAM_ID = "LOCAL_TEST_PROGRAM"
LOCAL_TRANSFER_INSTRUCTION = "LOCAL_TEST_TRANSFER"
SPL_TRANSFER_INSTRUCTION = "SPL_TOKEN_TRANSFER"
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {character: index for index, character in enumerate(_BASE58_ALPHABET)}


class SignerBoundaryError(ValueError):
    """Base error raised when a Signer boundary contract is violated."""


class SignerValidationError(SignerBoundaryError):
    """A Signer request is malformed or outside its Policy snapshot."""


class SignerIdempotencyError(SignerBoundaryError):
    """An idempotency key was reused for a different signer request."""


class SignerTransportError(SignerBoundaryError):
    """A configured local/devnet transport rejected a submission."""


class SignerPersistenceError(SignerBoundaryError):
    """The durable Signer record could not be written."""


class SignerNetwork(str, Enum):
    LOCAL = "LOCAL"
    SOLANA_DEVNET = "SOLANA_DEVNET"


class SignerWalletStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class SignerRequestStatus(str, Enum):
    REQUESTED = "REQUESTED"
    SIGNED = "SIGNED"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class SignerWallet(BaseModel):
    """Public wallet identity; no encrypted or plaintext private material."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    wallet_id: str = Field(min_length=1, max_length=200)
    agent_id: str = Field(min_length=1, max_length=200)
    network: SignerNetwork
    cluster: str = Field(min_length=1, max_length=80)
    public_key: str = Field(min_length=1, max_length=120)
    rotation_version: StrictInt = Field(default=1, ge=1)
    status: SignerWalletStatus = SignerWalletStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    revoked_at: datetime | None = None

    @field_validator("wallet_id", "agent_id", "cluster", "public_key")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("Signer identity cannot contain control characters")
        return value

    @field_validator("created_at", "updated_at", "revoked_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Signer timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_identity(self) -> "SignerWallet":
        expected_cluster = "LOCAL" if self.network is SignerNetwork.LOCAL else "DEVNET"
        if self.cluster.upper() != expected_cluster:
            raise ValueError("Signer cluster must match the selected network")
        _decode_public_key(self.public_key)
        if self.status is SignerWalletStatus.REVOKED and self.revoked_at is None:
            raise ValueError("revoked wallets require revoked_at")
        if self.status is not SignerWalletStatus.REVOKED and self.revoked_at is not None:
            raise ValueError("only revoked wallets may have revoked_at")
        return self

    def public_state(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SignerRequest(BaseModel):
    """Secret-free, correlation-complete request accepted by the Signer."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str = Field(min_length=1, max_length=200)
    authorization_id: str = Field(min_length=1, max_length=200)
    authorization_hash: str = Field(min_length=64, max_length=64)
    policy_decision_id: str = Field(min_length=1, max_length=200)
    policy_version: str = Field(default=MPP_ENGINE_POLICY_VERSION, min_length=1, max_length=100)
    payment_id: str = Field(min_length=1, max_length=200)
    challenge_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    nonce: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)
    agent_id: str = Field(min_length=1, max_length=200)
    service_id: str = Field(min_length=1, max_length=300)
    profile_id: str = Field(min_length=1, max_length=200)
    profile_version: StrictInt = Field(ge=1)
    wallet_id: str = Field(min_length=1, max_length=200)
    wallet_public_key: str = Field(min_length=1, max_length=120)
    wallet_rotation_version: StrictInt = Field(ge=1)
    network: SignerNetwork
    cluster: str = Field(min_length=1, max_length=80)
    program_id: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=80)
    token: str = Field(min_length=1, max_length=100)
    token_mint: str | None = Field(default=None, min_length=1, max_length=120)
    recipient: str = Field(min_length=1, max_length=300)
    source_token_account: str | None = Field(default=None, min_length=1, max_length=120)
    recipient_token_account: str | None = Field(default=None, min_length=1, max_length=120)
    recent_blockhash: str | None = Field(default=None, min_length=1, max_length=120)
    amount_units: StrictInt = Field(gt=0, le=MAX_SIGNED_BIGINT)
    purpose: str = Field(min_length=1, max_length=80)
    expires_at: datetime
    per_payment_limit_units: StrictInt = Field(gt=0, le=MAX_SIGNED_BIGINT)
    per_task_limit_units: StrictInt = Field(gt=0, le=MAX_SIGNED_BIGINT)
    daily_limit_units: StrictInt = Field(gt=0, le=MAX_SIGNED_BIGINT)
    task_spent_units: StrictInt = Field(default=0, ge=0, le=MAX_SIGNED_BIGINT)
    daily_spent_units: StrictInt = Field(default=0, ge=0, le=MAX_SIGNED_BIGINT)
    token_allowlist: tuple[str, ...] = Field(min_length=1)
    recipient_allowlist: tuple[str, ...] = Field(min_length=1)
    program_allowlist: tuple[str, ...] = Field(default_factory=tuple)
    instruction_allowlist: tuple[str, ...] = Field(default_factory=tuple)
    mint_allowlist: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator(
        "request_id",
        "authorization_id",
        "policy_decision_id",
        "policy_version",
        "payment_id",
        "challenge_id",
        "idempotency_key",
        "nonce",
        "task_id",
        "run_id",
        "trace_id",
        "correlation_id",
        "agent_id",
        "service_id",
        "profile_id",
        "wallet_id",
        "wallet_public_key",
        "cluster",
        "program_id",
        "instruction",
        "token",
        "token_mint",
        "recipient",
        "source_token_account",
        "recipient_token_account",
        "recent_blockhash",
        "purpose",
    )
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(character) < 32 for character in value):
            raise ValueError("Signer request identifiers cannot contain control characters")
        return value

    @field_validator(
        "token_allowlist",
        "recipient_allowlist",
        "program_allowlist",
        "instruction_allowlist",
        "mint_allowlist",
        mode="before",
    )
    @classmethod
    def normalise_allowlist(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            values = (value,)
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = tuple(value)
        else:
            raise ValueError("Signer allowlists must contain strings")
        if not all(isinstance(item, str) and item.strip() for item in values):
            raise ValueError("Signer allowlists must contain non-empty strings")
        normalised = tuple(item.strip() for item in values)
        if len(set(normalised)) != len(normalised):
            raise ValueError("Signer allowlists cannot contain duplicates")
        if any(any(ord(character) < 32 for character in item) for item in normalised):
            raise ValueError("Signer allowlists cannot contain control characters")
        return normalised

    @field_validator("expires_at")
    @classmethod
    def require_expiry_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Signer expires_at must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_request_contract(self) -> "SignerRequest":
        expected_cluster = "LOCAL" if self.network is SignerNetwork.LOCAL else "DEVNET"
        if self.cluster.upper() != expected_cluster:
            raise ValueError("Signer request cluster must match the selected network")
        if len(self.authorization_hash) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in self.authorization_hash
        ):
            raise ValueError("authorization_hash must be a SHA-256 hex digest")
        if self.purpose != "SERVICE_PAYMENT":
            raise ValueError("Signer only accepts SERVICE_PAYMENT")
        if self.policy_version != MPP_ENGINE_POLICY_VERSION:
            raise ValueError("Signer Policy version is not supported")
        if self.recipient.casefold() == self.agent_id.casefold() or _is_internal_recipient(self.recipient):
            raise ValueError("Signer recipient must be an external Service")
        if self.amount_units > self.per_payment_limit_units:
            raise ValueError("amount exceeds the per-payment Signer limit")
        if self.amount_units + self.task_spent_units > self.per_task_limit_units:
            raise ValueError("amount exceeds the per-Task Signer limit")
        if self.amount_units + self.daily_spent_units > self.daily_limit_units:
            raise ValueError("amount exceeds the daily Signer limit")
        if self.token not in self.token_allowlist:
            raise ValueError("token is outside the Signer allowlist")
        if self.recipient not in self.recipient_allowlist:
            raise ValueError("recipient is outside the Signer allowlist")
        if self.program_allowlist and self.program_id not in self.program_allowlist:
            raise ValueError("program is outside the Signer allowlist")
        if self.instruction_allowlist and self.instruction not in self.instruction_allowlist:
            raise ValueError("instruction is outside the Signer allowlist")

        _decode_public_key(self.wallet_public_key)
        if self.network is SignerNetwork.LOCAL:
            if not self.token.upper().startswith("LOCAL_TEST_"):
                raise ValueError("LOCAL Signer tokens must use LOCAL_TEST_ fixtures")
            if self.instruction != LOCAL_TRANSFER_INSTRUCTION:
                raise ValueError("LOCAL Signer only accepts LOCAL_TEST_TRANSFER")
            if not self.program_id.upper().startswith("LOCAL_TEST_"):
                raise ValueError("LOCAL Signer program must use LOCAL_TEST_ fixtures")
            if self.token_mint or self.source_token_account or self.recipient_token_account:
                raise ValueError("LOCAL Signer requests cannot carry SPL account fields")
            if self.recent_blockhash:
                raise ValueError("LOCAL Signer requests cannot carry a blockhash")
        else:
            if not self.token.upper().startswith("SPL_TEST_"):
                raise ValueError("SOLANA_DEVNET Signer tokens must use SPL_TEST_ fixtures")
            if self.instruction != SPL_TRANSFER_INSTRUCTION:
                raise ValueError("SOLANA_DEVNET Signer only accepts SPL_TOKEN_TRANSFER")
            if self.program_id != SOLANA_TOKEN_PROGRAM_ID:
                raise ValueError("unexpected SPL Token Program")
            if not self.token_mint or not self.mint_allowlist:
                raise ValueError("devnet SPL requests require a token mint allowlist")
            if self.token_mint not in self.mint_allowlist:
                raise ValueError("token mint is outside the Signer allowlist")
            _decode_public_key(self.token_mint, allow_fixture=True)
            if not self.source_token_account or not self.recipient_token_account:
                raise ValueError("devnet SPL requests require source and recipient token accounts")
            _decode_public_key(self.source_token_account)
            _decode_public_key(self.recipient_token_account)
            if self.recent_blockhash:
                _decode_public_key(self.recent_blockhash)
        return self

    def canonical_payload(self) -> dict[str, Any]:
        """Return the exact public payload covered by ``request_hash``."""

        payload = self.model_dump(mode="json", exclude_none=True)
        for key in (
            "token_allowlist",
            "recipient_allowlist",
            "program_allowlist",
            "instruction_allowlist",
            "mint_allowlist",
        ):
            payload[key] = list(getattr(self, key))
        payload["amount_units"] = str(self.amount_units)
        for key in (
            "per_payment_limit_units",
            "per_task_limit_units",
            "daily_limit_units",
            "task_spent_units",
            "daily_spent_units",
            "profile_version",
            "wallet_rotation_version",
        ):
            payload[key] = str(getattr(self, key))
        payload["expires_at"] = self.expires_at.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        return payload

    def request_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_payment(
        cls,
        payment: ServicePaymentRequest,
        *,
        profile: AgentPaymentProfile,
        authorization: MppSignerAuthorization,
        wallet: SignerWallet,
        program_id: str | None = None,
        token_mint: str | None = None,
        source_token_account: str | None = None,
        recipient_token_account: str | None = None,
        recent_blockhash: str | None = None,
        task_spent_units: int = 0,
        daily_spent_units: int = 0,
        instruction_allowlist: tuple[str, ...] = (),
    ) -> "SignerRequest":
        if payment.agent_id != wallet.agent_id or payment.agent_id != profile.agent_id:
            raise SignerValidationError("Payment, Profile, and Wallet Agent identities must match")
        if payment.network.value != wallet.network.value or payment.network.value != profile.network.value:
            raise SignerValidationError("Payment, Profile, and Wallet networks must match")
        selected_program = program_id
        if selected_program is None:
            selected_program = (
                profile.program_allowlist[0]
                if profile.program_allowlist
                else (
                    LOCAL_PROGRAM_ID
                    if wallet.network is SignerNetwork.LOCAL
                    else SOLANA_TOKEN_PROGRAM_ID
                )
            )
        selected_mint = token_mint
        if selected_mint is None and wallet.network is SignerNetwork.SOLANA_DEVNET:
            selected_mint = profile.mint_allowlist[0] if profile.mint_allowlist else None
        selected_instruction = (
            LOCAL_TRANSFER_INSTRUCTION
            if wallet.network is SignerNetwork.LOCAL
            else SPL_TRANSFER_INSTRUCTION
        )
        return cls(
            request_id=f"signer-request-{payment.payment_id}",
            authorization_id=authorization.authorization_id,
            authorization_hash=authorization.authorization_hash,
            policy_decision_id=authorization.policy_decision_id,
            policy_version=authorization.policy_version,
            payment_id=payment.payment_id,
            challenge_id=payment.challenge_id,
            idempotency_key=payment.idempotency_key,
            nonce=payment.nonce,
            task_id=payment.task_id,
            run_id=payment.run_id,
            trace_id=payment.trace_id,
            correlation_id=payment.correlation_id,
            agent_id=payment.agent_id,
            service_id=payment.service_id,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            wallet_id=wallet.wallet_id,
            wallet_public_key=wallet.public_key,
            wallet_rotation_version=wallet.rotation_version,
            network=wallet.network,
            cluster=wallet.cluster,
            program_id=selected_program,
            instruction=selected_instruction,
            token=payment.token,
            token_mint=selected_mint,
            recipient=payment.recipient,
            source_token_account=source_token_account,
            recipient_token_account=recipient_token_account,
            recent_blockhash=recent_blockhash,
            amount_units=payment.amount_units,
            purpose=payment.purpose.value,
            expires_at=payment.expires_at,
            per_payment_limit_units=profile.per_payment_limit_units,
            per_task_limit_units=profile.per_task_limit_units,
            daily_limit_units=profile.daily_limit_units,
            task_spent_units=task_spent_units,
            daily_spent_units=daily_spent_units,
            token_allowlist=profile.token_allowlist,
            recipient_allowlist=profile.recipient_allowlist,
            program_allowlist=profile.program_allowlist,
            instruction_allowlist=instruction_allowlist,
            mint_allowlist=profile.mint_allowlist,
        )


class SignerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    result_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    authorization_id: str = Field(min_length=1, max_length=200)
    payment_id: str = Field(min_length=1, max_length=200)
    request_hash: str = Field(min_length=64, max_length=64)
    status: SignerRequestStatus
    network: SignerNetwork
    cluster: str
    receipt_id: str | None = None
    external_signature: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime
    completed_at: datetime


class SignerReceipt(BaseModel):
    """Public payment proof correlating the Signer result to the Task trace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_id: str = Field(min_length=1, max_length=200)
    result_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    authorization_id: str = Field(min_length=1, max_length=200)
    payment_id: str = Field(min_length=1, max_length=200)
    challenge_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=200)
    trace_id: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=200)
    network: SignerNetwork
    cluster: str
    token: str
    amount_units: StrictInt = Field(gt=0, le=MAX_SIGNED_BIGINT)
    status: SignerRequestStatus
    request_hash: str = Field(min_length=64, max_length=64)
    external_signature: str | None = None
    created_at: datetime


class SignerSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SignerRequestStatus
    external_signature: str | None = None
    provider_receipt: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None


class SignerTransport(Protocol):
    network: SignerNetwork

    def recent_blockhash(self, request: SignerRequest) -> str | None: ...

    def submit(self, request: SignerRequest, signed_payload: bytes) -> SignerSubmission: ...


@dataclass
class EncryptedKeyStore:
    """AES-GCM key store whose public API exposes only wallet identities.

    The master key is supplied by process configuration in production.  When
    omitted, a process-local random key is used for local/devnet development;
    it is intentionally not persisted.  The encrypted record contains only
    nonce, ciphertext, and public metadata and is written with mode 0600.
    """

    master_key: bytes | None = None
    directory: Path | None = None
    _records: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.master_key is None:
            self.master_key = secrets.token_bytes(32)
        if len(self.master_key) != 32:
            raise ValueError("Signer key-store master_key must be exactly 32 bytes")
        if self.directory is not None:
            self.directory = Path(self.directory)
            self.directory.mkdir(parents=True, exist_ok=True)
            for path in self.directory.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    wallet_id = str(record["wallet_id"])
                    self._records[wallet_id] = record
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    raise SignerBoundaryError(f"invalid encrypted wallet record: {path.name}")

    def generate_wallet(
        self,
        *,
        wallet_id: str,
        agent_id: str,
        network: SignerNetwork | PaymentProfileNetwork | str,
        cluster: str | None = None,
    ) -> SignerWallet:
        selected_network = _as_network(network)
        selected_cluster = cluster or ("LOCAL" if selected_network is SignerNetwork.LOCAL else "DEVNET")
        key = Ed25519PrivateKey.generate()
        public_key = _encode_base58(
            key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        now = datetime.now(timezone.utc)
        wallet = SignerWallet(
            wallet_id=wallet_id,
            agent_id=agent_id,
            network=selected_network,
            cluster=selected_cluster,
            public_key=public_key,
            created_at=now,
            updated_at=now,
        )
        self._store_private_key(wallet, key)
        return wallet

    def public_identity(self, wallet_id: str) -> SignerWallet:
        with self._lock:
            record = self._records.get(wallet_id)
            if record is None:
                raise SignerBoundaryError(f"wallet is not registered: {wallet_id}")
            return SignerWallet.model_validate(record["public"])

    def rotate_wallet(self, wallet_id: str) -> SignerWallet:
        with self._lock:
            old = self.public_identity(wallet_id)
            if old.status is SignerWalletStatus.REVOKED:
                raise SignerBoundaryError("a revoked wallet cannot be rotated")
            key = Ed25519PrivateKey.generate()
            public_key = _encode_base58(
                key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            )
            now = datetime.now(timezone.utc)
            wallet = SignerWallet(
                wallet_id=old.wallet_id,
                agent_id=old.agent_id,
                network=old.network,
                cluster=old.cluster,
                public_key=public_key,
                rotation_version=old.rotation_version + 1,
                status=SignerWalletStatus.ACTIVE,
                created_at=old.created_at,
                updated_at=now,
            )
            self._store_private_key(wallet, key)
            return wallet

    def revoke_wallet(self, wallet_id: str) -> SignerWallet:
        with self._lock:
            old = self.public_identity(wallet_id)
            now = datetime.now(timezone.utc)
            wallet = old.model_copy(
                update={"status": SignerWalletStatus.REVOKED, "updated_at": now, "revoked_at": now}
            )
            record = self._records[wallet_id]
            record["public"] = wallet.public_state()
            self._persist(wallet_id, record)
            return wallet

    def _decrypt_private_key(self, wallet: SignerWallet) -> Ed25519PrivateKey:
        """Private method used only inside Signer execution."""

        with self._lock:
            record = self._records.get(wallet.wallet_id)
            if record is None:
                raise SignerBoundaryError("wallet key material is not registered")
            if record["public"] != wallet.public_state():
                raise SignerBoundaryError("wallet public identity has changed")
            nonce = base64.b64decode(record["nonce"])
            ciphertext = base64.b64decode(record["ciphertext"])
            aad = _wallet_aad(wallet)
            raw = AESGCM(self.master_key).decrypt(nonce, ciphertext, aad)
            key = Ed25519PrivateKey.from_private_bytes(raw)
            derived_public = _encode_base58(
                key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            )
            if derived_public != wallet.public_key:
                raise SignerBoundaryError("encrypted wallet key does not match public identity")
            return key

    def _store_private_key(self, wallet: SignerWallet, key: Ed25519PrivateKey) -> None:
        raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.master_key).encrypt(nonce, raw, _wallet_aad(wallet))
        record = {
            "wallet_id": wallet.wallet_id,
            "public": wallet.public_state(),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        with self._lock:
            self._records[wallet.wallet_id] = record
            self._persist(wallet.wallet_id, record)

    def _persist(self, wallet_id: str, record: Mapping[str, Any]) -> None:
        if self.directory is None:
            return
        target = self.directory / f"{_safe_filename(wallet_id)}.json"
        temporary = self.directory / f".{_safe_filename(wallet_id)}.{uuid4().hex}.tmp"
        temporary.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        os.chmod(target, 0o600)


@dataclass
class SignerAuthorizationRegistry:
    """In-process registry of Policy-issued, single-use capabilities."""

    authorizations: dict[str, MppSignerAuthorization] = field(default_factory=dict)
    revoked: set[str] = field(default_factory=set)
    consumed: set[str] = field(default_factory=set)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def register(self, authorization: MppSignerAuthorization) -> None:
        with self._lock:
            existing = self.authorizations.get(authorization.authorization_id)
            if existing is not None and existing != authorization:
                raise MppSignerAuthorizationError("Signer authorization identity conflict")
            self.authorizations[authorization.authorization_id] = authorization

    def revoke(self, authorization_id: str) -> None:
        with self._lock:
            if authorization_id not in self.authorizations:
                raise MppSignerAuthorizationError("Signer authorization is not registered")
            self.revoked.add(authorization_id)

    def assert_usable(
        self,
        authorization: MppSignerAuthorization,
        request: SignerRequest,
        *,
        now: datetime,
    ) -> None:
        current = _utc(now)
        with self._lock:
            registered = self.authorizations.get(authorization.authorization_id)
            if registered is None or registered != authorization:
                raise MppSignerAuthorizationError("Signer authorization was not issued to this Signer")
            if authorization.authorization_id in self.revoked:
                raise MppSignerAuthorizationError("Signer authorization was revoked")
            if authorization.authorization_id in self.consumed:
                raise MppSignerAuthorizationError("Signer authorization was already consumed")
            authorization.assert_usable(now=current)
            if request.authorization_id != authorization.authorization_id:
                raise MppSignerAuthorizationError("Signer authorization/request identity mismatch")
            if request.authorization_hash != authorization.authorization_hash:
                raise MppSignerAuthorizationError("Signer authorization hash mismatch")
            if request.payment_id != authorization.payment_id:
                raise MppSignerAuthorizationError("Signer authorization/payment identity mismatch")
            if request.policy_decision_id != authorization.policy_decision_id:
                raise MppSignerAuthorizationError("Signer authorization/Policy decision mismatch")
            if request.policy_version != authorization.policy_version:
                raise MppSignerAuthorizationError("Signer authorization/Policy version mismatch")
            if request.expires_at <= current or authorization.expires_at <= current:
                raise MppSignerAuthorizationError("Signer request or authorization has expired")

    def consume(self, authorization_id: str) -> None:
        with self._lock:
            if authorization_id in self.revoked:
                raise MppSignerAuthorizationError("Signer authorization was revoked")
            if authorization_id not in self.authorizations:
                raise MppSignerAuthorizationError("Signer authorization is not registered")
            self.consumed.add(authorization_id)


@dataclass
class SignerBudgetLedger:
    """Signer-side reservation check, independent from the Policy repository."""

    reservations: dict[str, tuple[str, int, str]] = field(default_factory=dict)
    consumed: set[str] = field(default_factory=set)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def reserve(self, request: SignerRequest) -> bool:
        request_hash = request.request_hash()
        with self._lock:
            existing = self.reservations.get(request.idempotency_key)
            if existing is not None:
                if existing[0] != request_hash or existing[1] != request.amount_units:
                    raise SignerIdempotencyError("Signer idempotency key is bound to another request")
                return False
            if request.amount_units > request.per_payment_limit_units:
                raise SignerValidationError("amount exceeds the per-payment Signer limit")
            if request.amount_units + request.task_spent_units > request.per_task_limit_units:
                raise SignerValidationError("amount exceeds the per-Task Signer limit")
            if request.amount_units + request.daily_spent_units > request.daily_limit_units:
                raise SignerValidationError("amount exceeds the daily Signer limit")
            reservation_id = f"signer-reservation-{uuid4().hex}"
            self.reservations[request.idempotency_key] = (
                request_hash,
                request.amount_units,
                reservation_id,
            )
            return True

    def release(self, request: SignerRequest) -> None:
        with self._lock:
            self.reservations.pop(request.idempotency_key, None)

    def consume(self, request: SignerRequest) -> None:
        with self._lock:
            if request.idempotency_key not in self.reservations:
                raise SignerBoundaryError("Signer budget reservation is missing")
            self.consumed.add(request.idempotency_key)


@dataclass
class InMemorySignerAuditLog:
    """Append-only audit sink used by local/devnet tests and adapters."""

    events: list[AuditEvent] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def append(self, event: AuditEvent) -> AuditEvent:
        sanitized = event.with_integrity()
        with self._lock:
            self.events.append(sanitized)
        return sanitized


class SignerPersistence(Protocol):
    """Persistence hooks kept separate from the private key store."""

    def record_wallet(self, wallet: SignerWallet) -> None: ...

    def record_request(self, request: SignerRequest) -> None: ...

    def record_result(self, result: SignerResult) -> None: ...

    def record_receipt(self, receipt: SignerReceipt) -> None: ...


@dataclass
class PostgresSignerPersistence:
    """Durable public Signer records through an existing transaction.

    This adapter never receives a private key.  The caller controls the
    transaction lifecycle; network submission remains outside the database
    transaction and retries use the unique request/idempotency keys.
    """

    transaction: Any

    def record_wallet(self, wallet: SignerWallet) -> None:
        self.transaction.execute(
            """
            INSERT INTO mvp_signer_wallets
              (id, agent_id, network, cluster, public_key, rotation_version,
               status, created_at, updated_at, revoked_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
              public_key = EXCLUDED.public_key,
              rotation_version = EXCLUDED.rotation_version,
              status = EXCLUDED.status,
              updated_at = EXCLUDED.updated_at,
              revoked_at = EXCLUDED.revoked_at
            """,
            (
                wallet.wallet_id,
                wallet.agent_id,
                wallet.network.value,
                wallet.cluster,
                wallet.public_key,
                wallet.rotation_version,
                wallet.status.value,
                wallet.created_at,
                wallet.updated_at,
                wallet.revoked_at,
            ),
        )

    def record_request(self, request: SignerRequest) -> None:
        self.transaction.execute(
            """
            INSERT INTO mvp_signer_requests
              (id, authorization_id, policy_version, payment_id, challenge_id,
               idempotency_key,
               nonce, task_id, run_id, trace_id, correlation_id, agent_id,
               service_id, profile_id, profile_version, wallet_id,
               wallet_public_key, wallet_rotation_version, network, cluster,
               program_id, instruction, token, token_mint, recipient,
               source_token_account, recipient_token_account, recent_blockhash,
               amount_units, purpose, per_payment_limit_units,
               per_task_limit_units, daily_limit_units, task_spent_units,
               daily_spent_units, token_allowlist, recipient_allowlist,
               program_allowlist, instruction_allowlist, mint_allowlist,
               request_hash, status, expires_at)
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
              %s::jsonb, %s, 'REQUESTED', %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (
                request.request_id,
                request.authorization_id,
                request.policy_version,
                request.payment_id,
                request.challenge_id,
                request.idempotency_key,
                request.nonce,
                request.task_id,
                request.run_id,
                request.trace_id,
                request.correlation_id,
                request.agent_id,
                request.service_id,
                request.profile_id,
                request.profile_version,
                request.wallet_id,
                request.wallet_public_key,
                request.wallet_rotation_version,
                request.network.value,
                request.cluster,
                request.program_id,
                request.instruction,
                request.token,
                request.token_mint,
                request.recipient,
                request.source_token_account,
                request.recipient_token_account,
                request.recent_blockhash,
                request.amount_units,
                request.purpose,
                request.per_payment_limit_units,
                request.per_task_limit_units,
                request.daily_limit_units,
                request.task_spent_units,
                request.daily_spent_units,
                json.dumps(list(request.token_allowlist)),
                json.dumps(list(request.recipient_allowlist)),
                json.dumps(list(request.program_allowlist)),
                json.dumps(list(request.instruction_allowlist)),
                json.dumps(list(request.mint_allowlist)),
                request.request_hash(),
                request.expires_at,
            ),
        )
        self.transaction.execute(
            """
            UPDATE mvp_service_payments
            SET signer_request_id = %s,
                status = 'SIGNER_REQUESTED',
                updated_at = now()
            WHERE id = %s
            """,
            (request.request_id, request.payment_id),
        )

    def record_result(self, result: SignerResult) -> None:
        self.transaction.execute(
            """
            INSERT INTO mvp_signer_results
              (id, request_id, authorization_id, payment_id, request_hash,
               status, external_signature, receipt_id, failure_code,
               failure_message, created_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (request_id) DO NOTHING
            """,
            (
                result.result_id,
                result.request_id,
                result.authorization_id,
                result.payment_id,
                result.request_hash,
                result.status.value,
                result.external_signature,
                result.receipt_id,
                result.failure_code,
                result.failure_message,
                result.created_at,
                result.completed_at,
            ),
        )
        self.transaction.execute(
            """
            UPDATE mvp_service_payments
            SET status = %s,
                transaction_signature = %s,
                failure_code = %s,
                failure_message = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (
                result.status.value,
                result.external_signature,
                result.failure_code,
                result.failure_message,
                result.payment_id,
            ),
        )

    def record_receipt(self, receipt: SignerReceipt) -> None:
        self.transaction.execute(
            """
            INSERT INTO mvp_signer_receipts
              (id, result_id, request_id, authorization_id, payment_id,
               challenge_id, task_id, run_id, trace_id, correlation_id,
               network, cluster, token, amount_units, status, request_hash,
               external_signature, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
            ON CONFLICT (result_id) DO NOTHING
            """,
            (
                receipt.receipt_id,
                receipt.result_id,
                receipt.request_id,
                receipt.authorization_id,
                receipt.payment_id,
                receipt.challenge_id,
                receipt.task_id,
                receipt.run_id,
                receipt.trace_id,
                receipt.correlation_id,
                receipt.network.value,
                receipt.cluster,
                receipt.token,
                receipt.amount_units,
                receipt.status.value,
                receipt.request_hash,
                receipt.external_signature,
                receipt.created_at,
            ),
        )
        self.transaction.execute(
            """
            UPDATE mvp_service_payments
            SET receipt_id = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (receipt.receipt_id, receipt.payment_id),
        )


@dataclass
class LocalDeterministicTransport:
    """A no-network transport for deterministic local/devnet-like tests."""

    network: SignerNetwork = SignerNetwork.LOCAL
    fail_next: str | None = None
    submissions: list[dict[str, Any]] = field(default_factory=list)

    def recent_blockhash(self, request: SignerRequest) -> str | None:
        return None

    def submit(self, request: SignerRequest, signed_payload: bytes) -> SignerSubmission:
        if request.network is not self.network:
            raise SignerTransportError("transport network does not match Signer request")
        if self.fail_next is not None:
            message = self.fail_next
            self.fail_next = None
            return SignerSubmission(
                status=SignerRequestStatus.FAILED,
                failure_code="TRANSPORT_FAILURE",
                failure_message=message,
            )
        self.submissions.append(
            {
                "request_id": request.request_id,
                "request_hash": request.request_hash(),
                "payload_hash": hashlib.sha256(signed_payload).hexdigest(),
            }
        )
        signature = "local-sig-" + hashlib.sha256(signed_payload).hexdigest()
        return SignerSubmission(
            status=SignerRequestStatus.CONFIRMED,
            external_signature=signature,
            provider_receipt="local-receipt-" + request.request_id,
        )


@dataclass
class DeterministicDevnetTransport(LocalDeterministicTransport):
    network: SignerNetwork = SignerNetwork.SOLANA_DEVNET

    def recent_blockhash(self, request: SignerRequest) -> str | None:
        return request.recent_blockhash or _encode_base58(hashlib.sha256(b"rcao-devnet-blockhash").digest()[:32])

    def submit(self, request: SignerRequest, signed_payload: bytes) -> SignerSubmission:
        result = super().submit(request, signed_payload)
        if result.status is SignerRequestStatus.CONFIRMED and result.external_signature:
            return result.model_copy(
                update={
                    "external_signature": "devnet-sig-" + _encode_base58(
                        hashlib.sha256(signed_payload).digest()[:32]
                    ),
                    "provider_receipt": "devnet-receipt-" + request.request_id,
                }
            )
        return result


class SolanaDevnetRpcTransport:
    """Devnet-only JSON-RPC transport with an injectable call for tests."""

    network = SignerNetwork.SOLANA_DEVNET

    def __init__(
        self,
        rpc_url: str,
        *,
        rpc_call: Callable[[str, list[Any]], Any] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.rpc_url = rpc_url
        self.timeout_seconds = timeout_seconds
        self._validate_rpc_url(rpc_url)
        self._rpc_call = rpc_call or self._http_rpc_call

    @staticmethod
    def _validate_rpc_url(rpc_url: str) -> None:
        parsed = urllib.parse.urlparse(rpc_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Signer RPC URL must be an HTTP(S) URL")
        hostname = parsed.hostname.casefold()
        allowed = {
            "api.devnet.solana.com",
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if hostname not in allowed and not hostname.endswith(".devnet.solana.com"):
            raise ValueError("only Solana devnet or loopback RPC endpoints are allowed")

    def recent_blockhash(self, request: SignerRequest) -> str | None:
        if request.network is not SignerNetwork.SOLANA_DEVNET:
            raise SignerTransportError("devnet RPC cannot handle a local request")
        response = self._rpc_call("getLatestBlockhash", [{"commitment": "confirmed"}])
        try:
            payload = response.get("result", response) if isinstance(response, Mapping) else response
            blockhash = payload["value"]["blockhash"]
        except (KeyError, TypeError):
            raise SignerTransportError("devnet RPC returned no recent blockhash")
        _decode_public_key(str(blockhash))
        return str(blockhash)

    def submit(self, request: SignerRequest, signed_payload: bytes) -> SignerSubmission:
        if request.network is not SignerNetwork.SOLANA_DEVNET:
            raise SignerTransportError("devnet RPC cannot handle a local request")
        encoded = base64.b64encode(signed_payload).decode("ascii")
        response = self._rpc_call(
            "sendTransaction",
            [encoded, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"}],
        )
        if isinstance(response, Mapping) and "error" in response:
            error = response["error"]
            return SignerSubmission(
                status=SignerRequestStatus.FAILED,
                failure_code="RPC_ERROR",
                failure_message=str(error)[:500],
            )
        try:
            signature = str(response["result"] if isinstance(response, Mapping) and "result" in response else response["signature"])
        except (KeyError, TypeError):
            return SignerSubmission(
                status=SignerRequestStatus.FAILED,
                failure_code="RPC_INVALID_RESPONSE",
                failure_message="devnet RPC returned no transaction signature",
            )
        return SignerSubmission(
            status=SignerRequestStatus.SUBMITTED,
            external_signature=signature,
            provider_receipt=signature,
        )

    def _http_rpc_call(self, method: str, params: list[Any]) -> Any:
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.rpc_url,
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise SignerTransportError(f"devnet RPC request failed: {exc}") from exc


@dataclass
class Signer:
    """Private execution object; every public signing entry point is blocked."""

    key_store: EncryptedKeyStore
    transport: SignerTransport
    authorization_registry: SignerAuthorizationRegistry = field(
        default_factory=SignerAuthorizationRegistry
    )
    budget_ledger: SignerBudgetLedger = field(default_factory=SignerBudgetLedger)
    audit_log: InMemorySignerAuditLog = field(default_factory=InMemorySignerAuditLog)
    persistence: SignerPersistence | None = None
    stop_controller: StopController | None = None
    clock: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc)
    )
    _capability: object = field(default_factory=object, init=False, repr=False)
    _results: dict[str, tuple[str, SignerResult, SignerReceipt | None]] = field(
        default_factory=dict, init=False, repr=False
    )
    _request_nonces: dict[tuple[str, str], str] = field(
        default_factory=dict, init=False, repr=False
    )
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def sign(self, *args: Any, **kwargs: Any) -> None:
        raise DirectSignerCallError("direct Signer sign is forbidden; use PolicyBoundSignerGateway")

    def submit(self, *args: Any, **kwargs: Any) -> None:
        raise DirectSignerCallError("direct Signer submit is forbidden; use PolicyBoundSignerGateway")

    def execute(self, *args: Any, **kwargs: Any) -> None:
        raise DirectSignerCallError("direct Signer execute is forbidden; use PolicyBoundSignerGateway")

    def sign_and_submit(self, *args: Any, **kwargs: Any) -> None:
        raise DirectSignerCallError(
            "direct Signer sign_and_submit is forbidden; use PolicyBoundSignerGateway"
        )

    def _execute_from_policy(
        self,
        request: SignerRequest,
        authorization: MppSignerAuthorization,
        capability: object,
    ) -> tuple[SignerResult, SignerReceipt | None]:
        if capability is not self._capability:
            raise DirectSignerCallError("Signer execution requires the Policy Gateway capability")
        now = _utc(self.clock())
        request_hash = request.request_hash()
        with self._lock:
            replay = self._results.get(request.idempotency_key)
            if replay is not None:
                if replay[0] != request_hash:
                    raise SignerIdempotencyError("Signer idempotency key is bound to another request")
                return replay[1], replay[2]
            nonce_key = (request.wallet_id, request.nonce)
            used_nonce_hash = self._request_nonces.get(nonce_key)
            if used_nonce_hash is not None:
                if used_nonce_hash != request_hash:
                    raise SignerIdempotencyError("Signer nonce is bound to another request")
                raise SignerIdempotencyError("Signer nonce was already used")

        self._audit_request(request, now)
        try:
            self.authorization_registry.assert_usable(authorization, request, now=now)
            wallet = self.key_store.public_identity(request.wallet_id)
            self._assert_wallet_matches(wallet, request)
            self._assert_stop_controls(request)
            if request.expires_at <= now:
                raise MppSignerAuthorizationError("Signer request has expired")
            self.budget_ledger.reserve(request)
            with self._lock:
                self._request_nonces[(request.wallet_id, request.nonce)] = request_hash
        except Exception as exc:
            status = (
                SignerRequestStatus.STOPPED
                if isinstance(exc, SignerStopError)
                else SignerRequestStatus.EXPIRED
                if isinstance(exc, MppSignerAuthorizationError)
                and "expired" in str(exc).casefold()
                else SignerRequestStatus.REJECTED
            )
            result, receipt = self._failure_result(
                request,
                status=status,
                code="STOPPED" if status is SignerRequestStatus.STOPPED else "REJECTED",
                message=str(exc),
                now=now,
            )
            return result, receipt

        try:
            prepared_request = request
            if (
                request.network is SignerNetwork.SOLANA_DEVNET
                and request.recent_blockhash is None
            ):
                blockhash = self.transport.recent_blockhash(request)
                if not blockhash:
                    raise SignerTransportError("devnet transport did not provide a recent blockhash")
                prepared_request = request.model_copy(update={"recent_blockhash": blockhash})
                prepared_request_hash = prepared_request.request_hash()
            else:
                prepared_request_hash = request_hash
            key = self.key_store._decrypt_private_key(wallet)
            signed_payload = _build_signed_payload(prepared_request, key)
            submission = self.transport.submit(prepared_request, signed_payload)
            if submission.status not in {
                SignerRequestStatus.SUBMITTED,
                SignerRequestStatus.CONFIRMED,
            }:
                self.budget_ledger.release(request)
                result, receipt = self._failure_result(
                    request,
                    status=SignerRequestStatus.FAILED,
                    code=submission.failure_code or "TRANSPORT_FAILURE",
                    message=submission.failure_message or "Signer transport rejected the request",
                    now=now,
                    request_hash=prepared_request_hash,
                )
                return result, receipt
            self.budget_ledger.consume(request)
            self.authorization_registry.consume(authorization.authorization_id)
            completed = _utc(self.clock())
            result = SignerResult(
                result_id=f"signer-result-{uuid4().hex}",
                request_id=request.request_id,
                authorization_id=authorization.authorization_id,
                payment_id=request.payment_id,
                request_hash=prepared_request_hash,
                status=submission.status,
                network=request.network,
                cluster=request.cluster,
                receipt_id=f"signer-receipt-{uuid4().hex}",
                external_signature=submission.external_signature,
                created_at=now,
                completed_at=completed,
            )
            receipt = self._receipt(request, result, now)
            self._store_result(request, result, receipt)
            self._audit_result(request, result, receipt)
            return result, receipt
        except Exception as exc:
            self.budget_ledger.release(request)
            result, receipt = self._failure_result(
                request,
                status=SignerRequestStatus.FAILED,
                code="SIGNER_FAILURE",
                message=str(exc),
                now=now,
            )
            return result, receipt

    def revoke_authorization(self, authorization_id: str) -> None:
        self.authorization_registry.revoke(authorization_id)

    def register_wallet(self, wallet: SignerWallet) -> None:
        """Register public wallet identity in the durable control plane."""

        if self.persistence is not None:
            self.persistence.record_wallet(wallet)

    def revoke_wallet(self, wallet_id: str) -> SignerWallet:
        return self.key_store.revoke_wallet(wallet_id)

    def _assert_wallet_matches(self, wallet: SignerWallet, request: SignerRequest) -> None:
        if wallet.status is not SignerWalletStatus.ACTIVE:
            raise SignerValidationError(f"wallet is not active: {wallet.status.value}")
        if wallet.agent_id != request.agent_id:
            raise SignerValidationError("wallet Agent does not match Signer request")
        if wallet.network is not request.network or wallet.cluster != request.cluster:
            raise SignerValidationError("wallet network does not match Signer request")
        if wallet.public_key != request.wallet_public_key:
            raise SignerValidationError("wallet public key does not match Signer request")
        if wallet.rotation_version != request.wallet_rotation_version:
            raise SignerValidationError("wallet rotation version is stale")

    def _assert_stop_controls(self, request: SignerRequest) -> None:
        if self.stop_controller is None:
            return
        for target, target_id in (
            (StopTarget.GLOBAL, "*"),
            (StopTarget.SIGNER, "*"),
            (StopTarget.PAYMENT, request.payment_id),
            (StopTarget.AGENT, request.agent_id),
            (StopTarget.RUN, request.run_id),
            (StopTarget.MPP, "*"),
        ):
            reason = self.stop_controller.stop_reason(target, target_id)
            if reason:
                raise SignerStopError(f"{target.value} stop control is active: {reason}")

    def _audit_request(self, request: SignerRequest, now: datetime) -> None:
        if self.persistence is not None:
            self.persistence.record_request(request)
        self.audit_log.append(
            AuditEvent(
                event_id=f"signer-audit-{uuid4().hex}",
                event_version=1,
                event_type="SIGNER_REQUEST",
                actor_id=f"signer:{request.wallet_id}",
                actor_type="SIGNER",
                action="SIGNER_REQUEST_RECEIVED",
                target_type="SIGNER_REQUEST",
                target_id=request.request_id,
                before_state={},
                after_state={
                    "request_id": request.request_id,
                    "payment_id": request.payment_id,
                    "authorization_id": request.authorization_id,
                    "request_hash": request.request_hash(),
                    "network": request.network.value,
                    "cluster": request.cluster,
                    "amount_units": request.amount_units,
                },
                policy_result="ALLOW",
                reason="Policy-bound Signer request received",
                correlation_id=request.correlation_id,
                task_id=request.task_id,
                run_id=request.run_id,
                payment_id=request.payment_id,
                created_at=now,
            )
        )

    def _audit_result(
        self,
        request: SignerRequest,
        result: SignerResult,
        receipt: SignerReceipt | None,
    ) -> None:
        self.audit_log.append(
            AuditEvent(
                event_id=f"signer-audit-{uuid4().hex}",
                event_version=1,
                event_type=("SIGNER_RESULT" if result.status in {SignerRequestStatus.SUBMITTED, SignerRequestStatus.CONFIRMED} else "SIGNER_FAILURE"),
                actor_id=f"signer:{request.wallet_id}",
                actor_type="SIGNER",
                action="SIGNER_RESULT_RECORDED",
                target_type="SIGNER_RESULT",
                target_id=result.result_id,
                before_state={"request_id": request.request_id},
                after_state={
                    "result_id": result.result_id,
                    "request_id": result.request_id,
                    "payment_id": result.payment_id,
                    "status": result.status.value,
                    "receipt_id": receipt.receipt_id if receipt else None,
                    "external_signature": result.external_signature,
                    "failure_code": result.failure_code,
                },
                policy_result=(
                    "ALLOW"
                    if result.status in {SignerRequestStatus.SUBMITTED, SignerRequestStatus.CONFIRMED}
                    else "DENY"
                ),
                reason=result.failure_message or "Signer result recorded",
                correlation_id=request.correlation_id,
                task_id=request.task_id,
                run_id=request.run_id,
                payment_id=request.payment_id,
                created_at=result.completed_at,
            )
        )

    def _failure_result(
        self,
        request: SignerRequest,
        *,
        status: SignerRequestStatus,
        code: str,
        message: str,
        now: datetime,
        request_hash: str | None = None,
    ) -> tuple[SignerResult, SignerReceipt]:
        result = SignerResult(
            result_id=f"signer-result-{uuid4().hex}",
            request_id=request.request_id,
            authorization_id=request.authorization_id,
            payment_id=request.payment_id,
            request_hash=request_hash or request.request_hash(),
            status=status,
            network=request.network,
            cluster=request.cluster,
            receipt_id=f"signer-receipt-{uuid4().hex}",
            failure_code=code,
            failure_message=message[:500],
            created_at=now,
            completed_at=_utc(self.clock()),
        )
        receipt = self._receipt(request, result, now)
        self._store_result(request, result, receipt)
        self._audit_result(request, result, receipt)
        return result, receipt

    def _receipt(self, request: SignerRequest, result: SignerResult, now: datetime) -> SignerReceipt:
        return SignerReceipt(
            receipt_id=result.receipt_id or f"signer-receipt-{uuid4().hex}",
            result_id=result.result_id,
            request_id=request.request_id,
            authorization_id=request.authorization_id,
            payment_id=request.payment_id,
            challenge_id=request.challenge_id,
            task_id=request.task_id,
            run_id=request.run_id,
            trace_id=request.trace_id,
            correlation_id=request.correlation_id,
            network=request.network,
            cluster=request.cluster,
            token=request.token,
            amount_units=request.amount_units,
            status=result.status,
            request_hash=result.request_hash,
            external_signature=result.external_signature,
            created_at=now,
        )

    def _store_result(
        self,
        request: SignerRequest,
        result: SignerResult,
        receipt: SignerReceipt | None,
    ) -> None:
        if self.persistence is not None:
            self.persistence.record_result(result)
            if receipt is not None:
                self.persistence.record_receipt(receipt)
        with self._lock:
            self._results[request.idempotency_key] = (
                result.request_hash,
                result,
                receipt,
            )


@dataclass
class PolicyBoundSignerGateway:
    """The only public object allowed to invoke a Signer execution."""

    signer: Signer

    def register_authorization(self, authorization: MppSignerAuthorization) -> None:
        self.signer.authorization_registry.register(authorization)

    def execute(
        self,
        request: SignerRequest,
        authorization: MppSignerAuthorization,
    ) -> tuple[SignerResult, SignerReceipt | None]:
        self.register_authorization(authorization)
        return self.signer._execute_from_policy(request, authorization, self.signer._capability)

    def revoke_authorization(self, authorization_id: str) -> None:
        self.signer.revoke_authorization(authorization_id)


SignerGateway = PolicyBoundSignerGateway


class SignerStopError(SignerBoundaryError):
    """An operational stop control blocked the signing attempt."""


def _as_network(value: SignerNetwork | PaymentProfileNetwork | str) -> SignerNetwork:
    try:
        return SignerNetwork(getattr(value, "value", value))
    except ValueError as exc:
        raise ValueError("Signer network must be LOCAL or SOLANA_DEVNET") from exc


def _utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SignerBoundaryError("Signer timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _is_internal_recipient(value: str) -> bool:
    lowered = value.casefold()
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


def _encode_base58(raw: bytes) -> str:
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * leading_zeroes + (encoded or ("1" if not leading_zeroes else ""))


def _decode_base58(value: str) -> bytes:
    if not value or value.strip() != value:
        raise ValueError("base58 value must be non-empty and whitespace-free")
    number = 0
    for character in value:
        try:
            number = number * 58 + _BASE58_INDEX[character]
        except KeyError as exc:
            raise ValueError("base58 value contains an invalid character") from exc
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + raw


def _decode_public_key(value: str, *, allow_fixture: bool = False) -> bytes:
    if allow_fixture and value.upper().startswith("SPL_TEST_"):
        return hashlib.sha256(value.encode("utf-8")).digest()[:32]
    raw = _decode_base58(value)
    if len(raw) != 32:
        raise ValueError("Solana public keys must decode to exactly 32 bytes")
    return raw


def _safe_filename(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)


def _wallet_aad(wallet: SignerWallet) -> bytes:
    return json.dumps(
        {
            "wallet_id": wallet.wallet_id,
            "agent_id": wallet.agent_id,
            "network": wallet.network.value,
            "cluster": wallet.cluster,
            "public_key": wallet.public_key,
            "rotation_version": wallet.rotation_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _compact_u16(value: int) -> bytes:
    if value < 0 or value >= 1 << 32:
        raise SignerValidationError("compact-u16 value is out of range")
    output = bytearray()
    remaining = value
    while True:
        digit = remaining & 0x7F
        remaining >>= 7
        if remaining:
            output.append(digit | 0x80)
        else:
            output.append(digit)
            return bytes(output)


def _build_signed_payload(request: SignerRequest, key: Ed25519PrivateKey) -> bytes:
    if request.network is SignerNetwork.LOCAL:
        payload = json.dumps(
            request.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        signature = key.sign(payload)
        return b"RCAO-LOCAL-SIGNER-V1\x00" + signature + payload
    message = _build_solana_transfer_message(request)
    signature = key.sign(message)
    return _compact_u16(1) + signature + message


def _build_solana_transfer_message(request: SignerRequest) -> bytes:
    if not request.recent_blockhash:
        raise SignerValidationError("devnet transaction requires a recent blockhash")
    account_keys = [
        _decode_public_key(request.wallet_public_key),
        _decode_public_key(request.source_token_account or ""),
        _decode_public_key(request.recipient_token_account or ""),
        _decode_public_key(request.program_id),
    ]
    header = bytes((1, 0, 1))
    instruction = bytes((3,)) + int(request.amount_units).to_bytes(8, "little")
    compiled_instruction = (
        bytes((3,))
        + _compact_u16(3)
        + bytes((1, 2, 0))
        + _compact_u16(len(instruction))
        + instruction
    )
    return (
        header
        + _compact_u16(len(account_keys))
        + b"".join(account_keys)
        + _decode_public_key(request.recent_blockhash)
        + _compact_u16(1)
        + compiled_instruction
    )


__all__ = [
    "DeterministicDevnetTransport",
    "DirectSignerCallError",
    "EncryptedKeyStore",
    "InMemorySignerAuditLog",
    "LOCAL_PROGRAM_ID",
    "LocalDeterministicTransport",
    "PolicyBoundSignerGateway",
    "Signer",
    "SignerBoundaryError",
    "SignerGateway",
    "SignerIdempotencyError",
    "SignerNetwork",
    "SignerPersistence",
    "SignerPersistenceError",
    "PostgresSignerPersistence",
    "SignerReceipt",
    "SignerRequest",
    "SignerRequestStatus",
    "SignerResult",
    "SignerStopError",
    "SignerSubmission",
    "SignerTransport",
    "SignerTransportError",
    "SignerValidationError",
    "SignerWallet",
    "SignerWalletStatus",
    "SOLANA_TOKEN_PROGRAM_ID",
    "SolanaDevnetRpcTransport",
]
