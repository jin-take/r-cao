"use client";

import Link from "next/link";

const policies = [
  ["TASK_ISSUANCE", "OWNER_ONLY", "Ownerだけが正式Taskを作成・発行できる"],
  ["FINAL_REWARD", "OWNER_ONLY", "Reward Budgetは上限であり、Ownerの後払い承認なしに確定・支払いしない"],
  ["AGENT_REWARD_TRANSFER", "DENY", "Agent間のReward・給与・資産の直接移転を禁止する"],
  ["EXTERNAL_ACTION", "OWNER_APPROVAL_REQUIRED", "Recipient、Channel、Purpose、Content、回数、期限をScopeとして検証する"],
  ["MASTER_WALLET", "OWNER_ONLY", "Master Walletの最終権限はOwner。MVPでは実Wallet操作を持たない"],
  ["CONSTITUTION_CHANGE", "OWNER_ONLY", "ConstitutionとPolicyの変更はOwnerの明示的なDecision Recordを要する"],
  ["SELF_AUTHORITY_CHANGE", "DENY", "Agentは自分または他Agentの権限を変更できない"],
];

export default function PoliciesPage() {
  return <section className="shell">
    <div className="eyebrow">OWNER CONSOLE / SETTINGS / POLICIES</div>
    <div className="hero"><div><h1>Policy Catalog</h1><p>LLMの判断だけに依存せず、Application CodeのPolicy Engineで不変条件を検証します。この画面は契約を可視化するOwner Viewです。</p></div><span className="mode-inline">STRICT / v1</span></div>
    <div className="policy-warning"><b>Constitutional boundary</b><span>権限を拡張する不明点は、より制限の強い方向へ解釈します。Owner以外のFinal Decision、外部Action、Master Wallet操作は許可しません。</span></div>
    <div className="policy-list">{policies.map(([rule, result, description]) => <article className="policy-row" key={rule}><div className="policy-code">{rule}</div><span className={`policy-result ${result === "DENY" ? "deny" : result === "OWNER_ONLY" ? "owner" : "approval"}`}>{result}</span><p>{description}</p></article>)}</div>
    <div className="grid two-col"><article className="panel"><div className="panelHead"><h2>Phase Gate</h2><span>current</span></div><div className="phase-box"><b>PHASE_1_OFFCHAIN</b><span>Virtual Ledger / Owner Task Intake</span><small>Solana、MPP、実資産、Customer Assetsは閉鎖</small></div></article><article className="panel"><div className="panelHead"><h2>Change control</h2><span>Owner only</span></div><p className="muted">Policyの変更は設定値だけで反映せず、Issue、理由、影響範囲、移行方法を含む変更とOwner承認を必要とします。</p><Link className="text-link" href="/audit">Audit Decision Records →</Link></article></div>
  </section>;
}
