import type { ApprovalDecision } from "@/domain/model";
import type { ConsoleSession } from "@/lib/rcao-api";

export interface MppOwnerDecisionResult {
  payment_id?: string;
  approval_id?: string;
  decision?: string;
  status?: string;
  [key: string]: unknown;
}

function baseUrl(session: ConsoleSession): string {
  return session.baseUrl.trim().replace(/\/+$/, "");
}

async function ownerPost<T>(session: ConsoleSession, path: string, body: unknown): Promise<T> {
  const response = await fetch(`${baseUrl(session)}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.token}`,
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) as unknown : null;
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? String((payload as { detail?: unknown }).detail ?? "MPP Owner action failed")
      : "MPP Owner action failed";
    throw new Error(`${detail} (HTTP ${response.status})`);
  }
  return payload as T;
}

export function decideMppPayment(
  session: ConsoleSession,
  paymentId: string,
  decision: Extract<ApprovalDecision, "APPROVE" | "REJECT" | "REQUEST_CHANGES" | "HOLD">,
  comment: string,
): Promise<MppOwnerDecisionResult> {
  return ownerPost<MppOwnerDecisionResult>(
    session,
    `/api/v1/commands/service-payments/${encodeURIComponent(paymentId)}/approval`,
    { decision, comment },
  );
}

export function cancelMppPayment(
  session: ConsoleSession,
  paymentId: string,
  reason: string,
): Promise<MppOwnerDecisionResult> {
  const query = new URLSearchParams({ reason });
  return ownerPost<MppOwnerDecisionResult>(
    session,
    `/api/v1/commands/service-payments/${encodeURIComponent(paymentId)}/cancel?${query.toString()}`,
    {},
  );
}

export function stopMppPaymentProfile(
  session: ConsoleSession,
  profileId: string,
  expectedVersion: number,
  reason: string,
): Promise<unknown> {
  return ownerPost<unknown>(
    session,
    `/api/v1/commands/payment-profiles/${encodeURIComponent(profileId)}/status`,
    {
      status: "STOPPED",
      expected_version: expectedVersion,
      reason,
    },
  );
}
