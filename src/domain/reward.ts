export interface RewardContribution {
  agentId: string;
  contributionScore: number;
}

export interface RewardAllocation {
  agentId: string;
  amountLamports: number;
}

export interface RewardResult {
  allocations: RewardAllocation[];
  retainedLamports: number;
}

const MINIMUM_ACCEPTED_SCORE = 60;

export function calculateVirtualReward(
  rewardPoolLamports: number,
  finalScore: number,
  contributions: RewardContribution[],
): RewardResult {
  if (!Number.isSafeInteger(rewardPoolLamports) || rewardPoolLamports < 0) {
    throw new Error("rewardPoolLamports must be a non-negative safe integer");
  }
  if (finalScore < MINIMUM_ACCEPTED_SCORE || finalScore > 100) {
    throw new Error("finalScore must be between 60 and 100 for Reward allocation");
  }
  if (contributions.length === 0) throw new Error("at least one contribution is required");
  if (contributions.some(({ contributionScore }) => contributionScore <= 0 || contributionScore > 100)) {
    throw new Error("contributionScore must be between 1 and 100");
  }

  const payable = Math.floor((rewardPoolLamports * finalScore) / 100);
  const totalWeight = contributions.reduce((sum, item) => sum + item.contributionScore, 0);
  let allocated = 0;

  const allocations = contributions.map((item, index) => {
    const amountLamports =
      index === contributions.length - 1
        ? payable - allocated
        : Math.floor((payable * item.contributionScore) / totalWeight);
    allocated += amountLamports;
    return { agentId: item.agentId, amountLamports };
  });

  return { allocations, retainedLamports: rewardPoolLamports - payable };
}
