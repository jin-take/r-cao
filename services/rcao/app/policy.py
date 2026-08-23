from .models import AgentRole, TaskState


class PolicyViolation(ValueError):
    """Raised when a constitutional rule rejects an operation."""


def require_owner(role: AgentRole, action: str) -> None:
    if role is not AgentRole.OWNER:
        raise PolicyViolation(f"{action} requires Owner authority")


def authorize_task_transition(
    role: AgentRole,
    from_state: TaskState,
    to_state: TaskState,
) -> None:
    owner_only = (
        (from_state is TaskState.DRAFT and to_state is TaskState.ISSUED)
        or (
            from_state is TaskState.IN_REVIEW
            and to_state in {TaskState.ACCEPTED, TaskState.REJECTED}
        )
        or to_state is TaskState.CANCELLED
    )
    if owner_only:
        require_owner(role, f"Task transition {from_state} -> {to_state}")

    if to_state is TaskState.REWARDED and role not in {
        AgentRole.TREASURY,
        AgentRole.OWNER,
    }:
        raise PolicyViolation("Reward posting requires Treasury or Owner authority")


def authorize_treasury_decision(role: AgentRole) -> None:
    require_owner(role, "Treasury proposal decision")

