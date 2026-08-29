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

export const ownerId = "owner-local";
export const lamportsPerSol = 1_000_000_000;

export const mvpAgents: MvpAgent[] = [
  {
    id: "agent-aria",
    name: "Aria",
    role: "STRATEGY",
    mission: "FY計画および長期的な組織戦略を構築する",
    responsibilities: ["Vision・FY Plan・Roadmap", "KPIとBoard Proposalの整理"],
    authority: ["Owner Taskの計画化", "Strategy Proposalの提出"],
    prohibitedActions: ["Final Decision", "Budget変更", "無許可外部Action"],
    reportsTo: ownerId,
    agentType: "EXECUTIVE",
    status: "ACTIVE",
    version: 1,
    model: "policy-bound",
    capabilityHash: "sha256:aria-phase-1",
    budgetLimitLamports: 0,
  },
  {
    id: "agent-mira",
    name: "Mira",
    role: "PRODUCT",
    mission: "ProductおよびContentの企画・価値設計を行う",
    responsibilities: ["User Value", "Product Requirements", "Content Planning"],
    authority: ["承認済みProduct Taskの分解", "Product Proposalの提出"],
    prohibitedActions: ["契約", "受注", "無許可公開"],
    reportsTo: ownerId,
    agentType: "EXECUTIVE",
    status: "ACTIVE",
    version: 1,
    model: "policy-bound",
    capabilityHash: "sha256:mira-phase-1",
    budgetLimitLamports: 0,
  },
  {
    id: "agent-theo",
    name: "Theo",
    role: "ENGINEERING",
    mission: "System Design、Development、Technical Reviewを統括する",
    responsibilities: ["Architecture", "Implementation", "Technical Quality"],
    authority: ["承認済みTaskの実装", "Sub Taskの作成・委任"],
    prohibitedActions: ["Production変更", "権限変更", "自己承認"],
    reportsTo: ownerId,
    agentType: "EXECUTIVE",
    status: "ACTIVE",
    version: 1,
    model: "policy-bound",
    capabilityHash: "sha256:theo-phase-1",
    budgetLimitLamports: 0,
  },
  {
    id: "agent-noah",
    name: "Noah",
    role: "TREASURY",
    mission: "Budget、Capital Allocation、Asset Managementの提案を行う",
    responsibilities: ["Budget Proposal", "ROI・Risk", "Treasury Reporting"],
    authority: ["Virtual Ledgerの照合", "資本配分案の提出"],
    prohibitedActions: ["Master Wallet移転", "Reward確定", "無許可投資"],
    reportsTo: ownerId,
    agentType: "EXECUTIVE",
    status: "ACTIVE",
    version: 1,
    model: "policy-bound",
    capabilityHash: "sha256:noah-phase-1",
    budgetLimitLamports: 0,
  },
  {
    id: "agent-iris",
    name: "Iris",
    role: "AUDITOR",
    mission: "Task、Reward、Policy、Riskの監査を行う",
    responsibilities: ["Policy Compliance", "Security Risk", "Evidence Audit"],
    authority: ["Findingの提出", "OwnerへのPause Recommendation"],
    prohibitedActions: ["自分のFindingの最終確定", "証跡の削除・改ざん"],
    reportsTo: ownerId,
    agentType: "AUDIT",
    status: "ACTIVE",
    version: 1,
    model: "policy-bound",
    capabilityHash: "sha256:iris-phase-1",
    budgetLimitLamports: 0,
  },
  {
    id: "agent-luca",
    name: "Luca",
    role: "OPERATIONS",
    mission: "Task Progress、Blocker、Owner Approval Queueを管理する",
    responsibilities: ["Task Board", "Approval Queue", "Incident Routing"],
    authority: ["承認済みTaskの運用", "安全手順に基づくPause"],
    prohibitedActions: ["Task発行", "Reward確定", "Pause解除"],
    reportsTo: ownerId,
    agentType: "EXECUTIVE",
    status: "ACTIVE",
    version: 1,
    model: "policy-bound",
    capabilityHash: "sha256:luca-phase-1",
    budgetLimitLamports: 0,
  },
  {
    id: "agent-astra",
    name: "Astra",
    role: "REVIEWER",
    mission: "成果物の品質、完全性、正確性を独立してレビューする",
    responsibilities: ["Quality Review", "Acceptance Criteria Check"],
    authority: ["Review Resultの提出"],
    prohibitedActions: ["Task実行と自己レビュー", "Reward確定"],
    reportsTo: "agent-iris",
    agentType: "SUB_AGENT",
    status: "ACTIVE",
    version: 1,
    model: "review",
    capabilityHash: "sha256:astra-phase-1",
    budgetLimitLamports: 0,
  },
];

