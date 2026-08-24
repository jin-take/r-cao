from dataclasses import dataclass
from enum import Enum

from .models import AgentRole, TaskState


POLICY_VERSION = "constitutional-policy-v1"


class PolicyViolation(ValueError):
    """Raised when a constitutional rule rejects an operation."""


class Phase(str, Enum):
    PHASE_1_OFFCHAIN = "PHASE_1_OFFCHAIN"
    DEVNET = "DEVNET"
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


class PolicyAction(str, Enum):
    ISSUE_TASK = "ISSUE_TASK"
    FINAL_ACCEPT_TASK = "FINAL_ACCEPT_TASK"
    FINAL_REJECT_TASK = "FINAL_REJECT_TASK"
    CANCEL_TASK = "CANCEL_TASK"
    POST_REWARD = "POST_REWARD"
    DECIDE_TREASURY = "DECIDE_TREASURY"
    CHANGE_POLICY = "CHANGE_POLICY"
    EXTERNAL_INTAKE = "EXTERNAL_INTAKE"
    DIRECT_AGENT_TRANSFER = "DIRECT_AGENT_TRANSFER"
    MASTER_WALLET_TRANSFER = "MASTER_WALLET_TRANSFER"
    MAINNET_ASSET_OPERATION = "MAINNET_ASSET_OPERATION"


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_OWNER_APPROVAL = "require_owner_approval"
    DENY = "deny"


class PhaseCapability(str, Enum):
    VIRTUAL_LEDGER = "VIRTUAL_LEDGER"
    OWNER_TASK_INTAKE = "OWNER_TASK_INTAKE"
    SOLANA_DEVNET = "SOLANA_DEVNET"
    MPP_DEVNET = "MPP_DEVNET"
    SOLANA_TESTNET = "SOLANA_TESTNET"
    EXTERNAL_INTAKE = "EXTERNAL_INTAKE"
    CUSTOMER_ASSETS = "CUSTOMER_ASSETS"
    MAINNET_ASSETS = "MAINNET_ASSETS"
    DIRECT_AGENT_TRANSFER = "DIRECT_AGENT_TRANSFER"


@dataclass(frozen=True)
class PhaseGate:
    phase: Phase
    allowed_capabilities: frozenset[PhaseCapability]
    description: str


PHASE_GATES: dict[Phase, PhaseGate] = {
    Phase.PHASE_1_OFFCHAIN: PhaseGate(
        phase=Phase.PHASE_1_OFFCHAIN,
        allowed_capabilities=frozenset(
            {
                PhaseCapability.VIRTUAL_LEDGER,
                PhaseCapability.OWNER_TASK_INTAKE,
            }
        ),
        description="Off-chain control plane with virtual ledger only",
    ),
    Phase.DEVNET: PhaseGate(
        phase=Phase.DEVNET,
        allowed_capabilities=frozenset(
            {
                PhaseCapability.VIRTUAL_LEDGER,
                PhaseCapability.OWNER_TASK_INTAKE,
                PhaseCapability.SOLANA_DEVNET,
                PhaseCapability.MPP_DEVNET,
            }
        ),
        description="Devnet-only payment experiments with bounded policy",
    ),
    Phase.TESTNET: PhaseGate(
        phase=Phase.TESTNET,
        allowed_capabilities=frozenset(
            {
                PhaseCapability.VIRTUAL_LEDGER,
                PhaseCapability.OWNER_TASK_INTAKE,
                PhaseCapability.SOLANA_DEVNET,
                PhaseCapability.MPP_DEVNET,
                PhaseCapability.SOLANA_TESTNET,
            }
        ),
        description="Testnet experiments without customer or mainnet assets",
    ),
    Phase.MAINNET: PhaseGate(
        phase=Phase.MAINNET,
        allowed_capabilities=frozenset(),
        description="Blocked until a later constitutional and security gate",
    ),
}


_ROLE_ACTIONS: dict[PolicyAction, frozenset[AgentRole]] = {
    PolicyAction.ISSUE_TASK: frozenset({AgentRole.OWNER}),
    PolicyAction.FINAL_ACCEPT_TASK: frozenset({AgentRole.OWNER}),
    PolicyAction.FINAL_REJECT_TASK: frozenset({AgentRole.OWNER}),
    PolicyAction.CANCEL_TASK: frozenset({AgentRole.OWNER}),
    PolicyAction.POST_REWARD: frozenset({AgentRole.OWNER, AgentRole.TREASURY}),
    PolicyAction.DECIDE_TREASURY: frozenset({AgentRole.OWNER}),
    PolicyAction.CHANGE_POLICY: frozenset({AgentRole.OWNER}),
}

