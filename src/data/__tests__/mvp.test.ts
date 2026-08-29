import { describe, expect, it } from "vitest";
import {
  isBlockingAuditAlert,
  isOwnerApprovalPending,
  mvpAuditLogs,
} from "../mvp";

describe("MVP audit classification", () => {
  it("does not treat an Owner approval request as a blocking alert", () => {
    const approval = mvpAuditLogs.find((item) => item.targetId === "reward-001");

    expect(approval).toBeDefined();
    expect(isOwnerApprovalPending(approval!)).toBe(true);
    expect(isBlockingAuditAlert(approval!)).toBe(false);
  });

  it("treats only DENY as a blocking alert", () => {
    expect(isBlockingAuditAlert({ policyResult: "DENY" })).toBe(true);
    expect(isBlockingAuditAlert({ policyResult: "ALLOW" })).toBe(false);
    expect(isBlockingAuditAlert({ policyResult: "ALLOW_WITH_SCOPE" })).toBe(false);
    expect(isBlockingAuditAlert({ policyResult: "OWNER_APPROVAL_REQUIRED" })).toBe(false);
  });
});
