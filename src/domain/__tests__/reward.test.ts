import { describe, expect, it } from "vitest";
import { calculateVirtualReward } from "../reward";

describe("virtual SOL Reward", () => {
  it("allocates quality-adjusted Reward by contribution and retains the remainder", () => {
    const result = calculateVirtualReward(1_000_000_000, 80, [
      { agentId: "research", contributionScore: 25 },
      { agentId: "builder", contributionScore: 50 },
      { agentId: "reviewer", contributionScore: 25 },
    ]);

    expect(result.allocations).toEqual([
      { agentId: "research", amountLamports: 200_000_000 },
      { agentId: "builder", amountLamports: 400_000_000 },
      { agentId: "reviewer", amountLamports: 200_000_000 },
    ]);
    expect(result.retainedLamports).toBe(200_000_000);
  });

  it("rejects a score below the acceptance threshold", () => {
    expect(() =>
      calculateVirtualReward(1_000, 59, [{ agentId: "builder", contributionScore: 100 }]),
    ).toThrow();
  });
});
