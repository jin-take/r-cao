export const agentRoles = [
  "OWNER",
  "MANAGER",
  "RESEARCHER",
  "BUILDER",
  "REVIEWER",
  "TREASURY",
  "AUDITOR",
] as const;

export type AgentRole = (typeof agentRoles)[number];

export const taskStates = [
  "DRAFT",
  "ISSUED",
  "IN_PROGRESS",
  "IN_REVIEW",
  "ACCEPTED",
  "REJECTED",
  "REWARDED",
  "CANCELLED",
] as const;

export type TaskState = (typeof taskStates)[number];

export interface Agent {
  id: string;
  name: string;
  role: AgentRole;
  capabilityHash: string;
  model: string;
  status: "ACTIVE" | "PAUSED" | "RETIRED";
  reputation: number;
  rank: number;
}

export interface Task {
  id: string;
  title: string;
  description: string;
  rewardLamports: number;
  difficulty: 1 | 2 | 3 | 4 | 5;
  state: TaskState;
  deadline: string;
  acceptanceCriteria: string[];
  issuedBy: string | null;
}

export interface TaskAssignment {
  taskId: string;
  agentId: string;
  role: Exclude<AgentRole, "OWNER">;
  contributionScore: number;
}

export interface Evaluation {
  taskId: string;
  reviewerId: string;
  quality: number;
  risk: number;
  comment: string;
  finalScore: number;
}

export interface LedgerEntry {
  id: string;
  agentId: string;
  type: "REWARD" | "ADJUSTMENT" | "TREASURY_RETENTION";
  amountLamports: number;
  source: string;
  txRef: string | null;
}

export interface TreasuryProposal {
  id: string;
  proposalType: "RESEARCH" | "INFRASTRUCTURE" | "PRODUCT" | "RESERVE";
  amountLamports: number;
  expectedRoiBps: number;
  risk: 1 | 2 | 3 | 4 | 5;
  status: "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED";
  approvalBy: string | null;
}

export interface AuditLog {
  id: string;
  actorId: string;
  action: string;
  before: unknown;
  after: unknown;
  hash: string;
  createdAt: string;
}
