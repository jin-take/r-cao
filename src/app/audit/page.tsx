"use client";

import Link from "next/link";
import { useMvp } from "@/app/mvp-context";
import { isBlockingAuditAlert, isOwnerApprovalPending } from "@/lib/console-utils";

export default function AuditPage() {
  const { auditLogs, audits, approvals } = useMvp();
  const alerts = auditLogs.filter(isBlockingAuditAlert);
  const pendingApprovals = approvals.filter(isOwnerApprovalPending);
  return <section className="shell">
    <div className="eyebrow">OWNER CONSOLE / AUDIT VIEW</div>
    <div className="hero"><div><h1>Audit Log</h1><p>重要操作をActor、Before、After、Policy Result、Reason、Timestamp、Correlation IDで追跡します。ログは削除せず追記型で扱います。</p></div><span className="mode-inline">APPEND ONLY</span></div>
    <div className="audit-summary"><div><strong>{auditLogs.length}</strong><span>recorded events</span></div><div><strong>{alerts.length}</strong><span>blocking alerts</span></div><div><strong>{pendingApprovals.length}</strong><span>owner approvals pending</span></div><div><strong>{audits.length}</strong><span>task audits</span></div></div>
    <div className="audit-table panel"><div className="panelHead"><h2>Event stream</h2><span>newest first</span></div>{auditLogs.map((item) => <div className="audit-row" key={item.id}><span className={`audit-dot ${item.policyResult.toLowerCase()}`} /><div className="audit-main"><div><strong>{item.action}</strong><span className="tag">{item.policyResult}</span></div><p>{item.reason}</p><small>{item.actor} · {item.actorType} · {item.targetType}/{item.targetId}</small></div><div className="audit-meta"><time>{item.timestamp}</time><code>{item.correlationId}</code></div></div>)}</div>
    <div className="audit-links"><Link className="secondary-button" href="/tasks/T-001">Open T-001 evidence</Link><Link className="secondary-button" href="/settings/policies">View Policy catalog</Link></div>
    <p className="notice">OWNER_APPROVAL_REQUIREDは異常ではなくOwnerの判断待ちです。DENYのみをブロッキング警告として扱います。認証Token、秘密鍵、seed phrase、API secretなどの秘密情報はAudit Logへ保存しません。</p>
  </section>;
}
