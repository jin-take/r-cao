from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.mpp_receipts import (
    PaymentAuditRecorder,
    PaymentLifecycleError,
    PaymentReceipt,
    PaymentReceiptIntegrityError,
    PaymentStatus,
    payment_operation,
    receipt_hash,
    verify_receipt,
)


class FakeTransaction:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.calls.append((sql, params))


def make_receipt(status: PaymentStatus = PaymentStatus.CHALLENGED, **changes: object) -> PaymentReceipt:
    values = {
        "payment_id": "pay-1",
        "agent_id": "agent-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "trace_id": "trace-1",
        "service_id": "svc-1",
        "payer": "payer-public-key",
        "recipient": "recipient-public-key",
        "cluster": "DEVNET",
        "token": "SPL_TEST_USDC",
        "amount_units": "100",
        "purpose": "task-bound service",
        "challenge_hash": "a" * 64,
        "status": status,
        "correlation_id": "corr-1",
        "idempotency_key": "idem-1",
        "receipt": {"signature": "sig-public", "private_key": "must-not-leak"},
        "created_at": datetime.now(timezone.utc),
    }
    values.update(changes)
    return PaymentReceipt(**values)


def test_receipt_hash_and_secret_redaction() -> None:
    payment = make_receipt()
    state = payment.public_state()
    assert state["receipt_hash"] == receipt_hash(payment.receipt or {})
    assert "private_key" not in state


def test_verify_detects_cluster_and_replay() -> None:
    payment = make_receipt()
    seen: set[str] = set()
    verify_receipt(payment, expected_cluster="devnet", seen_hashes=seen)
    with pytest.raises(PaymentReceiptIntegrityError, match="already used"):
        verify_receipt(payment, expected_cluster="DEVNET", seen_hashes=seen)
    with pytest.raises(PaymentReceiptIntegrityError, match="cluster mismatch"):
        verify_receipt(payment, expected_cluster="MAINNET")


def test_lifecycle_rejects_invalid_transition() -> None:
    payment = make_receipt()
    with pytest.raises(PaymentLifecycleError):
        payment.transition(PaymentStatus.CONFIRMED)


def test_audit_and_outbox_are_written_in_same_transaction() -> None:
    tx = FakeTransaction()
    challenged = make_receipt()
    approved = challenged.transition(PaymentStatus.POLICY_APPROVED, policy_decision="ALLOW")
    PaymentAuditRecorder.record(
        tx,
        before=challenged,
        after=approved,
        actor_id="policy-engine",
        actor_type="SYSTEM",
        reason="policy allowed task-bound payment",
    )
    assert len(tx.calls) == 2
    assert "INSERT INTO mvp_audit_logs" in tx.calls[0][0]
    assert "INSERT INTO mvp_outbox_events" in tx.calls[1][0]
    assert "pay-1" in tx.calls[0][1]


def test_operations_row_is_redacted_and_correlated() -> None:
    row = payment_operation({
        "id": "event-1",
        "payment_id": "pay-1",
        "correlation_id": "corr-1",
        "task_id": "task-1",
        "run_id": "run-1",
        "after_state": {
            "service_id": "svc-1",
            "status": "CONFIRMED",
            "token": "SPL_TEST_USDC",
            "amount_units": "100",
            "recipient": "recipient-public-key",
            "receipt_hash": "b" * 64,
            "private_key": "secret",
        },
    })
    assert row["payment_id"] == "pay-1"
    assert row["correlation_id"] == "corr-1"
    assert "private_key" not in row
