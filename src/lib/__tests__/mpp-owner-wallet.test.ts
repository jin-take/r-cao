import { describe, expect, it } from "vitest";
import {
  assertSafeOwnerWalletNetwork,
  buildOwnerApprovalMessage,
  walletConnectorAvailability,
} from "../mpp-owner-wallet";

const scope = {
  paymentId: "pay-1",
  taskId: "task-1",
  serviceId: "service-1",
  recipient: "recipient-public-key",
  token: "SPL_TEST_USDC",
  amountUnits: "100",
  purpose: "SERVICE_PAYMENT",
  network: "SOLANA_DEVNET",
  cluster: "DEVNET",
  challengeHash: "a".repeat(64),
};

describe("MPP Owner wallet boundary", () => {
  it("fails closed for mainnet and wrong cluster", () => {
    expect(() => assertSafeOwnerWalletNetwork("SOLANA_MAINNET", "MAINNET")).toThrow(/LOCAL or Solana DEVNET/);
    expect(() => assertSafeOwnerWalletNetwork("SOLANA_DEVNET", "MAINNET")).toThrow(/wrong cluster/);
    expect(assertSafeOwnerWalletNetwork("SOLANA_DEVNET", "DEVNET")).toBe("SOLANA_DEVNET");
  });

  it("binds the signed message to exact payment scope", () => {
    const message = buildOwnerApprovalMessage(scope);
    expect(message).toContain('"payment_id": "pay-1"');
    expect(message).toContain('"recipient": "recipient-public-key"');
    expect(message).toContain('"amount_units": "100"');
    expect(message).toContain('"challenge_hash"');
    expect(message).not.toContain("private_key");
    expect(message).not.toContain("seed phrase");
  });

  it("does not treat MetaMask or WalletConnect as a Solana Agent signer", () => {
    const support = walletConnectorAvailability();
    expect(support.WALLETCONNECT.signing).toBe(false);
    expect(support.METAMASK.signing).toBe(false);
  });
});
