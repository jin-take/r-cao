import pytest

from app.models import RewardContribution
from app.reward import calculate_virtual_reward


def test_quality_adjusted_reward_uses_integer_lamports() -> None:
    result = calculate_virtual_reward(
        1_000_000_000,
        80,
        [
            RewardContribution(agent_id="research", contribution_score=25),
            RewardContribution(agent_id="builder", contribution_score=50),
            RewardContribution(agent_id="reviewer", contribution_score=25),
        ],
    )

    assert [item.amount_lamports for item in result.allocations] == [
        200_000_000,
        400_000_000,
        200_000_000,
    ]
    assert result.retained_lamports == 200_000_000


def test_reward_rejects_score_below_acceptance_threshold() -> None:
    with pytest.raises(ValueError):
        calculate_virtual_reward(
            1_000,
            59,
            [RewardContribution(agent_id="builder", contribution_score=100)],
        )

