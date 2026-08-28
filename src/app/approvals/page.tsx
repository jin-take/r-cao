"use client";

import Link from "next/link";
import { useMvp } from "@/app/mvp-context";
import { agentName, formatSol } from "@/data/mvp";

const labels: Record<string, string> = {
  TASK_COMPLETION: "Task Completion Approval",
  REWARD: "Reward Approval",
  BOARD_PROPOSAL: "Board Proposal Approval",
  EXTERNAL_ACTION: "External Action Approval",
  AGENT_CREATION: "Agent Creation Approval",
  AGENT_AUTHORITY_CHANGE: "Agent Authority Change",
  POLICY_EXCEPTION: "Policy Exception Approval",
};

export default function ApprovalsPage() {
  const { approvals, tasks, rewards, proposals, externalActions, evaluations, decideApproval } = useMvp();
  const pending = approvals.filter((item) => !item.ownerDecision);
  const targetTitle = (approval: (typeof approvals)[number]) => {
    if (approval.approvalType === "TASK_COMPLETION") return tasks.find((task) => task.id === approval.targetId)?.title ?? approval.targetId;
    if (approval.approvalType === "REWARD") {
      const reward = rewards.find((item) => item.id === approval.targetId);
      return reward ? `${agentName(reward.agentId)} · ${formatSol(reward.proposedRewardLamports)}` : approval.targetId;
    }
    if (approval.approvalType === "BOARD_PROPOSAL") return proposals.find((item) => item.id === approval.targetId)?.title ?? approval.targetId;
    if (approval.approvalType === "EXTERNAL_ACTION") return externalActions.find((item) => item.id === approval.targetId)?.recipient ?? approval.targetId;
    return approval.targetId;
  };

  return <section className="shell">
    <div className="eyebrow">OWNER CONSOLE / APPROVAL CENTER</div>
    <div className="hero"><div><h1>Approval Center</h1><p>重要な判断を一か所に集約します。Approve、Reject、Request Changes、HoldはすべてOwnerのDecision Recordとして記録されます。</p></div><span className="mode-inline">{pending.length} PENDING</span></div>
    <div className="approval-rules"><span>Owner only</span><span>Audit first</span><span>No automatic Reward</span><span>No external send</span></div>
    <div className="approval-list">{pending.map((approval) => {
      const task = approval.approvalType === "TASK_COMPLETION" ? tasks.find((item) => item.id === approval.targetId) : undefined;
      const canApproveTask = !task || evaluations.some((item) => item.taskId === task.id);
      return <article className="approval-card" key={approval.id}><div className="approval-icon">{approval.approvalType === "REWARD" ? "R" : approval.approvalType === "EXTERNAL_ACTION" ? "X" : approval.approvalType === "BOARD_PROPOSAL" ? "P" : "T"}</div><div className="approval-main"><div className="approval-top"><span className="tag">{labels[approval.approvalType] ?? approval.approvalType}</span><time>{approval.createdAt}</time></div><h2>{targetTitle(approval)}</h2><p>Requested by <b>{agentName(approval.requestedBy)}</b> · target <code>{approval.targetId}</code></p>{approval.approvalType === "TASK_COMPLETION" && !canApproveTask && <Link className="text-link" href={`/tasks/${approval.targetId}`}>先にOwner Evaluationを記録する →</Link>}<div className="approval-actions"><button className="approve-button" type="button" disabled={!canApproveTask} onClick={() => decideApproval(approval.id, "APPROVE", "Owner approved")}>Approve</button><button className="secondary-button" type="button" onClick={() => decideApproval(approval.id, "REQUEST_CHANGES", "Owner requested additional evidence")}>Request Changes</button><button className="quiet-button" type="button" onClick={() => decideApproval(approval.id, "HOLD", "Owner placed this decision on hold")}>Hold</button><button className="danger-button" type="button" onClick={() => decideApproval(approval.id, "REJECT", "Owner rejected")}>Reject</button></div></div></article>;
    })}</div>
    {pending.length === 0 && <div className="empty-state"><strong>Approval queue is clear.</strong><span>No pending Owner decisions.</span></div>}
    <p className="notice">Agentからの提案はApproval Requestに留まり、Ownerの明示決定なしにTask、Reward、Proposal、External Actionを確定しません。</p>
  </section>;
}
