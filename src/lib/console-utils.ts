import type { MvpApproval, MvpAuditLog } from "@/domain/model";

export const lamportsPerSol = 1_000_000_000;

export function formatSol(lamports: number): string {
  return `${(lamports / lamportsPerSol).toFixed(2)} SOL`;
}

export function isBlockingAuditAlert(
  log: Pick<MvpAuditLog, "policyResult">,
): boolean {
  return log.policyResult === "DENY";
}

export function isOwnerApprovalPending(
  approval: Pick<MvpApproval, "ownerDecision">,
): boolean {
  return approval.ownerDecision === null;
}
