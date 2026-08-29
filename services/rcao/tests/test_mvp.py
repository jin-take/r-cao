from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.auth import ActorContext, ActorType
from app.models import AgentRole
from app.mvp import (
    ApprovalDecision,
    ApprovalDecisionCommand,
    AuditCommand,
    AuditResult,
    ExternalActionCreateCommand,
    ExternalActionScopeCommand,
    ExternalActionChannel,
    MvpAuthorizationError,
    OwnerDirectedStore,
    OwnerEvaluationCommand,
    PolicyResult,
    Priority,
    RewardApprovalCommand,
    RiskLevel,
    ReviewCommand,
    TaskCreateCommand,
    TaskStatus,
)
from app.policy import Phase


NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def actor(
    actor_id: str,
    role: AgentRole,
    actor_type: ActorType = ActorType.AGENT,
) -> ActorContext:
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


OWNER = actor("owner-local", AgentRole.OWNER, ActorType.OWNER)
THEO = actor("agent-theo", AgentRole.ENGINEERING)
ASTRA = actor("agent-astra", AgentRole.REVIEWER)
IRIS = actor("agent-iris", AgentRole.AUDITOR)


def task_command(*, external_action_allowed: bool = False) -> TaskCreateCommand:
    return TaskCreateCommand(
        title="Test Owner Task",
        objective="Exercise the complete Owner-directed workflow",
        background="MVP test",
        priority=Priority.HIGH,
        deadline=datetime(2026, 9, 30, tzinfo=timezone.utc),
        acceptance_criteria=["review", "audit", "owner decision"],
        reward_budget_lamports=1_000_000_000,
        assigned_executive_agent_id="agent-theo",
        risk_level=RiskLevel.LOW,
        external_action_allowed=external_action_allowed,
    )


def advance_to_review(store: OwnerDirectedStore) -> str:
    task = store.create_task(OWNER, task_command())
    store.transition_task(OWNER, task.id, TaskStatus.APPROVED)
    store.transition_task(THEO, task.id, TaskStatus.PLANNING)
    store.transition_task(THEO, task.id, TaskStatus.IN_PROGRESS)
    store.transition_task(THEO, task.id, TaskStatus.REVIEW)
    return task.id


def test_seed_contains_six_named_executives_and_mvp_pending_items() -> None:
    store = OwnerDirectedStore(clock=lambda: NOW)

    executives = store.list_agents(include_sub_agents=False)

    assert len(executives) == 6
    assert {agent.name for agent in executives} >= {"Aria", "Mira", "Theo", "Noah", "Iris", "Luca"}
    assert len(store.list_tasks()) == 3
    assert len(store.list_approvals()) >= 4
    assert len(store.list_rewards()) == 1
    assert len(store.list_external_actions()) == 1
    approval_log = next(item for item in store.audit_logs if item.target_id == "reward-001")
    assert approval_log.action == "REWARD_APPROVAL_PENDING"
    assert approval_log.policy_result is not PolicyResult.DENY


def test_only_owner_can_create_task_and_reward_budget_is_not_paid() -> None:
    store = OwnerDirectedStore(clock=lambda: NOW)

    with pytest.raises(MvpAuthorizationError):
        store.create_task(THEO, task_command())

    task = store.create_task(OWNER, task_command())
    reward = next(item for item in store.list_rewards() if item.task_id == task.id)

    assert task.status is TaskStatus.DRAFT
    assert reward.reward_budget_lamports == 1_000_000_000
    assert reward.approved_reward_lamports is None
    assert reward.paid_reward_lamports == 0
    assert store.audit_logs[-1].action == "CREATE_TASK"
    assert any(item.policy_result.value == "DENY" for item in store.audit_logs)


def test_workflow_requires_independent_review_audit_and_owner_evaluation() -> None:
    store = OwnerDirectedStore(clock=lambda: NOW)
    task_id = advance_to_review(store)

    with pytest.raises(MvpAuthorizationError):
        store.submit_review(THEO, task_id, ReviewCommand(quality=90, completeness=90, correctness=90))

    store.submit_review(
        ASTRA,
        task_id,
        ReviewCommand(quality=90, completeness=90, correctness=95, comment="Pass"),
    )
    assert store.tasks[task_id].status is TaskStatus.AUDIT

    store.record_audit(
        IRIS,
        task_id,
        AuditCommand(
            policy_compliance=True,
            security_risk=RiskLevel.LOW,
            external_action_check=True,
            reward_manipulation_check=True,
            authority_violation_check=True,
            result=AuditResult.PASS,
        ),
    )
    assert store.tasks[task_id].status is TaskStatus.OWNER_REVIEW

    store.evaluate_task(
        OWNER,
        task_id,
        OwnerEvaluationCommand(
            quality=88,
            difficulty=3,
            contribution=90,
            timeliness=95,
            rework=0,
            strategic_value=80,
            owner_comment="Explicit Owner evaluation",
        ),
    )
    reward_approval = next(
        item for item in store.list_approvals()
        if item.approval_type.value == "REWARD"
        and store.rewards[item.target_id].task_id == task_id
    )
    approved_reward = store.decide_approval(
        OWNER,
        reward_approval.id,
        ApprovalDecisionCommand(decision=ApprovalDecision.APPROVE),
    )
    reward = store.rewards[approved_reward.target_id]
    assert reward.status.value == "Approved"
    assert reward.paid_reward_lamports == 0
    assert reward.id in store.reward_ledgers

    completion = next(item for item in store.list_approvals() if item.target_id == task_id)
    store.decide_approval(
        OWNER,
        completion.id,
        ApprovalDecisionCommand(decision=ApprovalDecision.APPROVE),
    )

    assert store.tasks[task_id].status is TaskStatus.COMPLETED
    assert all(reward.paid_reward_lamports == 0 for reward in store.rewards.values())


