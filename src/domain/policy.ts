import type { AgentRole, TaskState } from "./model";

export class PolicyViolation extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PolicyViolation";
  }
}

export function requireOwner(role: AgentRole, action: string): void {
  if (role !== "OWNER") {
    throw new PolicyViolation(`${action} requires Owner authority`);
  }
}

export function authorizeTaskTransition(
  role: AgentRole,
  from: TaskState,
  to: TaskState,
): void {
  const ownerOnly =
    (from === "DRAFT" && to === "ISSUED") ||
    (from === "IN_REVIEW" && (to === "ACCEPTED" || to === "REJECTED")) ||
    to === "CANCELLED";

  if (ownerOnly) requireOwner(role, `Task transition ${from} -> ${to}`);

  if (to === "REWARDED" && role !== "TREASURY" && role !== "OWNER") {
    throw new PolicyViolation("Reward posting requires Treasury or Owner authority");
  }
}

export function authorizeTreasuryDecision(role: AgentRole): void {
  requireOwner(role, "Treasury proposal decision");
}
