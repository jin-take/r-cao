from datetime import datetime, timezone

import pytest

from app.auth import ActorContext, ActorType
from app.models import AgentRole
from app.mvp import TaskStatus
from app.policy import Phase
from app.task_workflow import (
    ALLOWED_TRANSITIONS,
    PersistedTask,
    TaskWorkflowRepository,
    WorkflowAuthorizationError,
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


def test_persistent_task_row_maps_to_the_existing_domain_contract() -> None:
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    task = PersistedTask.from_record(
        {
            "id": "T-001",
            "title": "Persistent Task",
            "objective": "Exercise the durable workflow",
            "background": "",
            "priority": "HIGH",
            "deadline": now,
            "acceptance_criteria": '["evidence", "review"]',
            "reward_budget_lamports": 100,
            "assigned_executive_agent_id": "agent-theo",
            "risk_level": "LOW",
            "external_action_allowed": False,
            "owner_approval_required": True,
            "status": "DRAFT",
            "progress": 0,
            "created_by": "owner-local",
            "created_at": now,
            "updated_at": now,
            "version": 1,
        }
    )

    model = task.to_model()

    assert model.id == "T-001"
    assert model.status is TaskStatus.DRAFT
    assert model.acceptance_criteria == ["evidence", "review"]
    assert task.version == 1


def test_transition_matrix_has_no_terminal_escape() -> None:
    assert ALLOWED_TRANSITIONS["DRAFT"] == frozenset({"APPROVED", "CANCELLED"})
    assert ALLOWED_TRANSITIONS["OWNER_REVIEW"] == frozenset(
        {"COMPLETED", "REWORK", "REJECTED", "BLOCKED"}
    )
    assert ALLOWED_TRANSITIONS["COMPLETED"] == frozenset()
    assert ALLOWED_TRANSITIONS["REJECTED"] == frozenset()
    assert ALLOWED_TRANSITIONS["CANCELLED"] == frozenset()


def test_persistent_commands_require_the_canonical_owner_identity() -> None:
    repository = TaskWorkflowRepository(transaction=object())

    with pytest.raises(WorkflowAuthorizationError):
        repository._require_owner(actor("agent-theo", ActorType.AGENT, AgentRole.ENGINEERING))

    repository._require_owner(actor("owner-local", ActorType.OWNER, AgentRole.OWNER))
