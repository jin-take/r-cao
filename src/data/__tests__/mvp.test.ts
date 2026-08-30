import { describe, expect, it } from "vitest";
import {
  isBlockingAuditAlert,
  isOwnerApprovalPending,
  mvpApprovals,
  mvpAuditLogs,
  resolveRewardApproval,
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

  it("resolves the matching reward approval when the reward is approved directly", () => {
    const approvals = resolveRewardApproval(mvpApprovals, "reward-001", "Owner approved");
    const rewardApproval = approvals.find((item) => item.targetId === "reward-001");

    expect(rewardApproval?.ownerDecision).toBe("APPROVE");
    expect(rewardApproval?.comment).toBe("Owner approved");
  });

  it("does not reopen or overwrite an already decided reward approval", () => {
    const approvals = resolveRewardApproval(
      [{ ...mvpApprovals[1], ownerDecision: "REJECT", comment: "Not yet" }],
      "reward-001",
      "Owner approved",
    );

    expect(approvals[0].ownerDecision).toBe("REJECT");
    expect(approvals[0].comment).toBe("Not yet");
  });

  it("treats only DENY as a blocking alert", () => {
    expect(isBlockingAuditAlert({ policyResult: "DENY" })).toBe(true);
    expect(isBlockingAuditAlert({ policyResult: "ALLOW" })).toBe(false);
    expect(isBlockingAuditAlert({ policyResult: "ALLOW_WITH_SCOPE" })).toBe(false);
    expect(isBlockingAuditAlert({ policyResult: "OWNER_APPROVAL_REQUIRED" })).toBe(false);
  });
});
