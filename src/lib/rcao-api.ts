import type {
  ApprovalDecision,
  MvpAgent,
  MvpApproval,
  MvpAudit,
  MvpAuditLog,
  MvpEvaluation,
  MvpExternalAction,
  MvpProposal,
  MvpReview,
  MvpReward,
  MvpSubTask,
  MvpTask,
} from "@/domain/model";
import type { OperationRecord, OperationScope } from "@/data/operations";

export interface ConsoleSession {
  baseUrl: string;
  token: string;
}

export interface OwnerActor {
  actorId: string;
  name: string;
  role: string;
  actorType: string;
  phase: string;
  expiresAt: number;
}

export interface ConsoleDashboard {
  fyPlan: {
    name: string;
    phase: string;
    status: string;
  };
  activeTasks: number;
  ownerApprovalPending: number;
  boardProposals: number;
  rewardApprovalPending: number;
  externalActionApprovalPending: number;
  auditAlerts: number;
  budgetStatus: {
    annualBudgetLamports: number;
    reservedRewardBudgetLamports: number;
    availableLamports: number;
    mode: string;
  };
}

export interface ConsoleSnapshot {
  actor: OwnerActor;
  dashboard: ConsoleDashboard;
  agents: MvpAgent[];
  tasks: MvpTask[];
  subtasks: MvpSubTask[];
  reviews: MvpReview[];
  audits: MvpAudit[];
  evaluations: MvpEvaluation[];
  rewards: MvpReward[];
  approvals: MvpApproval[];
  proposals: MvpProposal[];
  externalActions: MvpExternalAction[];
  auditLogs: MvpAuditLog[];
  operations: OperationRecord[];
}

export type ApiErrorKind =
  | "NETWORK"
  | "AUTHENTICATION"
  | "PERMISSION"
  | "CONFLICT"
  | "BAD_REQUEST"
  | "UNAVAILABLE"
  | "UNKNOWN";

export class RcaoApiError extends Error {
  readonly status: number;
  readonly kind: ApiErrorKind;

  constructor(message: string, status: number, kind: ApiErrorKind) {
    super(message);
    this.name = "RcaoApiError";
    this.status = status;
    this.kind = kind;
  }
}

export const defaultApiBaseUrl = (): string =>
  process.env.NEXT_PUBLIC_RCAO_API_URL?.trim() || "http://localhost:8000";

function trimBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

export function readStoredSession(): ConsoleSession | null {
  if (typeof window === "undefined") return null;
  try {
    const token = window.sessionStorage.getItem("rcao.owner.token");
    const baseUrl = window.sessionStorage.getItem("rcao.owner.api") || defaultApiBaseUrl();
    if (!token) return null;
    return { baseUrl: trimBaseUrl(baseUrl), token };
  } catch {
    return null;
  }
}

export function writeStoredSession(session: ConsoleSession): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem("rcao.owner.token", session.token);
  window.sessionStorage.setItem("rcao.owner.api", trimBaseUrl(session.baseUrl));
}

export function clearStoredSession(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem("rcao.owner.token");
  window.sessionStorage.removeItem("rcao.owner.api");
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown, fallback = ""): string {
  return value === null || value === undefined ? fallback : String(value);
}

function integer(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function boolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function strings(value: unknown): string[] {
  return asArray(value).map((item) => text(item)).filter(Boolean);
}

function errorKind(status: number): ApiErrorKind {
  if (status === 401) return "AUTHENTICATION";
  if (status === 403) return "PERMISSION";
  if (status === 409) return "CONFLICT";
  if (status === 400 || status === 422) return "BAD_REQUEST";
  if (status === 502 || status === 503 || status === 504) return "UNAVAILABLE";
  return "UNKNOWN";
}

async function requestJson<T>(
  session: ConsoleSession,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (!headers.has("Content-Type") && init.body) headers.set("Content-Type", "application/json");
  if (session.token) headers.set("Authorization", `Bearer ${session.token}`);

  let response: Response;
  try {
    response = await fetch(`${trimBaseUrl(session.baseUrl)}${path}`, {
      ...init,
      headers,
      cache: "no-store",
    });
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : "Control Plane is unreachable";
    throw new RcaoApiError(
      `Control Planeへ接続できません。${message}`,
      0,
      "NETWORK",
    );
  }

  const raw = await response.text();
  let payload: unknown = null;
  if (raw) {
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = raw;
    }
  }
  if (!response.ok) {
    const body = asRecord(payload);
    const detail = typeof body.detail === "string" ? body.detail : "Control Plane request failed";
    throw new RcaoApiError(`${detail} (HTTP ${response.status})`, response.status, errorKind(response.status));
  }
  return payload as T;
}