def test_owner_request_changes_returns_task_to_rework() -> None:
    store = OwnerDirectedStore(clock=lambda: NOW)
    task_id = advance_to_review(store)
    store.submit_review(
        ASTRA,
        task_id,
        ReviewCommand(quality=60, completeness=60, correctness=60),
    )
    assert store.tasks[task_id].status is TaskStatus.AUDIT
    store.record_audit(
        IRIS,
        task_id,
        AuditCommand(
            policy_compliance=True,
            security_risk=RiskLevel.LOW,
            external_action_check=True,
            reward_manipulation_check=True,
            authority_violation_check=True,
            result=AuditResult.PASS,
        ),
    )
    completion = next(item for item in store.list_approvals() if item.target_id == task_id)

    store.decide_approval(
        OWNER,
        completion.id,
        ApprovalDecisionCommand(
            decision=ApprovalDecision.REQUEST_CHANGES,
            comment="Owner requested more evidence",
        ),
    )

    assert store.tasks[task_id].status is TaskStatus.REWORK


def test_audit_fail_does_not_advance_to_owner_review() -> None:
    store = OwnerDirectedStore(clock=lambda: NOW)
    task_id = advance_to_review(store)
    store.submit_review(
        ASTRA,
        task_id,
        ReviewCommand(quality=90, completeness=90, correctness=90),
    )
    store.record_audit(
        IRIS,
        task_id,
        AuditCommand(
            policy_compliance=False,
            security_risk=RiskLevel.HIGH,
            external_action_check=False,
            reward_manipulation_check=True,
            authority_violation_check=False,
            result=AuditResult.FAIL,
            comment="Policy evidence is incomplete",
        ),
    )

    assert store.tasks[task_id].status is TaskStatus.AUDIT
    assert not any(item.target_id == task_id for item in store.list_approvals())


def test_reward_above_budget_requires_owner_reason() -> None:
    store = OwnerDirectedStore(clock=lambda: NOW)
    store.evaluate_task(
        OWNER,
        "T-001",
        OwnerEvaluationCommand(
            quality=90,
            difficulty=3,
            contribution=90,
            timeliness=90,
            rework=0,
            strategic_value=90,
            owner_comment="Evaluation before final allocation",
        ),
    )
    reward = store.list_rewards()[0]

    with pytest.raises(ValueError, match="reason"):
        store.approve_reward(
            OWNER,
            reward.id,
            RewardApprovalCommand(approved_reward_lamports=reward.reward_budget_lamports + 1),
        )

    approved = store.approve_reward(
        OWNER,
        reward.id,
        RewardApprovalCommand(
            approved_reward_lamports=reward.reward_budget_lamports + 1,
            reason="Owner-approved exceptional contribution",
        ),
    )
    assert approved.status.value == "Approved"
    assert approved.paid_reward_lamports == 0


def test_external_action_is_scoped_and_never_sent_by_mvp() -> None:
    store = OwnerDirectedStore(clock=lambda: NOW)
    task = store.create_task(OWNER, task_command(external_action_allowed=True))
    request = store.create_external_action(
        THEO,
        ExternalActionCreateCommand(
            task_id=task.id,
            recipient="recipient@example.test",
            channel=ExternalActionChannel.EMAIL,
            purpose="Clarification",
            content="Approved content",
            allowed_action_count=1,
            expires_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ),
    )

    with pytest.raises(MvpAuthorizationError):
        store.check_external_action_scope(
            THEO,
            request.id,
            ExternalActionScopeCommand(
                recipient=request.recipient,
                channel=request.channel,
                content=request.content,
            ),
        )

    store.decide_external_action(OWNER, request.id, ApprovalDecision.APPROVE)
    result = store.check_external_action_scope(
        THEO,
        request.id,
        ExternalActionScopeCommand(
            recipient=request.recipient,
            channel=request.channel,
            content=request.content,
        ),
    )
    assert result["policy_result"] == "ALLOW_WITH_SCOPE"
    assert result["execution"] == "NOT_EXECUTED_MVP"

    with pytest.raises(MvpAuthorizationError):
        store.check_external_action_scope(
            THEO,
            request.id,
            ExternalActionScopeCommand(
                recipient=request.recipient,
                channel=request.channel,
                content="different content",
            ),
        )
