import { describe, expect, it } from "vitest";
import {
  isBlockingAuditAlert,
  isOwnerApprovalPending,
  mvpApprovals,
  mvpAuditLogs,
} from "../mvp";

describe("MVP audit classification", () => {
  it("does not treat an Owner approval request as a blocking alert", () => {
    const auditEvent = mvpAuditLogs.find((item) => item.targetId === "reward-001");
    const approval = mvpApprovals.find((item) => item.targetId === "reward-001");

    expect(auditEvent).toBeDefined();
    expect(approval).toBeDefined();
    expect(isOwnerApprovalPending(approval!)).toBe(true);
    expect(isBlockingAuditAlert(auditEvent!)).toBe(false);
    expect(isOwnerApprovalPending({ ownerDecision: "APPROVE" })).toBe(false);
  });

  it("treats only DENY as a blocking alert", () => {
    expect(isBlockingAuditAlert({ policyResult: "DENY" })).toBe(true);
    expect(isBlockingAuditAlert({ policyResult: "ALLOW" })).toBe(false);
    expect(isBlockingAuditAlert({ policyResult: "ALLOW_WITH_SCOPE" })).toBe(false);
    expect(isBlockingAuditAlert({ policyResult: "OWNER_APPROVAL_REQUIRED" })).toBe(false);
  });
});
