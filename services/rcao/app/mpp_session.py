"""Owner-approved capped MPP payment sessions for local/devnet only.

Issue #10 adds a bounded session abstraction on top of the single-payment MPP
flow. A session never grants unlimited autonomy: Agent, Task, Service, token,
network, expiry and cumulative spend ceiling are fixed by the Owner approval.
Reward, salary, Treasury and Agent-to-Agent transfer use are explicitly denied.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter
from .payment_boundary import PaymentNetwork


class PaymentSessionError(ValueError):
    pass


class PaymentSessionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


FORBIDDEN_PURPOSES = frozenset({"REWARD", "SALARY", "TREASURY", "AGENT_TRANSFER", "AGENT_TO_AGENT"})


@dataclass(frozen=True)
class PaymentVoucher:
    voucher_id: str
    amount_units: int
    task_id: str
    service_id: str
    token: str
    issued_at: datetime

    def __post_init__(self) -> None:
        if not self.voucher_id:
            raise PaymentSessionError("voucher_id is required")
        if self.amount_units <= 0:
            raise PaymentSessionError("voucher amount must be positive")


@dataclass(frozen=True)
class PaymentSessionSnapshot:
    session_id: str
    owner_approval_id: str
    agent_id: str
    task_id: str
    service_id: str
    token: str
    network: PaymentNetwork
    cluster: str
    purpose: str
    ceiling_units: int
    funded_units: int
    spent_units: int
    status: PaymentSessionStatus
    expires_at: datetime
    correlation_id: str
    revoked_reason: str | None = None

    @property
    def remaining_units(self) -> int:
        return max(0, min(self.ceiling_units, self.funded_units) - self.spent_units)


@dataclass
class MppPaymentSession:
    owner_approval_id: str
    agent_id: str
    task_id: str
    service_id: str
    token: str
    network: PaymentNetwork
    cluster: str
    purpose: str
    ceiling_units: int
    expires_at: datetime
    correlation_id: str
    session_id: str = field(default_factory=lambda: f"mpp-session-{uuid4().hex}")
    _funded_units: int = 0
    _spent_units: int = 0
    _status: PaymentSessionStatus = PaymentSessionStatus.OPEN
    _settled: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _revoked_reason: str | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.ceiling_units <= 0:
            raise PaymentSessionError("ceiling_units must be positive")
        if self.expires_at.tzinfo is None:
            raise PaymentSessionError("expires_at must be timezone-aware")
        if self.network is PaymentNetwork.LOCAL:
            if self.cluster.upper() != "LOCAL" or not self.token.upper().startswith("LOCAL_TEST_"):
                raise PaymentSessionError("LOCAL session requires LOCAL cluster and LOCAL_TEST_ token")
        elif self.network is PaymentNetwork.SOLANA_DEVNET:
            if self.cluster.upper() != "DEVNET" or not self.token.upper().startswith("SPL_TEST_"):
                raise PaymentSessionError("devnet session requires DEVNET cluster and SPL_TEST_ token")
        else:
            raise PaymentSessionError("payment sessions are restricted to local/devnet")
        if self.purpose.upper() in FORBIDDEN_PURPOSES:
            raise PaymentSessionError("session cannot be used for Reward/Treasury/Agent transfer")
        for value in (self.owner_approval_id, self.agent_id, self.task_id, self.service_id, self.token, self.correlation_id):
            if not value:
                raise PaymentSessionError("session scope fields are required")

    def _refresh_expiry(self, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        if self._status is PaymentSessionStatus.OPEN and current >= self.expires_at:
            self._status = PaymentSessionStatus.EXPIRED

    def snapshot(self, now: datetime | None = None) -> PaymentSessionSnapshot:
        with self._lock:
            self._refresh_expiry(now)
            return PaymentSessionSnapshot(
                session_id=self.session_id,
                owner_approval_id=self.owner_approval_id,
                agent_id=self.agent_id,
                task_id=self.task_id,
                service_id=self.service_id,
                token=self.token,
                network=self.network,
                cluster=self.cluster,
                purpose=self.purpose,
                ceiling_units=self.ceiling_units,
                funded_units=self._funded_units,
                spent_units=self._spent_units,
                status=self._status,
                expires_at=self.expires_at,
                correlation_id=self.correlation_id,
                revoked_reason=self._revoked_reason,
            )

    def top_up(self, amount_units: int, *, now: datetime | None = None) -> PaymentSessionSnapshot:
        if amount_units <= 0:
            raise PaymentSessionError("top-up must be positive")
        with self._lock:
            self._require_open(now)
            if self._funded_units + amount_units > self.ceiling_units:
                raise PaymentSessionError("top-up exceeds Owner-approved session ceiling")
            self._funded_units += amount_units
            return self.snapshot(now)

    def settle(self, voucher: PaymentVoucher, *, now: datetime | None = None) -> PaymentSessionSnapshot:
        with self._lock:
            self._require_open(now)
            if voucher.task_id != self.task_id or voucher.service_id != self.service_id or voucher.token != self.token:
                raise PaymentSessionError("voucher scope does not match session")
            prior = self._settled.get(voucher.voucher_id)
            if prior is not None:
                if prior != voucher.amount_units:
                    raise PaymentSessionError("duplicate voucher id has different amount")
                return self.snapshot(now)
            if voucher.amount_units > self.snapshot(now).remaining_units:
                raise PaymentSessionError("voucher exceeds remaining session limit")
            self._spent_units += voucher.amount_units
            self._settled[voucher.voucher_id] = voucher.amount_units
            return self.snapshot(now)

    def close(self, *, now: datetime | None = None) -> PaymentSessionSnapshot:
        with self._lock:
            self._require_open(now)
            self._status = PaymentSessionStatus.CLOSED
            return self.snapshot(now)

    def revoke(self, reason: str) -> PaymentSessionSnapshot:
        if not reason.strip():
            raise PaymentSessionError("revoke reason is required")
        with self._lock:
            if self._status is not PaymentSessionStatus.OPEN:
                raise PaymentSessionError("only an open session can be revoked")
            self._status = PaymentSessionStatus.REVOKED
            self._revoked_reason = reason
            return self.snapshot()

    def unused_units(self, *, now: datetime | None = None) -> int:
        return self.snapshot(now).remaining_units

    def _require_open(self, now: datetime | None = None) -> None:
        self._refresh_expiry(now)
        if self._status is not PaymentSessionStatus.OPEN:
            raise PaymentSessionError(f"session is not open: {self._status.value}")


class PaymentSessionAuditRecorder:
    @staticmethod
    def record(transaction: Any, *, before: PaymentSessionSnapshot | None, after: PaymentSessionSnapshot, actor_id: str, actor_type: str, action: str, reason: str) -> None:
        event_id = f"mpp-session-audit-{uuid4().hex}"
        transaction_id = f"mpp-session-tx-{uuid4().hex}"
        before_state = before.__dict__ if before is not None else {}
        after_state = after.__dict__
        AuditWriter.append(transaction, AuditEvent(
            event_id=event_id,
            event_version=1,
            event_type="MPP_PAYMENT_SESSION",
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            target_type="PAYMENT_SESSION",
            target_id=after.session_id,
            before_state=before_state,
            after_state=after_state,
            policy_result="ALLOW",
            reason=reason,
            correlation_id=after.correlation_id,
            transaction_id=transaction_id,
            task_id=after.task_id,
        ))
        OutboxWriter.enqueue(transaction, OutboxEvent(
            event_id=f"mpp-session-outbox-{uuid4().hex}",
            aggregate_type="PAYMENT_SESSION",
            aggregate_id=after.session_id,
            event_type=action,
            idempotency_key=f"{after.session_id}:{action}:{after.spent_units}:{after.funded_units}",
            transaction_id=transaction_id,
            payload={
                "session_id": after.session_id,
                "agent_id": after.agent_id,
                "task_id": after.task_id,
                "service_id": after.service_id,
                "status": after.status.value,
                "ceiling_units": after.ceiling_units,
                "funded_units": after.funded_units,
                "spent_units": after.spent_units,
                "remaining_units": after.remaining_units,
                "owner_approval_id": after.owner_approval_id,
            },
        ))
