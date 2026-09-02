// Read-side contracts for the Owner Console.
//
// The authoritative policy, state transitions, reward math, and Agent-message
// validation live in services/rcao (Python). These types deliberately contain
// no business logic; the console must not become a second control plane.

export const agentRoles = [
  "OWNER",
  "STRATEGY",
  "PRODUCT",
  "ENGINEERING",
  "OPERATIONS",
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

// Owner-Directed MVP read/write contracts. The Python Control Plane is the
// authoritative policy boundary; these types are intentionally presentation
// contracts for the Owner Console.
export const mvpAgentTypes = [
  "EXECUTIVE",
  "SUB_AGENT",
  "EXPANSION_AGENT",
  "AUDIT",
] as const;
export type MvpAgentType = (typeof mvpAgentTypes)[number];

export const mvpAgentStatuses = ["ACTIVE", "SUSPENDED", "RETIRED", "DRAFT"] as const;
export type MvpAgentStatus = (typeof mvpAgentStatuses)[number];

export const priorities = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type Priority = (typeof priorities)[number];

export const riskLevels = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type RiskLevel = (typeof riskLevels)[number];

export const mvpTaskStatuses = [
  "DRAFT",
  "APPROVED",
  "PLANNING",
  "IN_PROGRESS",
  "REVIEW",
  "AUDIT",
  "OWNER_REVIEW",
  "REWORK",
  "BLOCKED",
  "COMPLETED",
  "REJECTED",
  "CANCELLED",
] as const;
export type MvpTaskStatus = (typeof mvpTaskStatuses)[number];

export interface MvpAgent {
  id: string;
  name: string;
  role: string;
  mission: string;
  responsibilities: string[];
  authority: string[];
  prohibitedActions: string[];
  reportsTo: string;
  agentType: MvpAgentType;
  status: MvpAgentStatus;
  version: number;
  model: string;
  capabilityHash: string;
  budgetLimitLamports: number;
}

// MPP Payment Profiles are read-only console contracts.  The Python Control
// Plane remains the authority for creating, changing, stopping, and using a
// profile; these fields intentionally contain no credential or signing data.
export const paymentProfileNetworks = ["LOCAL", "SOLANA_DEVNET"] as const;
export type PaymentProfileNetwork = (typeof paymentProfileNetworks)[number];

export const paymentProfileStatuses = [
  "DRAFT",
  "ACTIVE",
  "DISABLED",
  "SUSPENDED",
  "EXPIRED",
  "REVOKED",
] as const;
export type PaymentProfileStatus = (typeof paymentProfileStatuses)[number];

export const paymentApprovalModes = ["AUTO_ALLOW", "OWNER_APPROVAL", "DENY"] as const;
export type PaymentApprovalMode = (typeof paymentApprovalModes)[number];

export const paymentProfileRiskLevels = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type PaymentProfileRiskLevel = (typeof paymentProfileRiskLevels)[number];

export const paymentProfileRotationStates = [
  "CURRENT",
  "PENDING",
  "RETIRED",
  "REVOKED",
] as const;
export type PaymentProfileRotationState = (typeof paymentProfileRotationStates)[number];

export interface MvpAgentPaymentProfile {
  profileId: string;
  agentId: string;
  version: number;
  walletId: string | null;
  publicKey: string | null;
  network: PaymentProfileNetwork;
  cluster: "LOCAL" | "DEVNET";
  serviceId: string;
  recipient: string;
  recipientKind: "SERVICE";
  tokenAllowlist: string[];
  mintAllowlist: string[];
  serviceAllowlist: string[];
  recipientAllowlist: string[];
  programAllowlist: string[];
  purposeAllowlist: ["SERVICE_PAYMENT"];
  riskLevel: PaymentProfileRiskLevel;
  approvalMode: PaymentApprovalMode;
  perPaymentLimitUnits: number;
  perTaskLimitUnits: number;
  dailyLimitUnits: number;
  autoApprovalLimitUnits: number;
  maxExpirySeconds: number;
  expiresAt: string;
  status: PaymentProfileStatus;
  rotationState: PaymentProfileRotationState;
  createdBy: string;
  ownerApprovalId: string | null;
  createdAt: string;
  updatedAt: string;
}

export const mppPolicyDecisions = ["allow", "require_owner_approval", "deny"] as const;
export type MppPolicyDecision = (typeof mppPolicyDecisions)[number];

export const mppReservationStatuses = ["RESERVED", "CONSUMED", "RELEASED", "CANCELLED"] as const;
export type MppReservationStatus = (typeof mppReservationStatuses)[number];

export const mppSignerAuthorizationStatuses = ["ISSUED", "CONSUMED", "REVOKED", "EXPIRED"] as const;
export type MppSignerAuthorizationStatus = (typeof mppSignerAuthorizationStatuses)[number];

// MPP policy records are read-only console contracts.  They do not expose a
// key, seed, signature, or wallet operation.
export interface MvpMppPolicyDecision {
  id: string;
  paymentId: string | null;
  idempotencyKey: string;
  taskId: string;
  runId: string;
  traceId: string;
  correlationId: string;
  agentId: string;
  profileId: string | null;
  profileVersion: number | null;
  decision: MppPolicyDecision;
  reason: string;
  policyVersion: string;
  approvalId: string | null;
  reservationId: string | null;
  createdAt: string;
}

export interface MvpMppBudgetReservation {
  reservationId: string;
  idempotencyKey: string;
  paymentId: string;
  agentId: string;
  taskId: string;
  profileId: string;
  profileVersion: number;
  amountUnits: number;
  dailyPeriod: string;
  status: MppReservationStatus;
  correlationId: string;
  createdAt: string;
  updatedAt: string;
}

export interface MvpMppSignerAuthorization {
  authorizationId: string;
  paymentId: string;
  policyDecisionId: string;
  approvalId: string | null;
  authorizationHash: string;
  issuedBy: string;
  issuedAt: string;
  expiresAt: string;
  status: MppSignerAuthorizationStatus;
}

export interface MvpTask {
  id: string;
  title: string;
  objective: string;
  background: string;
  priority: Priority;
  deadline: string;
  acceptanceCriteria: string[];
  rewardBudgetLamports: number;
  assignedExecutiveAgentId: string;
  riskLevel: RiskLevel;
  externalActionAllowed: boolean;
  ownerApprovalRequired: boolean;
  status: MvpTaskStatus;
  progress: number;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
}

export interface MvpSubTask {
  id: string;
  parentTaskId: string;
  title: string;
  description: string;
  assignedAgentId: string;
  status: MvpTaskStatus;
  progress: number;
  dependencies: string[];
  artifact: string | null;
  reviewResult: string | null;
  auditResult: string | null;
}

export interface MvpReview {
  taskId: string;
  reviewer: string;
  quality: number;
  completeness: number;
  correctness: number;
  requiredChanges: string[];
  comment: string;
  reviewedAt: string;
}

export interface MvpAudit {
  taskId: string;
  auditor: string;
  policyCompliance: boolean;
  securityRisk: RiskLevel;
  externalActionCheck: boolean;
  rewardManipulationCheck: boolean;
  authorityViolationCheck: boolean;
  result: "PASS" | "PASS_WITH_CONDITIONS" | "FAIL" | "OWNER_REVIEW_REQUIRED";
  comment: string;
  auditedAt: string;
}

export interface MvpEvaluation {
  taskId: string;
  quality: number;
  difficulty: number;
  contribution: number;
  timeliness: number;
  rework: number;
  strategicValue: number;
  ownerComment: string;
  evaluatedBy: string;
  evaluatedAt: string;
}

export type MvpRewardStatus = "Pending" | "Proposed" | "Approved" | "Paid" | "Reserved" | "Cancelled";
export interface MvpReward {
  id: string;
  taskId: string;
  agentId: string;
  rewardBudgetLamports: number;
  proposedRewardLamports: number;
  approvedRewardLamports: number | null;
  paidRewardLamports: number;
  reservedRewardLamports: number;
  cancelledRewardLamports: number;
  status: MvpRewardStatus;
  approvedBy: string | null;
  comment: string;
}

export type ApprovalDecision = "APPROVE" | "REJECT" | "REQUEST_CHANGES" | "HOLD";
export type ApprovalType =
  | "TASK_COMPLETION"
  | "REWARD"
  | "BOARD_PROPOSAL"
  | "EXTERNAL_ACTION"
  | "AGENT_CREATION"
  | "AGENT_AUTHORITY_CHANGE"
  | "POLICY_EXCEPTION";
export interface MvpApproval {
  id: string;
  approvalType: ApprovalType;
  targetId: string;
  requestedBy: string;
  ownerDecision: ApprovalDecision | null;
  comment: string;
  createdAt: string;
}

export interface MvpProposal {
  id: string;
  title: string;
  proposer: string;
  background: string;
  objective: string;
  requiredBudgetLamports: number;
  expectedReturn: string;
  expectedPeriod: string;
  risks: string[];
  alternatives: string[];
  recommendedOption: string;
  exitCriteria: string[];
  strategyReview: string | null;
  treasuryReview: string | null;
  auditReview: string | null;
  ownerDecision: ApprovalDecision | null;
  status: string;
}

export interface MvpExternalAction {
  id: string;
  taskId: string | null;
  requestedBy: string;
  recipient: string;
  channel: "EMAIL" | "DM" | "SNS" | "API_WRITE" | "CONTRACT" | "OTHER";
  purpose: string;
  content: string;
  allowedActionCount: number;
  expiresAt: string;
  ownerDecision: ApprovalDecision | null;
  status: string;
  executionCount: number;
  executionResult: string | null;
}

export interface MvpAuditLog {
  id: string;
  actor: string;
  actorType: string;
  action: string;
  targetType: string;
  targetId: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  policyResult: "ALLOW" | "DENY" | "OWNER_APPROVAL_REQUIRED" | "ALLOW_WITH_SCOPE";
  reason: string;
  timestamp: string;
  correlationId: string;
}
