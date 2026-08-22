import Link from "next/link";
import { demoAgents, demoTasks, virtualTreasuryLamports } from "@/data/demo";

const sol = (lamports: number) => `${(lamports / 1_000_000_000).toFixed(2)} SOL`;

export default function OrganizationDashboard() {
  const active = demoTasks.filter((task) => !["REWARDED", "CANCELLED"].includes(task.state));
  return (
    <section className="shell">
      <div className="eyebrow">ORGANIZATION DASHBOARD</div>
      <div className="hero">
        <div><h1>Compounding control plane</h1><p>憲法に従い、Task・Agent・Reward・Treasury・監査証跡を一つの運用面で扱います。</p></div>
        <Link className="primary" href="/tasks">Open Task Board</Link>
      </div>
      <div className="metrics">
        <article><span>Virtual Treasury</span><strong>{sol(virtualTreasuryLamports)}</strong><small>実資産ではありません</small></article>
        <article><span>Active Tasks</span><strong>{active.length}</strong><small>Owner-issued boundary</small></article>
        <article><span>Active Agents</span><strong>{demoAgents.length}</strong><small>named and auditable</small></article>
        <article><span>Policy Mode</span><strong>STRICT</strong><small>Owner final approval</small></article>
      </div>
      <div className="grid">
        <article className="panel"><div className="panelHead"><h2>Operating cycle</h2><span>Phase 1</span></div><ol className="cycle"><li>Sense <small>Task intake</small></li><li>Think <small>Plan & assign</small></li><li>Act <small>Produce</small></li><li>Reflect <small>Review</small></li><li>Memory <small>Audit</small></li><li>Grow <small>Reinvest</small></li></ol></article>
        <article className="panel"><div className="panelHead"><h2>Constitutional gates</h2><span>enforced</span></div><ul className="gates"><li><b>Task issuance</b><span>Owner only</span></li><li><b>Final acceptance</b><span>Owner only</span></li><li><b>Treasury decision</b><span>Owner only</span></li><li><b>Reward rail</b><span>Virtual ledger</span></li></ul></article>
      </div>
    </section>
  );
}
