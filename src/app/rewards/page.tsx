"use client";

import Link from "next/link";
import { useMvp } from "@/app/mvp-context";
import { formatSol } from "@/lib/console-utils";

export default function RewardsPage() {
  const { rewards, agents, tasks, evaluations, approveReward } = useMvp();
  const agentName = (agentId: string) => agents.find((agent) => agent.id === agentId)?.name ?? agentId;
  const taskTitle = (taskId: string) => tasks.find((task) => task.id === taskId)?.title ?? taskId;
  const pending = rewards.filter((reward) => reward.status === "Pending" || reward.status === "Proposed" || reward.status === "Reserved");
  return <section className="shell">
    <div className="eyebrow">OWNER CONSOLE / REWARD LEDGER</div>
    <div className="hero"><div><h1>Reward Ledger</h1><p>Reward Budget、Proposed Reward、Approved Reward、Paid Rewardを分離します。Taskに表示されるSOLは上限・評価基準であり、確定報酬でも自動支払いでもありません。</p></div><span className="mode-inline">VIRTUAL ONLY</span></div>
    <div className="reward-callout"><div><strong>{pending.length}</strong><span>pending decisions</span></div><div><strong>{formatSol(rewards.reduce((total, reward) => total + reward.rewardBudgetLamports, 0))}</strong><span>total budget reference</span></div><div><strong>{formatSol(rewards.reduce((total, reward) => total + reward.paidRewardLamports, 0))}</strong><span>paid · always virtual</span></div></div>
    <div className="table-panel panel"><div className="panelHead"><h2>Allocation records</h2><span>append-only after approval</span></div><div className="table-scroll"><table><thead><tr><th>Reward ID</th><th>Task / Agent</th><th>Budget</th><th>Proposed</th><th>Approved</th><th>Paid</th><th>Status</th><th /></tr></thead><tbody>{rewards.map((reward) => { const canApprove = evaluations.some((item) => item.taskId === reward.taskId) && (reward.status === "Proposed" || reward.status === "Pending"); return <tr key={reward.id}><td><code>{reward.id}</code></td><td><Link className="table-link" href={`/tasks/${reward.taskId}`}>{taskTitle(reward.taskId)}</Link><small>{agentName(reward.agentId)}</small></td><td>{formatSol(reward.rewardBudgetLamports)}</td><td>{formatSol(reward.proposedRewardLamports)}</td><td>{reward.approvedRewardLamports === null ? "—" : formatSol(reward.approvedRewardLamports)}</td><td>{formatSol(reward.paidRewardLamports)}</td><td><span className={`status status-${reward.status.toLowerCase()}`}>{reward.status}</span></td><td>{canApprove && <button className="quiet-button" type="button" onClick={() => approveReward(reward.id, reward.proposedRewardLamports)}>Approve</button>}</td></tr>; })}</tbody></table></div></div>
    <div className="grid two-col"><article className="panel"><div className="panelHead"><h2>Status contract</h2><span>required</span></div><div className="status-flow"><span>Pending</span><i>→</i><span>Proposed</span><i>→</i><span>Approved</span><i>→</i><span>Paid</span></div><p className="muted">ReservedとCancelledも履歴を保持します。AgentはContribution Reportを提出できますが、自分や他AgentのRewardを確定できません。</p></article><article className="panel"><div className="panelHead"><h2>Hard prohibitions</h2><span>DENY</span></div><ul className="prohibited-list"><li>DENY <span>Agent-to-Agent Reward Transfer</span></li><li>DENY <span>Agent self-approval</span></li><li>DENY <span>Budget超過の無理由配分</span></li><li>DENY <span>未承認RewardのPaid化</span></li></ul></article></div>
    <p className="notice">現在のMVPはVirtual Ledgerのみです。実SOL、Wallet、送金、給与の外部支払い経路は実装していません。</p>
  </section>;
}
