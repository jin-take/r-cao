from .models import RewardAllocation, RewardContribution, RewardResult


MINIMUM_ACCEPTED_SCORE = 60


def calculate_virtual_reward(
    reward_pool_lamports: int,
    final_score: int,
    contributions: list[RewardContribution],
) -> RewardResult:
    if reward_pool_lamports < 0:
        raise ValueError("reward_pool_lamports must be non-negative")
    if final_score < MINIMUM_ACCEPTED_SCORE or final_score > 100:
        raise ValueError("final_score must be between 60 and 100")
    if not contributions:
        raise ValueError("at least one contribution is required")

    payable = (reward_pool_lamports * final_score) // 100
    total_weight = sum(item.contribution_score for item in contributions)
    allocated = 0
    allocations: list[RewardAllocation] = []

    for index, contribution in enumerate(contributions):
        amount = (
            payable - allocated
            if index == len(contributions) - 1
            else (payable * contribution.contribution_score) // total_weight
        )
        allocated += amount
        allocations.append(
            RewardAllocation(
                agent_id=contribution.agent_id,
                amount_lamports=amount,
            )
        )

    return RewardResult(
        allocations=allocations,
        retained_lamports=reward_pool_lamports - payable,
    )