export const mvpTasks: MvpTask[] = [
  {
    id: "T-001",
    title: "Owner-Directed MVP foundation",
    objective: "Owner TaskからReview・Audit・Reward確定までのMVPサイクルを動かす",
    background: "R-CAOの初期Control Planeを実装する",
    priority: "HIGH",
    deadline: "2026-09-05",
    acceptanceCriteria: ["Policy tests pass", "Audit evidence exists", "Owner final decision is recorded"],
    rewardBudgetLamports: 1_000_000_000,
    assignedExecutiveAgentId: "agent-theo",
    riskLevel: "MEDIUM",
    externalActionAllowed: false,
    ownerApprovalRequired: true,
    status: "OWNER_REVIEW",
    progress: 82,
    createdBy: ownerId,
    createdAt: "2026-08-27T09:00:00Z",
    updatedAt: "2026-08-27T09:00:00Z",
  },
  {
    id: "T-002",
    title: "Treasury reinvestment memo",
    objective: "運営継続性と再投資候補のROI・Riskを比較する",
    background: "Virtual Treasuryの次期Capital Allocationを検討する",
    priority: "MEDIUM",
    deadline: "2026-09-08",
    acceptanceCriteria: ["ROI and risk are documented"],
    rewardBudgetLamports: 300_000_000,
    assignedExecutiveAgentId: "agent-noah",
    riskLevel: "MEDIUM",
    externalActionAllowed: false,
    ownerApprovalRequired: true,
    status: "IN_PROGRESS",
    progress: 46,
    createdBy: ownerId,
    createdAt: "2026-08-27T09:00:00Z",
    updatedAt: "2026-08-27T09:00:00Z",
  },
  {
    id: "T-003",
    title: "Devnet evidence design",
    objective: "将来の証跡ハッシュ境界を定義する",
    background: "Phase 1では実Wallet操作を行わない",
    priority: "LOW",
    deadline: "2026-09-15",
    acceptanceCriteria: ["No production transfer path"],
    rewardBudgetLamports: 500_000_000,
    assignedExecutiveAgentId: "agent-theo",
    riskLevel: "HIGH",
    externalActionAllowed: false,
    ownerApprovalRequired: true,
    status: "DRAFT",
    progress: 0,
    createdBy: ownerId,
    createdAt: "2026-08-27T09:00:00Z",
    updatedAt: "2026-08-27T09:00:00Z",
  },
];

export const mvpSubTasks: MvpSubTask[] = [
  {
    id: "ST-001",
    parentTaskId: "T-001",
    title: "Control Plane domain boundary",
    description: "Task、Approval、Rewardのモデルと不変条件を定義する",
    assignedAgentId: "agent-theo",
    status: "COMPLETED",
    progress: 100,
    dependencies: [],
    artifact: "services/rcao/app/mvp.py",
    reviewResult: "PASS",
    auditResult: "PASS",
  },
  {
    id: "ST-002",
    parentTaskId: "T-001",
    title: "Independent review",
    description: "実行者と分離したReviewerが成果物を確認する",
    assignedAgentId: "agent-astra",
    status: "COMPLETED",
    progress: 100,
    dependencies: ["ST-001"],
    artifact: "evidence://task/T-001/review",
    reviewResult: "PASS",
    auditResult: "PASS",
  },
  {
    id: "ST-003",
    parentTaskId: "T-002",
    title: "ROI and risk comparison",
    description: "候補ごとの期待収益、流動性、撤退条件を整理する",
    assignedAgentId: "agent-noah",
    status: "IN_PROGRESS",
    progress: 45,
    dependencies: [],
    artifact: null,
    reviewResult: null,
    auditResult: null,
  },
];

export const mvpReviews: MvpReview[] = [
  {
    taskId: "T-001",
    reviewer: "agent-astra",
    quality: 92,
    completeness: 88,
    correctness: 94,
    requiredChanges: [],
    comment: "Acceptance criteria and policy evidence are present.",
    reviewedAt: "2026-08-27T10:30:00Z",
  },
];

export const mvpAudits: MvpAudit[] = [
  {
    taskId: "T-001",
    auditor: "agent-iris",
    policyCompliance: true,
    securityRisk: "LOW",
    externalActionCheck: true,
    rewardManipulationCheck: true,
    authorityViolationCheck: true,
    result: "PASS",
    comment: "No wallet, external write, or Agent-to-Agent transfer path is present.",
    auditedAt: "2026-08-27T11:00:00Z",
  },
];

export const mvpEvaluations: MvpEvaluation[] = [];

