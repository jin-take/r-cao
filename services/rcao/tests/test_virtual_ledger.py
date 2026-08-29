import pytest

from app.auth import ActorContext, ActorType
from app.models import AgentRole
from app.policy import Phase
from app.virtual_ledger import (
    DirectAgentTransferError,
    LedgerAuthorizationError,
    MAX_SIGNED_BIGINT,
    TreasuryBalance,
    VirtualLedgerEntry,
    VirtualLedgerPolicy,
)


def actor(actor_id: str, actor_type: ActorType, role: AgentRole) -> ActorContext:
    return ActorContext(
        actor_id=actor_id,
        subject=f"subject:{actor_id}",
        name=actor_id,
        role=role,
        actor_type=actor_type,
        phase=Phase.PHASE_1_OFFCHAIN,
        token_id=f"token:{actor_id}",
        issued_at=1,
        expires_at=2,
        identity_version=1,
    )


def test_virtual_ledger_requires_the_canonical_owner() -> None:
    owner = actor("owner-local", ActorType.OWNER, AgentRole.OWNER)
    agent = actor("agent-theo", ActorType.AGENT, AgentRole.ENGINEERING)

    VirtualLedgerPolicy.require_owner(owner, "owner-local")

    with pytest.raises(LedgerAuthorizationError):
        VirtualLedgerPolicy.require_owner(agent, "owner-local")


def test_virtual_ledger_amounts_are_bounded_in_the_database_integer_range() -> None:
    VirtualLedgerPolicy.validate_amount(MAX_SIGNED_BIGINT)

    with pytest.raises(ValueError, match="signed BIGINT"):
        VirtualLedgerPolicy.validate_amount(MAX_SIGNED_BIGINT + 1)

    with pytest.raises(ValueError, match="signed BIGINT"):
        VirtualLedgerPolicy.validate_amount(-1)


def test_retention_is_integer_based_and_balances_net_and_retained_values() -> None:
    assert VirtualLedgerPolicy.calculate_retention(10_000, 2_500) == (7_500, 2_500)
    assert VirtualLedgerPolicy.calculate_retention(3, 3_333) == (3, 0)

    with pytest.raises(ValueError, match="retention_bps"):
        VirtualLedgerPolicy.calculate_retention(100, 10_001)


def test_direct_agent_transfers_are_rejected() -> None:
    with pytest.raises(DirectAgentTransferError):
        VirtualLedgerPolicy.reject_direct_agent_transfer(
            sender_agent_id="agent-theo",
            recipient_agent_id="agent-iris",
        )


def test_treasury_balance_exposes_the_accounting_invariant() -> None:
    balance = TreasuryBalance(
        account_id="virtual-reward-treasury",
        asset_type="VIRTUAL_REWARD",
        currency="VIRTUAL",
        funded_lamports=1_000,
        available_lamports=500,
        reserved_lamports=200,
        paid_lamports=250,
        retained_lamports=50,
        version=4,
    )

    assert balance.total_accounted_lamports == balance.funded_lamports


def test_ledger_entry_idempotent_response_round_trips() -> None:
    entry = VirtualLedgerEntry(
        entry_id="ledger-1",
        account_id="virtual-reward-treasury",
        entry_type="REWARD_PAYMENT",
        status="Paid",
        amount_lamports=750,
        asset_type="VIRTUAL_REWARD",
        currency="VIRTUAL",
        task_id="T-001",
        allocation_id="allocation-1",
        agent_id="agent-theo",
        calculation_version="reward-payment-v1",
        idempotency_key="payment-1:payment",
        recorded_by="owner-local",
        correlation_id="corr-1",
        created_at="2026-08-29T00:00:00+00:00",
    )

    restored = VirtualLedgerEntry.from_payload(entry.to_payload())

    assert restored == entry
