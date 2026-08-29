from datetime import datetime, timezone

import pytest

from app.agent_registry import (
    AgentMembership,
    AgentNotEligibleError,
    AgentRegistryPolicy,
    AgentRegistryRepository,
    DelegationError,
    DelegationGrant,
    MembershipError,
    RegisteredAgent,
    RegistryAuthorizationError,
)


NOW = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)


def agent(**changes: object) -> RegisteredAgent:
    values: dict[str, object] = {
        "agent_id": "agent-theo",
        "identity_id": "agent-theo",
        "name": "Theo",
        "role": "ENGINEERING",
        "organization_layer": "VALUE_CREATION",
        "mission": "Build the control plane",
        "responsibilities": ("Implementation",),
        "authority": ("Implement approved Tasks",),
        "prohibited_actions": ("Change authority",),
        "reports_to": "owner-local",
        "agent_type": "EXECUTIVE",
        "status": "ACTIVE",
        "version": 1,
        "model": "policy-bound",
        "provider": "TEST",
        "prompt_version": "v1",
        "capability_hash": "sha256:theo",
        "allowed_tools": ("repo.read",),
        "network_scope": ("OFFCHAIN",),
        "budget_scope": {"max_lamports": 100},
        "risk_scope": {"max": "LOW"},
        "budget_limit_lamports": 100,
        "expires_at": None,
    }
    values.update(changes)
    return RegisteredAgent(**values)


def membership(**changes: object) -> AgentMembership:
    values: dict[str, object] = {
        "task_id": "T-001",
        "agent_id": "agent-theo",
        "membership_role": "EXECUTIVE",
        "assigned_by": "owner-local",
        "active": True,
        "expires_at": None,
    }
    values.update(changes)
    return AgentMembership(**values)


def delegation(**changes: object) -> DelegationGrant:
    values: dict[str, object] = {
        "delegation_id": "delegation-001",
        "parent_agent_id": "agent-theo",
        "child_agent_id": "agent-astra",
        "task_id": "T-001",
        "allowed_scope": ("REVIEW",),
        "budget_limit_lamports": 10,
        "risk_scope": {"max": "LOW"},
        "status": "ACTIVE",
        "expires_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }
    values.update(changes)
    return DelegationGrant(**values)


def test_active_agent_requires_task_membership_and_respects_scopes() -> None:
    AgentRegistryPolicy.ensure_can_participate(
        agent(),
        task_id="T-001",
        membership=membership(),
        required_tool="repo.read",
        network="OFFCHAIN",
        amount_lamports=50,
        now=NOW,
    )


def test_hard_budget_limit_applies_when_scope_is_empty() -> None:
    with pytest.raises(AgentNotEligibleError, match="budget limit"):
        AgentRegistryPolicy.ensure_can_participate(
            agent(budget_scope={}, budget_limit_lamports=10),
            task_id="T-001",
            membership=membership(),
            amount_lamports=11,
            now=NOW,
        )


def test_agent_and_delegation_risk_scopes_are_enforced() -> None:
    with pytest.raises(AgentNotEligibleError, match="risk classification"):
        AgentRegistryPolicy.ensure_can_participate(
            agent(risk_scope={"max": "LOW"}),
            task_id="T-001",
            membership=membership(),
            risk_level="MEDIUM",
            now=NOW,
        )

    with pytest.raises(DelegationError, match="risk classification"):
        AgentRegistryPolicy.ensure_can_participate(
            agent(risk_scope={"max": "HIGH"}),
            task_id="T-001",
            membership=membership(),
            delegation=delegation(risk_scope={"max": "LOW"}),
            action="REVIEW",
            risk_level="MEDIUM",
            now=NOW,
        )


def test_empty_requested_delegation_is_rejected() -> None:
    class RegistryTransaction:
        def fetch_one(self, statement: str, params: tuple[object, ...] = ()) -> object:
            return None

    registry = AgentRegistryRepository(RegistryTransaction())
    registry.require_agent = lambda agent_id: agent()  # type: ignore[method-assign]
    registry.get_delegation = lambda delegation_id: None  # type: ignore[method-assign]

    with pytest.raises(DelegationError, match="not registered"):
        registry.ensure_can_participate("agent-theo", delegation_id="missing")


def test_suspended_agent_cannot_participate() -> None:
    with pytest.raises(AgentNotEligibleError, match="status"):
        AgentRegistryPolicy.ensure_can_participate(
            agent(status="SUSPENDED"),
            task_id="T-001",
            membership=membership(),
            now=NOW,
        )


def test_missing_or_expired_membership_is_rejected() -> None:
    with pytest.raises(MembershipError):
        AgentRegistryPolicy.ensure_can_participate(agent(), task_id="T-001", now=NOW)

    with pytest.raises(MembershipError, match="expired"):
        AgentRegistryPolicy.ensure_can_participate(
            agent(),
            task_id="T-001",
            membership=membership(expires_at=datetime(2026, 8, 29, 11, tzinfo=timezone.utc)),
            now=NOW,
        )


def test_delegation_is_task_bound_and_scope_bound() -> None:
    child = agent(agent_id="agent-astra", identity_id="agent-astra", name="Astra")
    grant = delegation()
    AgentRegistryPolicy.ensure_can_participate(
        child,
        task_id="T-001",
        membership=membership(agent_id="agent-astra", membership_role="REVIEWER"),
        delegation=grant,
        action="REVIEW",
        amount_lamports=10,
        now=NOW,
    )

    with pytest.raises(DelegationError, match="scope"):
        AgentRegistryPolicy.ensure_can_participate(
            child,
            task_id="T-001",
            membership=membership(agent_id="agent-astra", membership_role="REVIEWER"),
            delegation=grant,
            action="CHANGE_AUTHORITY",
            now=NOW,
        )


def test_tool_and_network_allowlists_are_closed_by_default() -> None:
    with pytest.raises(AgentNotEligibleError, match="Tool"):
        AgentRegistryPolicy.ensure_can_participate(
            agent(),
            task_id="T-001",
            membership=membership(),
            required_tool="repo.write",
            now=NOW,
        )

    with pytest.raises(AgentNotEligibleError, match="Network"):
        AgentRegistryPolicy.ensure_can_participate(
            agent(),
            task_id="T-001",
            membership=membership(),
            network="SOLANA_DEVNET",
            now=NOW,
        )


def test_registry_mutations_require_owner() -> None:
    with pytest.raises(RegistryAuthorizationError, match="Owner"):
        AgentRegistryPolicy.require_owner("EXECUTIVE")

    AgentRegistryPolicy.require_owner("OWNER")
