"use client";

import { useMvp } from "@/app/mvp-context";
import { agentName, formatSol } from "@/data/mvp";

export default function ProposalsPage() {
  const { proposals, decideApproval, approvals } = useMvp();
  return <section className="shell">
    <div className="eyebrow">OWNER CONSOLE / BOARD PROPOSALS</div>
    <div className="hero"><div><h1>Board Proposals</h1><p>経営、資本配分、Risk、組織変更に関するProposalです。Agentは提案できますが、最終決議はOwnerだけが行います。</p></div><span className="mode-inline">OWNER DECISION</span></div>
    <div className="proposal-list">{proposals.map((proposal) => { const approval = approvals.find((item) => item.approvalType === "BOARD_PROPOSAL" && item.targetId === proposal.id && !item.ownerDecision); return <article className="proposal-card panel" key={proposal.id}><div className="proposal-head"><div><span className="tag">{proposal.status}</span><h2>{proposal.title}</h2><p>Proposed by {agentName(proposal.proposer)}</p></div><div className="proposal-budget"><span>Required Budget</span><strong>{formatSol(proposal.requiredBudgetLamports)}</strong><small>{proposal.expectedPeriod}</small></div></div><div className="proposal-columns"><div><h3>Background / Objective</h3><p>{proposal.background}</p><p>{proposal.objective}</p></div><div><h3>Recommended Option</h3><p className="recommendation">{proposal.recommendedOption}</p><h3>Expected Return</h3><p>{proposal.expectedReturn}</p></div></div><div className="proposal-reviews"><span>Strategy <b>{proposal.strategyReview ?? "—"}</b></span><span>Treasury <b>{proposal.treasuryReview ?? "—"}</b></span><span>Audit <b>{proposal.auditReview ?? "—"}</b></span></div>{approval && <div className="approval-actions"><button className="approve-button" type="button" onClick={() => decideApproval(approval.id, "APPROVE", "Owner approved Board Proposal")}>Approve</button><button className="secondary-button" type="button" onClick={() => decideApproval(approval.id, "REQUEST_CHANGES", "Owner requested revised proposal")}>Request Changes</button><button className="quiet-button" type="button" onClick={() => decideApproval(approval.id, "HOLD", "Owner placed proposal on hold")}>Hold</button><button className="danger-button" type="button" onClick={() => decideApproval(approval.id, "REJECT", "Owner rejected proposal")}>Reject</button></div>}</article>; })}</div>
    <p className="notice">Approval後も、実資産移転やMaster Wallet操作は別のOwner-only Gateです。MVPではProposalを決議しても外部実行は発生しません。</p>
  </section>;
}
