"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMvp } from "@/app/mvp-context";
import { decideMppPayment, stopMppPaymentProfile } from "@/lib/mpp-owner-api";
import {
  buildOwnerApprovalMessage,
  signOwnerPaymentApproval,
  walletConnectorAvailability,
  type MppPaymentApprovalScope,
} from "@/lib/mpp-owner-wallet";

function stringValue(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function paymentScope(paymentId: string, auditLogs: ReturnType<typeof useMvp>["auditLogs"]): MppPaymentApprovalScope & { profileId: string; profileVersion: number } {
  const relevant = [...auditLogs].reverse().find((log) => log.targetId === paymentId && log.targetType === "SERVICE_PAYMENT");
  const state = relevant?.after ?? {};
  return {
    paymentId,
    taskId: stringValue(state.task_id),
    serviceId: stringValue(state.service_id),
    recipient: stringValue(state.recipient),
    token: stringValue(state.token),
    amountUnits: stringValue(state.amount_units),
    purpose: stringValue(state.purpose || "SERVICE_PAYMENT"),
    network: stringValue(state.network),
    cluster: stringValue(state.network) === "SOLANA_DEVNET" ? "DEVNET" : "LOCAL",
    challengeHash: stringValue(state.challenge_hash),
    expiresAt: stringValue(state.expires_at),
    instruction: "Approve only this exact Task-bound Service Payment. Do not authorize Reward, Treasury, or Agent-to-Agent transfer.",
    profileId: stringValue(state.profile_id),
    profileVersion: Number(state.profile_version || 0),
  };
}

export default function PaymentsPage() {
  const { session, actor, approvals, auditLogs, refresh } = useMvp();
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const walletSupport = useMemo(() => walletConnectorAvailability(), []);
  const pending = approvals.filter((approval) => approval.approvalType === "POLICY_EXCEPTION" && !approval.ownerDecision);

  const act = async (paymentId: string, decision: "APPROVE" | "REJECT" | "REQUEST_CHANGES" | "HOLD") => {
    if (!session || actor?.actorType !== "OWNER" || actor.role !== "OWNER") {
      setError("Authenticated Owner session is required.");
      return;
    }
    const scope = paymentScope(paymentId, auditLogs);
    setBusy(paymentId);
    setError(null);
    setNotice(null);
    try {
      let comment = `Owner ${decision.toLowerCase()} from MPP Approval Console.`;
      if (decision === "APPROVE") {
        if (!scope.recipient || !scope.token || !scope.amountUnits || !scope.taskId || !scope.serviceId || !scope.challengeHash) {
          throw new Error("Payment scope is incomplete in Audit; approval is blocked.");
        }
        if (scope.network === "SOLANA_DEVNET") {
          const signed = await signOwnerPaymentApproval(scope);
          comment = `Owner wallet approval: connector=${signed.connector}; public_key=${signed.publicKey}; signature=${signed.signature}; scope=${buildOwnerApprovalMessage(scope)}`;
        } else {
          buildOwnerApprovalMessage(scope);
          comment = "Owner approved LOCAL fixture after reviewing exact payment scope; browser wallet signing is intentionally not used for LOCAL fixtures.";
        }
      }
      await decideMppPayment(session, paymentId, decision, comment);
      setNotice(`${paymentId}: ${decision} recorded with authenticated Owner identity.`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "MPP Owner action failed");
    } finally {
      setBusy(null);
    }
  };

  const stopProfile = async (paymentId: string) => {
    if (!session) return;
    const scope = paymentScope(paymentId, auditLogs);
    if (!scope.profileId || scope.profileVersion <= 0) {
      setError("Payment Profile snapshot is not available in Audit.");
      return;
    }
    setBusy(paymentId);
    try {
      await stopMppPaymentProfile(session, scope.profileId, scope.profileVersion, `Owner stopped MPP capability from payment ${paymentId}`);
      setNotice(`${scope.profileId}: MPP Payment Profile stopped.`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to stop Payment Profile");
    } finally {
      setBusy(null);
    }
  };

  return <section className="shell">
    <div className="eyebrow">OWNER CONSOLE / MPP</div>
    <div className="hero"><div><h1>MPP Payment Approval</h1><p>Task-bound Service Paymentの支払先、Token、数量、Policy、命令を確認してからOwnerが判断します。秘密鍵やseed phraseはConsoleへ入力しません。</p></div><span className="mode-inline">{pending.length} PENDING</span></div>

    <div className="approval-rules"><span>Owner identity rechecked by API</span><span>LOCAL / DEVNET only</span><span>No Agent signer</span><span>No Reward/Treasury transfer</span></div>

    {error && <p className="notice">ERROR: {error}</p>}
    {notice && <p className="notice">{notice}</p>}

    <div className="approval-list">{pending.map((approval) => {
      const scope = paymentScope(approval.targetId, auditLogs);
      const complete = Boolean(scope.taskId && scope.serviceId && scope.recipient && scope.token && scope.amountUnits && scope.challengeHash);
      return <article className="approval-card" key={approval.id}>
        <div className="approval-icon">$</div>
        <div className="approval-main">
          <div className="approval-top"><span className="tag">MPP SERVICE PAYMENT</span><time>{approval.createdAt}</time></div>
          <h2>{scope.amountUnits || "?"} {scope.token || "TOKEN"} → {scope.recipient || "unknown recipient"}</h2>
          <p>Payment <code>{approval.targetId}</code> · Task <code>{scope.taskId || "missing"}</code> · Service <code>{scope.serviceId || "missing"}</code></p>
          <p>Network <b>{scope.network || "missing"}</b> / {scope.cluster} · Purpose <b>{scope.purpose}</b> · Policy request <code>{approval.id}</code></p>
          <p>Challenge <code>{scope.challengeHash || "missing"}</code></p>
          {!complete && <p className="notice">Auditから完全な支払scopeを復元できないためApproveはfail-closedで無効です。</p>}
          <div className="approval-actions">
            <button className="approve-button" type="button" disabled={!complete || busy === approval.targetId} onClick={() => void act(approval.targetId, "APPROVE")}>Review & Approve</button>
            <button className="secondary-button" type="button" disabled={busy === approval.targetId} onClick={() => void act(approval.targetId, "REQUEST_CHANGES")}>Request Changes</button>
            <button className="quiet-button" type="button" disabled={busy === approval.targetId} onClick={() => void act(approval.targetId, "HOLD")}>Hold</button>
            <button className="danger-button" type="button" disabled={busy === approval.targetId} onClick={() => void act(approval.targetId, "REJECT")}>Reject</button>
            <button className="danger-button" type="button" disabled={!scope.profileId || busy === approval.targetId} onClick={() => void stopProfile(approval.targetId)}>Stop MPP capability</button>
          </div>
        </div>
      </article>;
    })}</div>

    {pending.length === 0 && <div className="empty-state"><strong>MPP approval queue is clear.</strong><span>Pending POLICY_EXCEPTION payment approvals will appear here.</span></div>}

    <div className="card-grid">
      {Object.entries(walletSupport).map(([connector, support]) => <article className="card" key={connector}><h2>{connector}</h2><p>{support.note}</p><p><b>{support.available ? "Detected" : "Not detected"}</b> · {support.signing ? "signMessage enabled" : "signing disabled"}</p></article>)}
    </div>

    <p className="notice">WalletConnect/Reown and MetaMask are not silently promoted to Agent signing sessions. Wallet Standard-compatible Solana signing is used only for an explicit Owner DEVNET approval; API-side Owner Identity and Policy remain authoritative. Session ceilings/revoke continue to be enforced by the #10 MPP Session boundary.</p>
    <Link className="text-link" href="/approvals">← General Approval Center</Link>
  </section>;
}
