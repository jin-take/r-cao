"""Persistent MPP payment receipt/audit contracts.

Issue #12 keeps MPP payment evidence separate from Reward/Treasury accounting.
Every lifecycle mutation is written to the existing immutable Audit + Outbox
transaction boundary, using one correlation id from Challenge through terminal
state. Receipt hashes are derived from a sanitized canonical payload so a
receipt can be integrity-checked without retaining secrets.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4

from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter, sanitize


class PaymentReceiptError(ValueError):
    pass


class PaymentReceiptIntegrityError(PaymentReceiptError):
    pass


class PaymentLifecycleError(PaymentReceiptError):
    pass


class PaymentStatus(StrEnum):
    CHALLENGED = "CHALLENGED"
    POLICY_APPROVED = "POLICY_APPROVED"
    OWNER_APPROVED = "OWNER_APPROVED"
    SIGNED = "SIGNED"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    REVOKED = "REVOKED"


_ALLOWED_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.CHALLENGED: frozenset({PaymentStatus.POLICY_APPROVED, PaymentStatus.REJECTED, PaymentStatus.FAILED, PaymentStatus.REVOKED}),
    PaymentStatus.POLICY_APPROVED: frozenset({PaymentStatus.OWNER_APPROVED, PaymentStatus.SIGNED, PaymentStatus.REJECTED, PaymentStatus.FAILED, PaymentStatus.REVOKED}),
    PaymentStatus.OWNER_APPROVED: frozenset({PaymentStatus.SIGNED, PaymentStatus.REJECTED, PaymentStatus.FAILED, PaymentStatus.REVOKED}),
    PaymentStatus.SIGNED: frozenset({PaymentStatus.SUBMITTED, PaymentStatus.FAILED, PaymentStatus.REVOKED}),
    PaymentStatus.SUBMITTED: frozenset({PaymentStatus.CONFIRMED, PaymentStatus.FAILED, PaymentStatus.REVOKED}),
    PaymentStatus.CONFIRMED: frozenset({PaymentStatus.REFUNDED}),
    PaymentStatus.REJECTED: frozenset(),
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.REFUNDED: frozenset(),
    PaymentStatus.REVOKED: frozenset(),
}


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(sanitize(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def receipt_hash(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(receipt).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PaymentReceipt:
    payment_id: str
    agent_id: str
    task_id: str
    run_id: str
    trace_id: str
    service_id: str
    payer: str
    recipient: str
    cluster: str
    token: str
    amount_units: str
    purpose: str
    challenge_hash: str
    status: PaymentStatus
    correlation_id: str
    idempotency_key: str
    conversation_id: str | None = None
    transaction_signature: str | None = None
    policy_decision: str | None = None
    policy_version: str | None = None
    signer_request_id: str | None = None
    owner_approval_id: str | None = None
    receipt: Mapping[str, Any] | None = None
    receipt_digest: str | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("payment_id", "agent_id", "task_id", "run_id", "trace_id", "service_id", "recipient", "cluster", "token", "amount_units", "purpose", "challenge_hash", "correlation_id", "idempotency_key"):
            if not getattr(self, name):
                raise PaymentReceiptError(f"{name} is required")
        if self.receipt is not None:
            digest = receipt_hash(self.receipt)
            if self.receipt_digest is not None and self.receipt_digest != digest:
                raise PaymentReceiptIntegrityError("receipt hash mismatch")

    def public_state(self) -> dict[str, Any]:
        data = {
            "payment_id": self.payment_id,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "trace_id": self.trace_id,
            "service_id": self.service_id,
            "payer": self.payer,
            "recipient": self.recipient,
            "cluster": self.cluster,
            "token": self.token,
            "amount_units": self.amount_units,
            "purpose": self.purpose,
            "challenge_hash": self.challenge_hash,
            "transaction_signature": self.transaction_signature,
            "policy_decision": self.policy_decision,
            "policy_version": self.policy_version,
            "signer_request_id": self.signer_request_id,
            "owner_approval_id": self.owner_approval_id,
            "receipt_hash": receipt_hash(self.receipt) if self.receipt is not None else self.receipt_digest,
            "status": self.status.value,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "error_code": self.error_code,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "confirmed_at": self.confirmed_at,
        }
        return dict(sanitize(data))

    def transition(self, new_status: PaymentStatus, **changes: Any) -> "PaymentReceipt":
        if new_status not in _ALLOWED_TRANSITIONS[self.status]:
            raise PaymentLifecycleError(f"invalid payment transition: {self.status.value} -> {new_status.value}")
        values = dict(self.__dict__)
        values.update(changes)
        values["status"] = new_status
        if new_status is PaymentStatus.CONFIRMED and values.get("confirmed_at") is None:
            values["confirmed_at"] = datetime.now(timezone.utc)
        return PaymentReceipt(**values)


class PaymentAuditRecorder:
    """Write payment lifecycle evidence into Audit and Outbox atomically."""

    @staticmethod
    def record(transaction: Any, *, before: PaymentReceipt | None, after: PaymentReceipt, actor_id: str, actor_type: str, reason: str) -> PaymentReceipt:
        if before is not None:
            if before.payment_id != after.payment_id or before.correlation_id != after.correlation_id:
                raise PaymentLifecycleError("payment identity/correlation cannot change")
            if after.status not in _ALLOWED_TRANSITIONS[before.status]:
                raise PaymentLifecycleError(f"invalid payment transition: {before.status.value} -> {after.status.value}")

        event_id = f"payment-audit-{uuid4().hex}"
        tx_id = f"payment-tx-{uuid4().hex}"
        action = f"MPP_PAYMENT_{after.status.value}"
        AuditWriter.append(transaction, AuditEvent(
            event_id=event_id,
            event_version=1,
            event_type="MPP_PAYMENT_LIFECYCLE",
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            target_type="PAYMENT",
            target_id=after.payment_id,
            before_state=before.public_state() if before else {},
            after_state=after.public_state(),
            policy_result=after.policy_decision or "ALLOW",
            reason=reason,
            correlation_id=after.correlation_id,
            transaction_id=tx_id,
            task_id=after.task_id,
            run_id=after.run_id,
            payment_id=after.payment_id,
        ))
        OutboxWriter.enqueue(transaction, OutboxEvent(
            event_id=f"payment-outbox-{uuid4().hex}",
            aggregate_type="PAYMENT",
            aggregate_id=after.payment_id,
            event_type=action,
            idempotency_key=f"{after.payment_id}:{after.status.value}:{after.idempotency_key}",
            transaction_id=tx_id,
            payload={
                "payment_id": after.payment_id,
                "correlation_id": after.correlation_id,
                "task_id": after.task_id,
                "run_id": after.run_id,
                "service_id": after.service_id,
                "status": after.status.value,
                "receipt_hash": after.public_state().get("receipt_hash"),
            },
        ))
        return after


def verify_receipt(receipt: PaymentReceipt, *, expected_cluster: str | None = None, seen_hashes: set[str] | None = None) -> str:
    if receipt.receipt is None:
        raise PaymentReceiptIntegrityError("receipt body is required")
    digest = receipt_hash(receipt.receipt)
    if receipt.receipt_digest is not None and digest != receipt.receipt_digest:
        raise PaymentReceiptIntegrityError("receipt hash mismatch")
    if expected_cluster is not None and receipt.cluster.upper() != expected_cluster.upper():
        raise PaymentReceiptIntegrityError("receipt cluster mismatch")
    if seen_hashes is not None:
        if digest in seen_hashes:
            raise PaymentReceiptIntegrityError("receipt was already used")
        seen_hashes.add(digest)
    return digest


def payment_operation(event: Mapping[str, Any]) -> dict[str, Any]:
    """Build a redacted Operations row from a persisted Audit record."""
    state = event.get("after_state") or {}
    return dict(sanitize({
        "event_id": event.get("id") or event.get("event_id"),
        "payment_id": event.get("payment_id") or state.get("payment_id"),
        "correlation_id": event.get("correlation_id") or state.get("correlation_id"),
        "task_id": event.get("task_id") or state.get("task_id"),
        "run_id": event.get("run_id") or state.get("run_id"),
        "service_id": state.get("service_id"),
        "status": state.get("status"),
        "token": state.get("token"),
        "amount_units": state.get("amount_units"),
        "recipient": state.get("recipient"),
        "receipt_hash": state.get("receipt_hash"),
        "error_code": state.get("error_code"),
        "created_at": event.get("created_at"),
    }))