function authActor(value: unknown): OwnerActor {
  const raw = asRecord(value);
  return {
    actorId: text(raw.actor_id),
    name: text(raw.name),
    role: text(raw.role),
    actorType: text(raw.actor_type),
    phase: text(raw.phase),
    expiresAt: integer(raw.expires_at),
  };
}

function mapAgent(value: unknown): MvpAgent {
  const raw = asRecord(value);
  return {
    id: text(raw.id),
    name: text(raw.name),
    role: text(raw.role),
    mission: text(raw.mission),
    responsibilities: strings(raw.responsibilities),
    authority: strings(raw.authority),
    prohibitedActions: strings(raw.prohibited_actions),
    reportsTo: text(raw.reports_to),
    agentType: text(raw.agent_type) as MvpAgent["agentType"],
    status: text(raw.status) as MvpAgent["status"],
    version: integer(raw.version, 1),
    model: text(raw.model),
    capabilityHash: text(raw.capability_hash),
    budgetLimitLamports: integer(raw.budget_limit_lamports),
  };
}

function mapTask(value: unknown): MvpTask {
  const raw = asRecord(value);
  return {
    id: text(raw.id),
    title: text(raw.title),
    objective: text(raw.objective),
    background: text(raw.background),
    priority: text(raw.priority) as MvpTask["priority"],
    deadline: text(raw.deadline),
    acceptanceCriteria: strings(raw.acceptance_criteria),
    rewardBudgetLamports: integer(raw.reward_budget_lamports),
    assignedExecutiveAgentId: text(raw.assigned_executive_agent_id),
    riskLevel: text(raw.risk_level) as MvpTask["riskLevel"],
    externalActionAllowed: boolean(raw.external_action_allowed),
    ownerApprovalRequired: boolean(raw.owner_approval_required, true),
    status: text(raw.status) as MvpTask["status"],
    progress: integer(raw.progress),
    createdBy: text(raw.created_by),
    createdAt: text(raw.created_at),
    updatedAt: text(raw.updated_at),
  };
}

function mapSubtask(value: unknown): MvpSubTask {
  const raw = asRecord(value);
  return {
    id: text(raw.id),
    parentTaskId: text(raw.parent_task_id),
    title: text(raw.title),
    description: text(raw.description),
    assignedAgentId: text(raw.assigned_agent_id),
    status: text(raw.status) as MvpSubTask["status"],
    progress: integer(raw.progress),
    dependencies: strings(raw.dependencies),
    artifact: raw.artifact === null || raw.artifact === undefined ? null : text(raw.artifact),
    reviewResult: raw.review_result === null || raw.review_result === undefined ? null : text(raw.review_result),
    auditResult: raw.audit_result === null || raw.audit_result === undefined ? null : text(raw.audit_result),
  };
}

function mapReview(value: unknown): MvpReview {
  const raw = asRecord(value);
  return {
    taskId: text(raw.task_id),
    reviewer: text(raw.reviewer),
    quality: integer(raw.quality),
    completeness: integer(raw.completeness),
    correctness: integer(raw.correctness),
    requiredChanges: strings(raw.required_changes),
    comment: text(raw.comment),
    reviewedAt: text(raw.reviewed_at),
  };
}

function mapAudit(value: unknown): MvpAudit {
  const raw = asRecord(value);
  return {
    taskId: text(raw.task_id),
    auditor: text(raw.auditor),
    policyCompliance: boolean(raw.policy_compliance),
    securityRisk: text(raw.security_risk) as MvpAudit["securityRisk"],
    externalActionCheck: boolean(raw.external_action_check),
    rewardManipulationCheck: boolean(raw.reward_manipulation_check),
    authorityViolationCheck: boolean(raw.authority_violation_check),
    result: text(raw.result) as MvpAudit["result"],
    comment: text(raw.comment),
    auditedAt: text(raw.audited_at),
  };
}

