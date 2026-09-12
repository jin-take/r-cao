from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.mpp_session import (
    MppPaymentSession,
    PaymentSessionError,
    PaymentSessionStatus,
    PaymentVoucher,
)
from app.payment_boundary import PaymentNetwork


def make_session(**changes: object) -> MppPaymentSession:
    values = {
        "owner_approval_id": "approval-1",
        "agent_id": "agent-1",
        "task_id": "task-1",
        "service_id": "service-1",
        "token": "LOCAL_TEST_USDC",
        "network": PaymentNetwork.LOCAL,
        "cluster": "LOCAL",
        "purpose": "task-bound-service",
        "ceiling_units": 1000,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "correlation_id": "corr-1",
    }
    values.update(changes)
    return MppPaymentSession(**values)


def voucher(voucher_id: str, amount: int, **changes: object) -> PaymentVoucher:
    values = {
        "voucher_id": voucher_id,
        "amount_units": amount,
        "task_id": "task-1",
        "service_id": "service-1",
        "token": "LOCAL_TEST_USDC",
        "issued_at": datetime.now(timezone.utc),
    }
    values.update(changes)
    return PaymentVoucher(**values)


def test_top_up_and_multiple_settles_are_capped() -> None:
    session = make_session()
    session.top_up(700)
    first = session.settle(voucher("v1", 200))
    assert first.spent_units == 200
    assert first.remaining_units == 500
    second = session.settle(voucher("v2", 500))
    assert second.spent_units == 700
    assert second.remaining_units == 0
    with pytest.raises(PaymentSessionError, match="remaining session limit"):
        session.settle(voucher("v3", 1))


def test_duplicate_voucher_is_idempotent() -> None:
    session = make_session()
    session.top_up(500)
    first = session.settle(voucher("same", 100))
    second = session.settle(voucher("same", 100))
    assert first.spent_units == second.spent_units == 100
    with pytest.raises(PaymentSessionError, match="different amount"):
        session.settle(voucher("same", 101))


def test_scope_revoke_and_close_block_future_payment() -> None:
    session = make_session()
    session.top_up(500)
    with pytest.raises(PaymentSessionError, match="scope"):
        session.settle(voucher("wrong", 10, task_id="task-2"))
    revoked = session.revoke("owner emergency stop")
    assert revoked.status is PaymentSessionStatus.REVOKED
    with pytest.raises(PaymentSessionError, match="not open"):
        session.settle(voucher("v1", 10))

    other = make_session(correlation_id="corr-2")
    other.top_up(100)
    closed = other.close()
    assert closed.status is PaymentSessionStatus.CLOSED
    assert closed.remaining_units == 100
    with pytest.raises(PaymentSessionError, match="not open"):
        other.top_up(1)


def test_expired_session_fails_closed() -> None:
    now = datetime.now(timezone.utc)
    session = make_session(expires_at=now + timedelta(seconds=1))
    with pytest.raises(PaymentSessionError, match="EXPIRED"):
        session.top_up(10, now=now + timedelta(seconds=2))
    assert session.snapshot(now + timedelta(seconds=2)).status is PaymentSessionStatus.EXPIRED


def test_mainnet_and_reward_use_are_forbidden() -> None:
    with pytest.raises(PaymentSessionError, match="local/devnet"):
        make_session(network=PaymentNetwork.SOLANA_MAINNET, cluster="MAINNET")
    with pytest.raises(PaymentSessionError, match="Reward/Treasury"):
        make_session(purpose="REWARD")
