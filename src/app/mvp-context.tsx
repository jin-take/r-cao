"use client";

import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  mvpAgents as seedAgents,
  mvpApprovals as seedApprovals,
  mvpAuditLogs as seedAuditLogs,
  mvpAudits as seedAudits,
  mvpEvaluations as seedEvaluations,
  mvpExternalActions as seedExternalActions,
  mvpProposals as seedProposals,
  mvpReviews as seedReviews,
  mvpRewards as seedRewards,
  mvpSubTasks as seedSubTasks,
  mvpTasks as seedTasks,
  ownerId,
  resolveRewardApproval,
} from "@/data/mvp";
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

type CreateTaskInput = Pick<MvpTask, "title" | "objective" | "deadline" | "rewardBudgetLamports" | "assignedExecutiveAgentId">;

interface MvpContextValue {
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
  createTask: (input: CreateTaskInput) => void;
  setTaskStatus: (taskId: string, status: MvpTask["status"], reason?: string) => void;
  evaluateTask: (taskId: string) => void;
  decideApproval: (approvalId: string, decision: ApprovalDecision, comment?: string) => void;
  approveReward: (rewardId: string, amount: number, comment?: string) => void;
}

const MvpContext = createContext<MvpContextValue | null>(null);

function clone<T>(value: T): T {
  return structuredClone(value);
}

