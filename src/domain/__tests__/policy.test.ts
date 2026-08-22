import { describe, expect, it } from "vitest";
import { authorizeTreasuryDecision, PolicyViolation } from "../policy";
import { transitionTask } from "../task-state-machine";
import type { Task } from "../model";

const task: Task = {
  id: "task-1",
  title: "Foundation",
  description: "Build Phase 1",
  rewardLamports: 1_000_000_000,
  difficulty: 3,
  state: "DRAFT",
  deadline: "2026-09-01",
  acceptanceCriteria: ["tests pass"],
  issuedBy: null,
};

describe("constitutional policy", () => {
  it("allows only Owner to issue a Task", () => {
    expect(() => transitionTask(task, "ISSUED", "MANAGER")).toThrow(PolicyViolation);
    expect(transitionTask(task, "ISSUED", "OWNER").state).toBe("ISSUED");
  });

  it("allows only Owner to decide a Treasury proposal", () => {
    expect(() => authorizeTreasuryDecision("TREASURY")).toThrow(PolicyViolation);
    expect(() => authorizeTreasuryDecision("OWNER")).not.toThrow();
  });
});
