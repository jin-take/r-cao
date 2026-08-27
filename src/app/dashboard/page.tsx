"use client";

import Link from "next/link";
import { useMvp } from "@/app/mvp-context";
import { formatSol } from "@/data/mvp";

export default function DashboardPage() {
  const { agents, tasks, approvals, rewards, proposals, externalActions, auditLogs } = useMvp();
  const activeTasks = tasks.filter((task) => !["COMPLETED", "REJECTED", "CANCELLED"].includes(task.status));
  const reservedBudget = activeTasks.reduce((total, task) => total + task.rewardBudgetLamports, 0);
  const pendingRewards = rewards.filter((reward) => ["Pending", "Proposed", "Reserved"].includes(reward.status));
  const pendingExternal = externalActions.filter((item) => !item.ownerDecision);

  return (
    <section className="shell">
      <div className="eyebrow">OWNER CONSOLE / DASHBOARD</div>
      <div className="hero">
        <div>
          <h1>Compounding control plane</h1>
          <p>OwnerがTask・Executive・Review・Audit・Reward・Treasuryを一つの運用面で確認する、R-CAO Owner-Directed MVPです。</p>
        </div>
        <Link className="primary" href="/tasks">Open Task Board</Link>
      </div>

      <div className="metrics">
        <article><span>Virtual Treasury</span><strong>{formatSol(12_500_000_000)}</strong><small>実資産・Wallet操作なし</small></article>
        <article><span>Active Tasks</span><strong>{activeTasks.length}</strong><small>Owner-issued boundary</small></article>
        <article><span>Executive / Audit</span><strong>{agents.filter((agent) => agent.agentType !== "SUB_AGENT").length}</strong><small>named and auditable</small></article>
        <article><span>Approval Queue</span><strong>{approvals.filter((item) => !item.ownerDecision).length}</strong><small>Owner final decision</small></article>
      </div>

      <div className="grid dashboard-grid">
        <article className="panel">
          <div className="panelHead"><h2>Owner-directed workflow</h2><span>Phase 1 / off-chain</span></div>
          <ol className="workflow">
            {[
              ["01", "Owner Task", "Ownerだけが正式Taskを発行"],
              ["02", "Executive Assignment", "固有名Agentへ割当"],
              ["03", "Planning / Execution", "Sub Taskと成果物を管理"],
              ["04", "Review / Audit", "実行者から職務を分離"],
              ["05", "Owner Evaluation", "完了条件と価値を評価"],
              ["06", "Final Reward", "Owner承認後に台帳確定"],
            ].map(([number, title, description]) => <li key={number}><b>{number}</b><div><strong>{title}</strong><small>{description}</small></div></li>)}
          </ol>
        </article>
        <article className="panel">
          <div className="panelHead"><h2>Constitutional gates</h2><span>strict</span></div>
          <ul className="gates">
            <li><b>Task issuance</b><span>Owner only</span></li>
            <li><b>Final acceptance</b><span>Owner only</span></li>
            <li><b>Reward Budget</b><span>not payment</span></li>
            <li><b>Agent transfer</b><span>DENY</span></li>
            <li><b>External Action</b><span>approval + scope</span></li>
            <li><b>Master Wallet</b><span>not in MVP</span></li>
          </ul>
        </article>
      </div>

      <div className="summary-row">
        <Link className="summary-card" href="/approvals"><span>Owner Approval Pending</span><strong>{approvals.filter((item) => !item.ownerDecision).length}</strong><small>Task / Reward / Proposal / External</small></Link>
        <Link className="summary-card" href="/rewards"><span>Reward Approval Pending</span><strong>{pendingRewards.length}</strong><small>Budget {formatSol(reservedBudget)}</small></Link>
        <Link className="summary-card" href="/proposals"><span>Board Proposals</span><strong>{proposals.filter((item) => !item.ownerDecision).length}</strong><small>Owner decision required</small></Link>
        <Link className="summary-card" href="/audit"><span>Audit Alerts</span><strong>{auditLogs.filter((item) => item.policyResult !== "ALLOW").length}</strong><small>{pendingExternal.length} external request pending</small></Link>
      </div>

      <p className="notice"><b>Safety boundary:</b> このMVPはVirtual LedgerとOff-chain業務サイクルのみを扱います。実SOL送金、Wallet接続、DeFi、Validator、外部送信、Agent間Reward Transferはありません。</p>
    </section>
  );
}