function mapEvaluation(value: unknown): MvpEvaluation {
  const raw = asRecord(value);
  return {
    taskId: text(raw.task_id),
    quality: integer(raw.quality),
    difficulty: integer(raw.difficulty, 1),
    contribution: integer(raw.contribution),
    timeliness: integer(raw.timeliness),
    rework: integer(raw.rework),
    strategicValue: integer(raw.strategic_value),
    ownerComment: text(raw.owner_comment),
    evaluatedBy: text(raw.evaluated_by),
    evaluatedAt: text(raw.evaluated_at),
  };
}

function mapReward(value: unknown): MvpReward {
  const raw = asRecord(value);
  return {
    id: text(raw.id),
    taskId: text(raw.task_id),
    agentId: text(raw.agent_id),
    rewardBudgetLamports: integer(raw.reward_budget_lamports),
    proposedRewardLamports: integer(raw.proposed_reward_lamports),
    approvedRewardLamports: raw.approved_reward_lamports === null || raw.approved_reward_lamports === undefined
      ? null
      : integer(raw.approved_reward_lamports),
    paidRewardLamports: integer(raw.paid_reward_lamports),
    reservedRewardLamports: integer(raw.reserved_reward_lamports),
    cancelledRewardLamports: integer(raw.cancelled_reward_lamports),
    status: text(raw.status) as MvpReward["status"],
    approvedBy: raw.approved_by === null || raw.approved_by === undefined ? null : text(raw.approved_by),
    comment: text(raw.comment),
  };
}

function mapApproval(value: unknown): MvpApproval {
  const raw = asRecord(value);
  return {
    id: text(raw.id),
    approvalType: text(raw.approval_type) as MvpApproval["approvalType"],
    targetId: text(raw.target_id),
    requestedBy: text(raw.requested_by),
    ownerDecision: raw.owner_decision === null || raw.owner_decision === undefined
      ? null
      : text(raw.owner_decision) as ApprovalDecision,
    comment: text(raw.comment),
    createdAt: text(raw.created_at),
  };
}

function mapProposal(value: unknown): MvpProposal {
  const raw = asRecord(value);
  return {
    id: text(raw.id),
    title: text(raw.title),
    proposer: text(raw.proposer),
    background: text(raw.background),
    objective: text(raw.objective),
    requiredBudgetLamports: integer(raw.required_budget_lamports),
    expectedReturn: text(raw.expected_return),
    expectedPeriod: text(raw.expected_period),
    risks: strings(raw.risks),
    alternatives: strings(raw.alternatives),
    recommendedOption: text(raw.recommended_option),
    exitCriteria: strings(raw.exit_criteria),
    strategyReview: raw.strategy_review === null || raw.strategy_review === undefined ? null : text(raw.strategy_review),
    treasuryReview: raw.treasury_review === null || raw.treasury_review === undefined ? null : text(raw.treasury_review),
    auditReview: raw.audit_review === null || raw.audit_review === undefined ? null : text(raw.audit_review),
    ownerDecision: raw.owner_decision === null || raw.owner_decision === undefined
      ? null
      : text(raw.owner_decision) as ApprovalDecision,
    status: text(raw.status),
  };
}

function mapExternalAction(value: unknown): MvpExternalAction {
  const raw = asRecord(value);
  return {
    id: text(raw.id),
    taskId: raw.task_id === null || raw.task_id === undefined ? null : text(raw.task_id),
    requestedBy: text(raw.requested_by),
    recipient: text(raw.recipient),
    channel: text(raw.channel) as MvpExternalAction["channel"],
    purpose: text(raw.purpose),
    content: text(raw.content),
    allowedActionCount: integer(raw.allowed_action_count),
    expiresAt: text(raw.expires_at),
    ownerDecision: raw.owner_decision === null || raw.owner_decision === undefined
      ? null
      : text(raw.owner_decision) as ApprovalDecision,
    status: text(raw.status),
    executionCount: integer(raw.execution_count),
    executionResult: raw.execution_result === null || raw.execution_result === undefined ? null : text(raw.execution_result),
  };
}

