import pytest

from app.models import (
    Evaluation,
    PhaseOneSimulationInput,
    Task,
    TaskAssignment,
)
from app.phase_one import run_phase_one_simulation


def request() -> PhaseOneSimulationInput:
    task = Task(
        id="task-phase-1",
        title="Run one organization cycle",
        description="Verify the first completion condition",
        reward_lamports=1_000_000_000,
        difficulty=3,
        deadline="2026-09-30",
        acceptance_criteria=["independent review", "virtual Reward"],
    )
    return PhaseOneSimulationInput(
        task=task,
        assignments=[
            TaskAssignment(
                task_id=task.id,
                agent_id="researcher",
                role="RESEARCHER",
                contribution_score=40,
            ),
            TaskAssignment(
                task_id=task.id,
                agent_id="builder",
                role="BUILDER",
                contribution_score=60,
            ),
        ],
        evaluation=Evaluation(
            task_id=task.id,
            reviewer_id="reviewer",
            quality=85,
            risk=20,
            comment="Acceptance criteria satisfied",
            final_score=80,
        ),
        owner_id="owner-local",
        treasury_agent_id="treasury",
    )


def test_phase_one_cycle_reaches_reward_and_treasury_proposal() -> None:
    result = run_phase_one_simulation(request())

    assert result.task.state == "REWARDED"
    assert len(result.reward.allocations) == 2
    assert result.treasury_proposal.status == "SUBMITTED"
    assert "OWNER_ACCEPTED" in result.audit_actions


def test_phase_one_rejects_self_review() -> None:
    data = request()
    data.evaluation.reviewer_id = "builder"

    with pytest.raises(ValueError, match="independent"):
        run_phase_one_simulation(data)

