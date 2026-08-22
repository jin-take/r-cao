import type { Evaluation, Task, TaskAssignment, TreasuryProposal } from "./model";
import { requireOwner } from "./policy";
import { calculateVirtualReward, type RewardResult } from "./reward";
import { transitionTask } from "./task-state-machine";

export interface PhaseOneSimulationInput {
  task: Task;
  assignments: TaskAssignment[];
  evaluation: Evaluation;
  ownerId: string;
  treasuryAgentId: string;
}

export interface PhaseOneSimulationResult {
  task: Task;
  reward: RewardResult;
  treasuryProposal: TreasuryProposal;
  auditActions: string[];
}

export function runPhaseOneSimulation(input: PhaseOneSimulationInput): PhaseOneSimulationResult {
  requireOwner("OWNER", "Phase 1 simulation");

  if (input.assignments.length < 2) {
    throw new Error("Phase 1 completion requires multiple assigned Agents");
  }
  if (input.assignments.some((item) => item.agentId === input.evaluation.reviewerId)) {
    throw new Error("Reviewer must be independent from contributing Agents");
  }
  if (input.evaluation.taskId !== input.task.id) {
    throw new Error("Evaluation must reference the simulated Task");
  }

  let task = transitionTask(input.task, "ISSUED", "OWNER");
  task = { ...task, issuedBy: input.ownerId };
  task = transitionTask(task, "IN_PROGRESS", "MANAGER");
  task = transitionTask(task, "IN_REVIEW", "REVIEWER");
  task = transitionTask(task, "ACCEPTED", "OWNER");

  const reward = calculateVirtualReward(
    task.rewardLamports,
    input.evaluation.finalScore,
    input.assignments.map(({ agentId, contributionScore }) => ({
      agentId,
      contributionScore,
    })),
  );

  task = transitionTask(task, "REWARDED", "TREASURY");

  const treasuryProposal: TreasuryProposal = {
    id: `proposal-${task.id}`,
    proposalType: "INFRASTRUCTURE",
    amountLamports: reward.retainedLamports,
    expectedRoiBps: 500,
    risk: 2,
    status: "SUBMITTED",
    approvalBy: null,
  };

  return {
    task,
    reward,
    treasuryProposal,
    auditActions: [
      "TASK_ISSUED",
      "AGENTS_ASSIGNED",
      "DELIVERABLE_REVIEWED",
      "OWNER_ACCEPTED",
      "VIRTUAL_REWARD_POSTED",
      "TREASURY_PROPOSAL_SUBMITTED",
    ],
  };
}