_OWNER_ONLY_ACTIONS = frozenset(
    {
        PolicyAction.ISSUE_TASK,
        PolicyAction.FINAL_ACCEPT_TASK,
        PolicyAction.FINAL_REJECT_TASK,
        PolicyAction.CANCEL_TASK,
        PolicyAction.DECIDE_TREASURY,
        PolicyAction.CHANGE_POLICY,
    }
)

_HARD_FORBIDDEN_ACTIONS = frozenset(
    {
        PolicyAction.EXTERNAL_INTAKE,
        PolicyAction.DIRECT_AGENT_TRANSFER,
    }
)

_PHASE_BOUND_ACTIONS: dict[PolicyAction, PhaseCapability] = {
    PolicyAction.MASTER_WALLET_TRANSFER: PhaseCapability.MAINNET_ASSETS,
    PolicyAction.MAINNET_ASSET_OPERATION: PhaseCapability.MAINNET_ASSETS,
}


def require_phase_capability(
    phase: Phase,
    capability: PhaseCapability,
) -> None:
    gate = PHASE_GATES[phase]
    if capability not in gate.allowed_capabilities:
        raise PolicyViolation(
            f"{capability.value} is unavailable in {phase.value}"
        )


def evaluate_policy(
    role: AgentRole,
    action: PolicyAction,
    *,
    phase: Phase = Phase.PHASE_1_OFFCHAIN,
) -> PolicyDecision:
    """Return the constitutional decision without executing the operation."""
    if action in _HARD_FORBIDDEN_ACTIONS:
        return PolicyDecision.DENY

    phase_capability = _PHASE_BOUND_ACTIONS.get(action)
    if phase_capability is not None:
        if phase_capability not in PHASE_GATES[phase].allowed_capabilities:
            return PolicyDecision.DENY

    allowed_roles = _ROLE_ACTIONS.get(action)
    if allowed_roles is None:
        return PolicyDecision.DENY

    if action is PolicyAction.ISSUE_TASK:
        if (
            PhaseCapability.OWNER_TASK_INTAKE
            not in PHASE_GATES[phase].allowed_capabilities
        ):
            return PolicyDecision.DENY

    if action in _OWNER_ONLY_ACTIONS and role is not AgentRole.OWNER:
        return PolicyDecision.REQUIRE_OWNER_APPROVAL

    if role not in allowed_roles:
        return PolicyDecision.DENY

    return PolicyDecision.ALLOW


def authorize_constitutional_action(
    role: AgentRole,
    action: PolicyAction,
    *,
    phase: Phase = Phase.PHASE_1_OFFCHAIN,
) -> None:
    """Reject every operation that is not directly authorized by Policy."""
    decision = evaluate_policy(role, action, phase=phase)
    if decision is PolicyDecision.ALLOW:
        return
    if decision is PolicyDecision.REQUIRE_OWNER_APPROVAL:
        raise PolicyViolation(f"{action.value} requires Owner approval")
    raise PolicyViolation(
        f"{action.value} is denied by {POLICY_VERSION} in {phase.value}"
    )


def require_owner(role: AgentRole, action: str) -> None:
    if role is not AgentRole.OWNER:
        raise PolicyViolation(f"{action} requires Owner authority")


def authorize_task_transition(
    role: AgentRole,
    from_state: TaskState,
    to_state: TaskState,
) -> None:
    transition_action: PolicyAction | None = None
    if from_state is TaskState.DRAFT and to_state is TaskState.ISSUED:
        transition_action = PolicyAction.ISSUE_TASK
    elif (
        from_state is TaskState.IN_REVIEW
        and to_state is TaskState.ACCEPTED
    ):
        transition_action = PolicyAction.FINAL_ACCEPT_TASK
    elif (
        from_state is TaskState.IN_REVIEW
        and to_state is TaskState.REJECTED
    ):
        transition_action = PolicyAction.FINAL_REJECT_TASK
    elif to_state is TaskState.CANCELLED:
        transition_action = PolicyAction.CANCEL_TASK

    if transition_action is not None:
        authorize_constitutional_action(role, transition_action)
        return

    if to_state is TaskState.REWARDED:
        authorize_constitutional_action(role, PolicyAction.POST_REWARD)


def authorize_treasury_decision(role: AgentRole) -> None:
    authorize_constitutional_action(role, PolicyAction.DECIDE_TREASURY)