function mapAuditLog(value: unknown): MvpAuditLog {
  const raw = asRecord(value);
  return {
    id: text(raw.id),
    actor: text(raw.actor),
    actorType: text(raw.actor_type),
    action: text(raw.action),
    targetType: text(raw.target_type),
    targetId: text(raw.target_id),
    before: asRecord(raw.before),
    after: asRecord(raw.after),
    policyResult: text(raw.policy_result) as MvpAuditLog["policyResult"],
    reason: text(raw.reason),
    timestamp: text(raw.timestamp),
    correlationId: text(raw.correlation_id),
  };
}

function mapOperation(value: unknown): OperationRecord {
  const raw = asRecord(value);
  return {
    recordId: text(raw.record_id),
    scope: text(raw.scope) as OperationScope,
    title: text(raw.title),
    body: text(raw.body),
    taskId: raw.task_id === null || raw.task_id === undefined ? null : text(raw.task_id),
    runId: raw.run_id === null || raw.run_id === undefined ? null : text(raw.run_id),
    agentId: raw.agent_id === null || raw.agent_id === undefined ? null : text(raw.agent_id),
    status: raw.status === null || raw.status === undefined ? null : text(raw.status),
    createdAt: text(raw.created_at),
    refs: strings(raw.refs),
  };
}

function mapDashboard(value: unknown): ConsoleDashboard {
  const raw = asRecord(value);
  const plan = asRecord(raw.fy_plan);
  const budget = asRecord(raw.budget_status);
  return {
    fyPlan: {
      name: text(plan.name),
      phase: text(plan.phase),
      status: text(plan.status),
    },
    activeTasks: integer(raw.active_tasks),
    ownerApprovalPending: integer(raw.owner_approval_pending),
    boardProposals: integer(raw.board_proposals),
    rewardApprovalPending: integer(raw.reward_approval_pending),
    externalActionApprovalPending: integer(raw.external_action_approval_pending),
    auditAlerts: integer(raw.audit_alerts),
    budgetStatus: {
      annualBudgetLamports: integer(budget.annual_budget_lamports),
      reservedRewardBudgetLamports: integer(budget.reserved_reward_budget_lamports),
      availableLamports: integer(budget.available_lamports),
      mode: text(budget.mode),
    },
  };
}

interface RawTaskDetail {
  task: unknown;
  subtasks: unknown[];
  reviews: unknown[];
  audits: unknown[];
  owner_evaluation: unknown | null;
  rewards: unknown[];
  activity: unknown[];
}

function mapTaskDetail(value: unknown): RawTaskDetail {
  const raw = asRecord(value);
  return {
    task: raw.task,
    subtasks: asArray(raw.subtasks),
    reviews: asArray(raw.reviews),
    audits: asArray(raw.audits),
    owner_evaluation: raw.owner_evaluation ?? null,
    rewards: asArray(raw.rewards),
    activity: asArray(raw.activity),
  };
}

function idempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `console-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function post<T>(session: ConsoleSession, path: string, body: unknown): Promise<T> {
  return requestJson<T>(session, path, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey() },
    body: JSON.stringify(body),
  });
}

export async function loadConsoleSnapshot(session: ConsoleSession): Promise<ConsoleSnapshot> {
  const actor = authActor(await requestJson<unknown>(session, "/api/v1/auth/me"));
  if (actor.actorType !== "OWNER" || actor.role !== "OWNER") {
    throw new RcaoApiError("Owner Identityが必要です。Agent用TokenはConsoleに使用できません。", 403, "PERMISSION");
  }

  const [dashboard, agents, tasks, approvals, rewards, proposals, externalActions, auditLogs, operations] = await Promise.all([
    requestJson<unknown>(session, "/api/v1/dashboard"),
    requestJson<unknown[]>(session, "/api/v1/agents?include_sub_agents=true"),
    requestJson<unknown[]>(session, "/api/v1/tasks"),
    requestJson<unknown[]>(session, "/api/v1/approvals"),
    requestJson<unknown[]>(session, "/api/v1/rewards"),
    requestJson<unknown[]>(session, "/api/v1/proposals"),
    requestJson<unknown[]>(session, "/api/v1/external-actions"),
    requestJson<unknown[]>(session, "/api/v1/audit"),
    requestJson<unknown>(session, "/api/v1/operations/search?q=&scope=ALL&limit=50"),
  ]);

  const taskDetails = await Promise.all(
    asArray(tasks).map((task) => requestJson<unknown>(session, `/api/v1/tasks/${encodeURIComponent(text(asRecord(task).id))}`)),
  );
  const details = taskDetails.map(mapTaskDetail);

  return {
    actor,
    dashboard: mapDashboard(dashboard),
    agents: asArray(agents).map(mapAgent),
    tasks: asArray(tasks).map(mapTask),
    subtasks: details.flatMap((detail) => detail.subtasks.map(mapSubtask)),
    reviews: details.flatMap((detail) => detail.reviews.map(mapReview)),
    audits: details.flatMap((detail) => detail.audits.map(mapAudit)),
    evaluations: details.flatMap((detail) => detail.owner_evaluation === null ? [] : [mapEvaluation(detail.owner_evaluation)]),
    rewards: asArray(rewards).map(mapReward),
    approvals: asArray(approvals).map(mapApproval),
    proposals: asArray(proposals).map(mapProposal),
    externalActions: asArray(externalActions).map(mapExternalAction),
    auditLogs: asArray(auditLogs).map(mapAuditLog),
    operations: asArray(asRecord(operations).hits).map(mapOperation),
  };
}

export const rcaoApi = {
  createTask(session: ConsoleSession, input: {
    title: string;
    objective: string;
    deadline: string;
    rewardBudgetLamports: number;
    assignedExecutiveAgentId: string;
  }): Promise<MvpTask> {
    return post<unknown>(session, "/api/v1/tasks", {
      title: input.title,
      objective: input.objective,
      background: "Created from Owner Console",
      priority: "MEDIUM",
      deadline: `${input.deadline}T00:00:00Z`,
      acceptance_criteria: ["成果物が提出されている", "ReviewとAuditを通過する"],
      reward_budget_lamports: input.rewardBudgetLamports,
      assigned_executive_agent_id: input.assignedExecutiveAgentId,
      risk_level: "LOW",
      external_action_allowed: false,
      owner_approval_required: true,
    }).then(mapTask);
  },

  setTaskStatus(session: ConsoleSession, taskId: string, status: MvpTask["status"], reason: string): Promise<MvpTask> {
    return post<unknown>(session, `/api/v1/tasks/${encodeURIComponent(taskId)}/status`, { status, reason }).then(mapTask);
  },

  evaluateTask(session: ConsoleSession, taskId: string): Promise<MvpEvaluation> {
    return post<unknown>(session, `/api/v1/tasks/${encodeURIComponent(taskId)}/evaluation`, {
      quality: 88,
      difficulty: 3,
      contribution: 90,
      timeliness: 95,
      rework: 0,
      strategic_value: 80,
      owner_comment: "Owner evaluation recorded from the authenticated Owner Console.",
    }).then(mapEvaluation);
  },

  decideApproval(session: ConsoleSession, approvalId: string, decision: ApprovalDecision, comment: string): Promise<MvpApproval> {
    return post<unknown>(session, `/api/v1/approvals/${encodeURIComponent(approvalId)}/decision`, { decision, comment }).then(mapApproval);
  },

  approveReward(session: ConsoleSession, rewardId: string, amount: number, comment: string): Promise<MvpReward> {
    return post<unknown>(session, `/api/v1/rewards/${encodeURIComponent(rewardId)}/approve`, {
      approved_reward_lamports: amount,
      reason: comment,
    }).then(mapReward);
  },

  setAgentStatus(session: ConsoleSession, agentId: string, status: MvpAgent["status"], reason: string): Promise<MvpAgent> {
    return post<unknown>(session, `/api/v1/agents/${encodeURIComponent(agentId)}/status`, { status, reason }).then(mapAgent);
  },

  async searchOperations(session: ConsoleSession, query: string, scope: OperationScope): Promise<OperationRecord[]> {
    const response = await requestJson<unknown>(
      session,
      `/api/v1/operations/search?q=${encodeURIComponent(query)}&scope=${encodeURIComponent(scope)}&limit=50`,
    );
    return asArray(asRecord(response).hits).map(mapOperation);
  },
};
