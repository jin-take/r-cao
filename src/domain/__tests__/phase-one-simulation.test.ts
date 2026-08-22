import { describe, expect, it } from "vitest";
import { runPhaseOneSimulation } from "../phase-one-simulation";
import type { Evaluation, Task, TaskAssignment } from "../model";

const task: Task = {
  id: "task-phase-1",
  title: "Run one organization cycle",
  description: "Verify the first completion condition",
  rewardLamports: 1_000_000_000,
  difficulty: 3,
  state: "DRAFT",
  deadline: "2026-09-30",
  acceptanceCriteria: ["independent review", "virtual Reward"],
  issuedBy: null,
};

const assignments: TaskAssignment[] = [
  { taskId: task.id, agentId: "researcher", role: "RESEARCHER", contributionScore: 40 },
  { taskId: task.id, agentId: "builder", role: "BUILDER", contributionScore: 60 },
];

const evaluation: Evaluation = {
  taskId: task.id,
  reviewerId: "reviewer",
  quality: 85,
  risk: 20,
  comment: "Acceptance criteria satisfied",
  finalScore: 80,
};

describe("Phase 1 first completion condition", () => {
  it("runs Task through independent review, Reward, and Treasury proposal", () => {
    const result = runPhaseOneSimulation({
      task,
      assignments,
      evaluation,
      ownerId: "owner-local",
      treasuryAgentId: "treasury",
    });

    expect(result.task.state).toBe("REWARDED");
    expect(result.reward.allocations).toHaveLength(2);
    expect(result.treasuryProposal.status).toBe("SUBMITTED");
    expect(result.auditActions).toContain("OWNER_ACCEPTED");
    expect(result.auditActions).toContain("TREASURY_PROPOSAL_SUBMITTED");
  });

  it("rejects self-review by a contributing Agent", () => {
    expect(() =>
      runPhaseOneSimulation({
        task,
        assignments,
        evaluation: { ...evaluation, reviewerId: "builder" },
        ownerId: "owner-local",
        treasuryAgentId: "treasury",
      }),
    ).toThrow("Reviewer must be independent");
  });
});