export const mvpRewards: MvpReward[] = [
  {
    id: "reward-001",
    taskId: "T-001",
    agentId: "agent-theo",
    rewardBudgetLamports: 1_000_000_000,
    proposedRewardLamports: 650_000_000,
    approvedRewardLamports: null,
    paidRewardLamports: 0,
    reservedRewardLamports: 0,
    cancelledRewardLamports: 0,
    status: "Proposed",
    approvedBy: null,
    comment: "参考値。OwnerのFinal Reward確定前であり自動支払いしない。",
  },
];

export const mvpApprovals: MvpApproval[] = [
  { id: "approval-task-001", approvalType: "TASK_COMPLETION", targetId: "T-001", requestedBy: "agent-theo", ownerDecision: null, comment: "", createdAt: "2026-08-27T11:01:00Z" },
  { id: "approval-reward-001", approvalType: "REWARD", targetId: "reward-001", requestedBy: "agent-theo", ownerDecision: null, comment: "", createdAt: "2026-08-27T11:01:00Z" },
  { id: "approval-proposal-001", approvalType: "BOARD_PROPOSAL", targetId: "proposal-001", requestedBy: "agent-iris", ownerDecision: null, comment: "", createdAt: "2026-08-27T11:01:00Z" },
  { id: "approval-external-001", approvalType: "EXTERNAL_ACTION", targetId: "external-001", requestedBy: "agent-noah", ownerDecision: null, comment: "", createdAt: "2026-08-27T11:01:00Z" },
];

export const mvpProposals: MvpProposal[] = [
  {
    id: "proposal-001",
    title: "Phase 1 evidence hardening",
    proposer: "agent-iris",
    background: "監査証跡を次のPhaseの基盤にする必要がある。",
    objective: "AuditとOwner Decisionの再現性を高める",
    requiredBudgetLamports: 200_000_000,
    expectedReturn: "Auditability and lower operational risk",
    expectedPeriod: "1 sprint",
    risks: ["実装遅延", "運用コスト増"],
    alternatives: ["現状維持", "段階導入"],
    recommendedOption: "段階導入",
    exitCriteria: ["CIで主要Policy testsが通過"],
    strategyReview: "PENDING",
    treasuryReview: "PENDING",
    auditReview: "PASS_WITH_CONDITIONS",
    ownerDecision: null,
    status: "SUBMITTED",
  },
];

export const mvpExternalActions: MvpExternalAction[] = [
  {
    id: "external-001",
    taskId: "T-002",
    requestedBy: "agent-noah",
    recipient: "approved-recipient@example.test",
    channel: "EMAIL",
    purpose: "Treasury memo source clarification",
    content: "Owner承認後に送信する確認文面（MVPでは送信しない）",
    allowedActionCount: 1,
    expiresAt: "2026-09-30T00:00:00Z",
    ownerDecision: null,
    status: "PENDING",
    executionCount: 0,
    executionResult: "MVPでは外部送信を実装しない",
  },
];

export const mvpAuditLogs: MvpAuditLog[] = [
  {
    id: "audit-alert-001",
    actor: "agent-iris",
    actorType: "AUDIT",
    action: "REWARD_APPROVAL_PENDING",
    targetType: "REWARD",
    targetId: "reward-001",
    before: {},
    after: { status: "Proposed" },
    policyResult: "OWNER_APPROVAL_REQUIRED",
    reason: "Reward proposal awaits explicit Owner decision; no automatic payment is allowed.",
    timestamp: "2026-08-27T11:02:00Z",
    correlationId: "corr-alert-001",
  },
  {
    id: "audit-task-001",
    actor: "owner-local",
    actorType: "OWNER",
    action: "CREATE_TASK",
    targetType: "TASK",
    targetId: "T-001",
    before: {},
    after: { status: "DRAFT", rewardBudget: "1.00 SOL" },
    policyResult: "ALLOW",
    reason: "Owner created a Task with a Reward Budget, not an automatic payment.",
    timestamp: "2026-08-27T09:00:00Z",
    correlationId: "corr-task-001",
  },
];

export function isBlockingAuditAlert(
  log: Pick<MvpAuditLog, "policyResult">,
): boolean {
  return log.policyResult === "DENY";
}

export function isOwnerApprovalPending(
  log: Pick<MvpAuditLog, "policyResult">,
): boolean {
  return log.policyResult === "OWNER_APPROVAL_REQUIRED";
}

export function formatSol(lamports: number): string {
  return `${(lamports / lamportsPerSol).toFixed(2)} SOL`;
}

export function agentName(agentId: string): string {
  return mvpAgents.find((agent) => agent.id === agentId)?.name ?? agentId;
}

export function taskTitle(taskId: string): string {
  return mvpTasks.find((task) => task.id === taskId)?.title ?? taskId;
}

export type { ApprovalDecision };