export function MvpProvider({ children }: { children: ReactNode }) {
  const [agents] = useState(() => clone(seedAgents));
  const [tasks, setTasks] = useState(() => clone(seedTasks));
  const [subtasks] = useState(() => clone(seedSubTasks));
  const [reviews] = useState(() => clone(seedReviews));
  const [audits] = useState(() => clone(seedAudits));
  const [evaluations, setEvaluations] = useState(() => clone(seedEvaluations));
  const [rewards, setRewards] = useState(() => clone(seedRewards));
  const [approvals, setApprovals] = useState(() => clone(seedApprovals));
  const [proposals, setProposals] = useState(() => clone(seedProposals));
  const [externalActions, setExternalActions] = useState(() => clone(seedExternalActions));
  const [auditLogs, setAuditLogs] = useState(() => clone(seedAuditLogs));
  const sequence = useRef(100);

  const audit = (action: string, targetType: string, targetId: string, reason: string, policyResult: MvpAuditLog["policyResult"] = "ALLOW") => {
    sequence.current += 1;
    setAuditLogs((current) => [
      {
        id: `audit-${sequence.current}`,
        actor: ownerId,
        actorType: "OWNER",
        action,
        targetType,
        targetId,
        before: {},
        after: {},
        policyResult,
        reason,
        timestamp: new Date().toISOString(),
        correlationId: `corr-${sequence.current}`,
      },
      ...current,
    ]);
  };

  const createTask = (input: CreateTaskInput) => {
    sequence.current += 1;
    const id = `T-${String(sequence.current).padStart(3, "0")}`;
    const now = new Date().toISOString();
    const task: MvpTask = {
      id,
      title: input.title,
      objective: input.objective,
      background: "Created from Owner Console MVP",
      priority: "MEDIUM",
      deadline: input.deadline,
      acceptanceCriteria: ["成果物が提出されている", "ReviewとAuditを通過する"],
      rewardBudgetLamports: input.rewardBudgetLamports,
      assignedExecutiveAgentId: input.assignedExecutiveAgentId,
      riskLevel: "LOW",
      externalActionAllowed: false,
      ownerApprovalRequired: true,
      status: "DRAFT",
      progress: 0,
      createdBy: ownerId,
      createdAt: now,
      updatedAt: now,
    };
    setTasks((current) => [task, ...current]);
    setRewards((current) => [
      {
        id: `reward-${id.toLowerCase()}`,
        taskId: id,
        agentId: input.assignedExecutiveAgentId,
        rewardBudgetLamports: input.rewardBudgetLamports,
        proposedRewardLamports: 0,
        approvedRewardLamports: null,
        paidRewardLamports: 0,
        reservedRewardLamports: 0,
        cancelledRewardLamports: 0,
        status: "Pending",
        approvedBy: null,
        comment: "Reward Budget。自動支払いなし。",
      },
      ...current,
    ]);
    audit("CREATE_TASK", "TASK", id, "Owner created a draft Task with a Reward Budget");
  };

  const setTaskStatus = (taskId: string, status: MvpTask["status"], reason = "Owner Console action") => {
    setTasks((current) => current.map((task) => task.id === taskId ? { ...task, status, updatedAt: new Date().toISOString() } : task));
    audit("TRANSITION_TASK", "TASK", taskId, reason);
  };

  const evaluateTask = (taskId: string) => {
    const evaluation: MvpEvaluation = {
      taskId,
      quality: 88,
      difficulty: 3,
      contribution: 90,
      timeliness: 95,
      rework: 0,
      strategicValue: 80,
      ownerComment: "Owner evaluation recorded in the MVP console.",
      evaluatedBy: ownerId,
      evaluatedAt: new Date().toISOString(),
    };
    setEvaluations((current) => [...current.filter((item) => item.taskId !== taskId), evaluation]);
    setRewards((current) => current.map((reward) => reward.taskId === taskId && reward.status === "Pending" ? {
      ...reward,
      proposedRewardLamports: Math.floor(reward.rewardBudgetLamports * evaluation.quality / 100),
      status: "Proposed",
    } : reward));
    audit("OWNER_EVALUATE_TASK", "TASK", taskId, "Owner evaluation recorded; Reward remains unapproved");
  };

  const approveReward = (rewardId: string, amount: number, comment = "Owner approved virtual Reward") => {
    const reward = rewards.find((item) => item.id === rewardId);
    if (!reward || !evaluations.some((item) => item.taskId === reward.taskId) || !["Pending", "Proposed"].includes(reward.status)) return;
    setRewards((current) => current.map((reward) => reward.id === rewardId ? {
      ...reward,
      approvedRewardLamports: amount,
      approvedBy: ownerId,
      status: "Approved",
      comment,
    } : reward));
    setApprovals((current) => resolveRewardApproval(current, rewardId, comment));
    audit("APPROVE_REWARD", "REWARD", rewardId, comment);
  };

  const decideApproval = (approvalId: string, decision: ApprovalDecision, comment = "") => {
    const approval = approvals.find((item) => item.id === approvalId);
    if (!approval) return;
    if (approval.approvalType === "TASK_COMPLETION" && decision === "APPROVE" && !evaluations.some((item) => item.taskId === approval.targetId)) return;
    if (approval.approvalType === "REWARD" && decision === "APPROVE") {
      const reward = rewards.find((item) => item.id === approval.targetId);
      if (!reward || !evaluations.some((item) => item.taskId === reward.taskId)) return;
    }
    setApprovals((current) => current.map((item) => item.id === approvalId ? { ...item, ownerDecision: decision, comment } : item));
    if (approval.approvalType === "TASK_COMPLETION") {
      if (decision === "APPROVE" && evaluations.some((item) => item.taskId === approval.targetId)) setTaskStatus(approval.targetId, "COMPLETED", comment || "Owner approved Task completion");
      if (decision === "REQUEST_CHANGES") setTaskStatus(approval.targetId, "REWORK", comment || "Owner requested changes");
      if (decision === "REJECT") setTaskStatus(approval.targetId, "REJECTED", comment || "Owner rejected completion");
    }
    if (approval.approvalType === "REWARD" && decision === "APPROVE") {
      const reward = rewards.find((item) => item.id === approval.targetId);
      if (reward) approveReward(reward.id, reward.proposedRewardLamports, comment || "Owner approved proposed Reward");
    }
    if (approval.approvalType === "BOARD_PROPOSAL") setProposals((current) => current.map((item) => item.id === approval.targetId ? { ...item, ownerDecision: decision, status: decision } : item));
    if (approval.approvalType === "EXTERNAL_ACTION") setExternalActions((current) => current.map((item) => item.id === approval.targetId ? { ...item, ownerDecision: decision, status: decision === "APPROVE" ? "APPROVED" : "REJECTED" } : item));
    audit("DECIDE_APPROVAL", "APPROVAL_REQUEST", approvalId, comment || `Owner decision: ${decision}`);
  };

  const value = useMemo<MvpContextValue>(() => ({
    agents,
    tasks,
    subtasks,
    reviews,
    audits,
    evaluations,
    rewards,
    approvals,
    proposals,
    externalActions,
    auditLogs,
    createTask,
    setTaskStatus,
    evaluateTask,
    decideApproval,
    approveReward,
  }), [agents, tasks, subtasks, reviews, audits, evaluations, rewards, approvals, proposals, externalActions, auditLogs]);

  return <MvpContext.Provider value={value}>{children}</MvpContext.Provider>;
}

export function useMvp(): MvpContextValue {
  const context = useContext(MvpContext);
  if (!context) throw new Error("useMvp must be used inside MvpProvider");
  return context;
}
