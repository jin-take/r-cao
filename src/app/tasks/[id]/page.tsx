"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMvp } from "@/app/mvp-context";
import { agentName, formatSol } from "@/data/mvp";

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { tasks, subtasks, reviews, audits, evaluations, rewards, approvals, auditLogs, evaluateTask, decideApproval, approveReward } = useMvp();
  const task = tasks.find((item) => item.id === id);

  if (!task) return <section className="shell"><div className="eyebrow">TASK NOT FOUND</div><h1>Unknown Task</h1><Link className="text-link" href="/tasks">Back to Task Board</Link></section>;

  const taskSubtasks = subtasks.filter((item) => item.parentTaskId === task.id);
  const taskReview = reviews.filter((item) => item.taskId === task.id);
  const taskAudits = audits.filter((item) => item.taskId === task.id);
  const taskEvaluation = evaluations.find((item) => item.taskId === task.id);
  const taskRewards = rewards.filter((item) => item.taskId === task.id);
  const completionApproval = approvals.find((item) => item.approvalType === "TASK_COMPLETION" && item.targetId === task.id && !item.ownerDecision);
  const taskActivity = auditLogs.filter((item) => item.targetId === task.id || taskRewards.some((reward) => reward.id === item.targetId));

  return (
    <section className="shell">
      <div className="eyebrow"><Link href="/tasks">TASK BOARD</Link> / {task.id}</div>
      <div className="detail-hero">
        <div><div className="detail-kicker">{task.priority} PRIORITY · {task.riskLevel} RISK</div><h1>{task.title}</h1><p>{task.objective}</p></div>
        <div className="detail-actions">
          <span className={`status status-${task.status.toLowerCase()}`}>{task.status}</span>
          {task.status === "OWNER_REVIEW" && !taskEvaluation && <button className="primary" type="button" onClick={() => evaluateTask(task.id)}>Record Owner Evaluation</button>}
          {task.status === "OWNER_REVIEW" && taskEvaluation && completionApproval && <button className="primary" type="button" onClick={() => decideApproval(completionApproval.id, "APPROVE", "Owner approved after evaluation")}>Approve Completion</button>}
        </div>
      </div>

      <div className="detail-grid">
        <article className="panel detail-overview">
          <div className="panelHead"><h2>Task overview</h2><span>Owner record</span></div>
          <dl className="facts">
            <div><dt>Objective</dt><dd>{task.objective}</dd></div>
            <div><dt>Assigned Executive</dt><dd>{agentName(task.assignedExecutiveAgentId)}</dd></div>
            <div><dt>Deadline</dt><dd>{task.deadline}</dd></div>
            <div><dt>Reward Budget</dt><dd className="money">{formatSol(task.rewardBudgetLamports)}</dd></div>
            <div><dt>External Action</dt><dd>{task.externalActionAllowed ? "Allowed with Owner scope" : "Not allowed"}</dd></div>
            <div><dt>Created By</dt><dd>{task.createdBy}</dd></div>
          </dl>
          <h3>Acceptance criteria</h3>
          <ul className="check-list">{task.acceptanceCriteria.map((criterion) => <li key={criterion}><span>✓</span>{criterion}</li>)}</ul>
        </article>

        <article className="panel progress-panel">
          <div className="panelHead"><h2>Progress</h2><strong>{task.progress}%</strong></div>
          <div className="big-progress"><span style={{ width: `${task.progress}%` }} /></div>
          <div className="progress-meta"><span>Workflow status</span><b>{task.status.replaceAll("_", " ")}</b></div>
          <div className="workflow-mini"><span className="done">Task</span><span className={task.status !== "DRAFT" ? "done" : ""}>Plan</span><span className={["IN_PROGRESS", "REVIEW", "AUDIT", "OWNER_REVIEW", "COMPLETED"].includes(task.status) ? "done" : ""}>Execute</span><span className={["AUDIT", "OWNER_REVIEW", "COMPLETED"].includes(task.status) ? "done" : ""}>Audit</span><span className={task.status === "COMPLETED" ? "done" : ""}>Owner</span></div>
        </article>

        <article className="panel span-2">
          <div className="panelHead"><h2>Sub Tasks</h2><span>{taskSubtasks.length} items · Executive managed</span></div>
          <div className="subtask-list">{taskSubtasks.map((item) => <div className="subtask" key={item.id}><div><small>{item.id} · {agentName(item.assignedAgentId)}</small><strong>{item.title}</strong><p>{item.description}</p></div><div className="subtask-side"><span className="status status-small">{item.status}</span><span>{item.progress}%</span><small>{item.artifact ?? "No artifact"}</small></div></div>)}</div>
        </article>

        <article className="panel">
          <div className="panelHead"><h2>Review</h2><span>independent</span></div>
          {taskReview.length === 0 ? <p className="muted">Review not submitted.</p> : taskReview.map((review) => <div className="record" key={`${review.taskId}-${review.reviewer}`}><div className="record-top"><b>{agentName(review.reviewer)}</b><span>PASS</span></div><div className="score-row"><span>Quality <b>{review.quality}</b></span><span>Completeness <b>{review.completeness}</b></span><span>Correctness <b>{review.correctness}</b></span></div><p>{review.comment}</p></div>)}
        </article>
        <article className="panel">
          <div className="panelHead"><h2>Audit</h2><span>third line</span></div>
          {taskAudits.length === 0 ? <p className="muted">Audit not completed.</p> : taskAudits.map((audit) => <div className="record" key={`${audit.taskId}-${audit.auditor}`}><div className="record-top"><b>{agentName(audit.auditor)}</b><span className={audit.result === "PASS" ? "good" : "warning"}>{audit.result}</span></div><p>{audit.comment}</p><div className="audit-flags"><span>{audit.policyCompliance ? "Policy ✓" : "Policy !"}</span><span>{audit.rewardManipulationCheck ? "Reward ✓" : "Reward !"}</span><span>{audit.authorityViolationCheck ? "Authority ✓" : "Authority !"}</span></div></div>)}
        </article>

        <article className="panel">
          <div className="panelHead"><h2>Owner Evaluation</h2><span>explicit</span></div>
          {!taskEvaluation ? <p className="muted">Owner evaluation pending. Reward is not final.</p> : <div className="evaluation-grid"><b>{taskEvaluation.quality}<small>Quality</small></b><b>{taskEvaluation.contribution}<small>Contribution</small></b><b>{taskEvaluation.timeliness}<small>Timeliness</small></b><p>{taskEvaluation.ownerComment}</p></div>}
        </article>
        <article className="panel">
          <div className="panelHead"><h2>Reward Allocation</h2><span>virtual ledger</span></div>
          {taskRewards.map((reward) => <div className="reward-record" key={reward.id}><div><small>{agentName(reward.agentId)}</small><strong>{formatSol(reward.approvedRewardLamports ?? reward.proposedRewardLamports)}</strong><span>Budget {formatSol(reward.rewardBudgetLamports)}</span></div><span className={`status status-${reward.status.toLowerCase()}`}>{reward.status}</span>{reward.status === "Proposed" && <button className="quiet-button" type="button" onClick={() => approveReward(reward.id, reward.proposedRewardLamports)}>Approve proposed Reward</button>}</div>)}
          <p className="mini-warning">Budget is a reference ceiling. No automatic payment and no wallet operation.</p>
        </article>

        <article className="panel span-2">
          <div className="panelHead"><h2>Activity Log</h2><span>append-only audit</span></div>
          <div className="activity-list">{taskActivity.length === 0 ? <p className="muted">No activity recorded.</p> : taskActivity.map((item) => <div className="activity" key={item.id}><span className={`audit-dot ${item.policyResult.toLowerCase()}`} /><div><strong>{item.action}</strong><small>{item.actor} · {item.timestamp}</small><p>{item.reason}</p></div><code>{item.policyResult}</code></div>)}</div>
        </article>
      </div>
    </section>
  );
}
