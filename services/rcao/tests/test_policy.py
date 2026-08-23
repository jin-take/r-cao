import pytest

from app.models import AgentRole, TaskState
from app.policy import PolicyViolation, authorize_task_transition


def test_only_owner_can_issue_task() -> None:
    with pytest.raises(PolicyViolation):
        authorize_task_transition(
            AgentRole.MANAGER,
            TaskState.DRAFT,
            TaskState.ISSUED,
        )

    authorize_task_transition(
        AgentRole.OWNER,
        TaskState.DRAFT,
        TaskState.ISSUED,
    )


def test_only_owner_can_decide_treasury() -> None:
    from app.policy import authorize_treasury_decision

    with pytest.raises(PolicyViolation):
        authorize_treasury_decision(AgentRole.TREASURY)

    authorize_treasury_decision(AgentRole.OWNER)

