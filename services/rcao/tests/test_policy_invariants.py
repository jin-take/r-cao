import pytest

from app.models import AgentRole
from app.policy import (
    PHASE_GATES,
    Phase,
    PhaseCapability,
    PolicyAction,
    PolicyDecision,
    PolicyViolation,
    authorize_constitutional_action,
    evaluate_policy,
    require_phase_capability,
)


def test_phase_one_gate_is_offchain_and_virtual_only() -> None:
    gate = PHASE_GATES[Phase.PHASE_1_OFFCHAIN]

    assert PhaseCapability.VIRTUAL_LEDGER in gate.allowed_capabilities
    assert PhaseCapability.OWNER_TASK_INTAKE in gate.allowed_capabilities

    require_phase_capability(
        Phase.PHASE_1_OFFCHAIN,
        PhaseCapability.VIRTUAL_LEDGER,
    )
    with pytest.raises(PolicyViolation):
        require_phase_capability(
            Phase.PHASE_1_OFFCHAIN,
            PhaseCapability.SOLANA_DEVNET,
        )


def test_devnet_gate_allows_bounded_payment_experiments() -> None:
    require_phase_capability(Phase.DEVNET, PhaseCapability.SOLANA_DEVNET)
    require_phase_capability(Phase.DEVNET, PhaseCapability.MPP_DEVNET)

    with pytest.raises(PolicyViolation):
        require_phase_capability(Phase.DEVNET, PhaseCapability.CUSTOMER_ASSETS)
    with pytest.raises(PolicyViolation):
        require_phase_capability(Phase.DEVNET, PhaseCapability.MAINNET_ASSETS)


def test_owner_and_treasury_action_matrix() -> None:
    assert (
        evaluate_policy(AgentRole.OWNER, PolicyAction.ISSUE_TASK)
        is PolicyDecision.ALLOW
    )
    assert (
        evaluate_policy(AgentRole.MANAGER, PolicyAction.ISSUE_TASK)
        is PolicyDecision.REQUIRE_OWNER_APPROVAL
    )
    assert (
        evaluate_policy(AgentRole.TREASURY, PolicyAction.POST_REWARD)
        is PolicyDecision.ALLOW
    )

    authorize_constitutional_action(
        AgentRole.OWNER,
        PolicyAction.FINAL_ACCEPT_TASK,
    )
    authorize_constitutional_action(
        AgentRole.TREASURY,
        PolicyAction.POST_REWARD,
    )
    with pytest.raises(PolicyViolation):
        authorize_constitutional_action(
            AgentRole.REVIEWER,
            PolicyAction.POST_REWARD,
        )


def test_constitutional_forbidden_actions_are_hard_denied() -> None:
    for role in AgentRole:
        for action in (
            PolicyAction.EXTERNAL_INTAKE,
            PolicyAction.DIRECT_AGENT_TRANSFER,
        ):
            assert evaluate_policy(role, action) is PolicyDecision.DENY
            with pytest.raises(PolicyViolation):
                authorize_constitutional_action(role, action)


def test_mainnet_and_customer_assets_remain_closed() -> None:
    for phase in Phase:
        with pytest.raises(PolicyViolation):
            require_phase_capability(phase, PhaseCapability.CUSTOMER_ASSETS)
        with pytest.raises(PolicyViolation):
            require_phase_capability(phase, PhaseCapability.MAINNET_ASSETS)

    with pytest.raises(PolicyViolation):
        authorize_constitutional_action(
            AgentRole.OWNER,
            PolicyAction.MASTER_WALLET_TRANSFER,
            phase=Phase.MAINNET,
        )
    with pytest.raises(PolicyViolation):
        authorize_constitutional_action(
            AgentRole.OWNER,
            PolicyAction.MAINNET_ASSET_OPERATION,
            phase=Phase.DEVNET,
        )


def test_policy_change_is_owner_only() -> None:
    assert (
        evaluate_policy(AgentRole.OWNER, PolicyAction.CHANGE_POLICY)
        is PolicyDecision.ALLOW
    )
    assert (
        evaluate_policy(AgentRole.AUDITOR, PolicyAction.CHANGE_POLICY)
        is PolicyDecision.REQUIRE_OWNER_APPROVAL
    )
    with pytest.raises(PolicyViolation):
        authorize_constitutional_action(
            AgentRole.AUDITOR,
            PolicyAction.CHANGE_POLICY,
        )
    authorize_constitutional_action(
        AgentRole.OWNER,
        PolicyAction.CHANGE_POLICY,
    )
