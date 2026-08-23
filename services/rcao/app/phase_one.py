from .models import (
    AgentRole,
    PhaseOneSimulationInput,
    PhaseOneSimulationResult,
    RewardContribution,
    TaskState,
    TreasuryProposal,
)
from .policy import require_owner
from .reward import calculate_virtual_reward
from .task_state import transition_task


def run_phase_one_simulation(
    request: PhaseOneSimulationInput,
) -> PhaseOneSimulationResult:
    require_owner(AgentRole.OWNER, "Phase 1 simulation")

    if any(
        assignment.agent_id == request.evaluation.reviewer_id
        for assignment in request.assignments
    ):
        raise ValueError("Reviewer must be independent from contributors")
    if request.evaluation.task_id != request.task.id:
        raise ValueError("Evaluation must reference the simulated Task")

    task = transition_task(request.task, TaskState.ISSUED, AgentRole.OWNER)
    task = task.model_copy(update={"issued_by": request.owner_id})
    task = transition_task(task, TaskState.IN_PROGRESS, AgentRole.MANAGER)
    task = transition_task(task, TaskState.IN_REVIEW, AgentRole.REVIEWER)
    task = transition_task(task, TaskState.ACCEPTED, AgentRole.OWNER)

    reward = calculate_virtual_reward(
        task.reward_lamports,
        request.evaluation.final_score,
        [
            RewardContribution(
                agent_id=assignment.agent_id,
                contribution_score=assignment.contribution_score,
            )
            for assignment in request.assignments
        ],
    )
    task = transition_task(task, TaskState.REWARDED, AgentRole.TREASURY)

    proposal = TreasuryProposal(
        id=f"proposal-{task.id}",
        proposal_type="INFRASTRUCTURE",
        amount_lamports=reward.retained_lamports,
        expected_roi_bps=500,
        risk=2,
        status="SUBMITTED",
    )

    return PhaseOneSimulationResult(
        task=task,
        reward=reward,
        treasury_proposal=proposal,
        audit_actions=[
            "TASK_ISSUED",
            "AGENTS_ASSIGNED",
            "DELIVERABLE_REVIEWED",
            "OWNER_ACCEPTED",
            "VIRTUAL_REWARD_POSTED",
            "TREASURY_PROPOSAL_SUBMITTED",
        ],
    )
