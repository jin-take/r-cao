export type MppWalletConnector = "WALLET_STANDARD" | "WALLETCONNECT" | "METAMASK";
export type MppWalletNetwork = "LOCAL" | "SOLANA_DEVNET";

export interface MppPaymentApprovalScope {
  paymentId: string;
  taskId: string;
  serviceId: string;
  recipient: string;
  token: string;
  amountUnits: string;
  purpose: string;
  network: string;
  cluster: string;
  challengeHash: string;
  expiresAt?: string;
  instruction?: string;
}

export interface OwnerWalletSignature {
  connector: MppWalletConnector;
  publicKey: string;
  network: MppWalletNetwork;
  signature: string;
  signedMessage: string;
}

interface SolanaProvider {
  isConnected?: boolean;
  publicKey?: { toString(): string } | null;
  connect(): Promise<{ publicKey?: { toString(): string } }>;
  signMessage(message: Uint8Array, encoding?: string): Promise<{ signature: Uint8Array }>;
}

declare global {
  interface Window {
    solana?: SolanaProvider;
    phantom?: { solana?: SolanaProvider };
    ethereum?: unknown;
  }
}

function nonEmpty(value: string, field: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`${field} is required`);
  return trimmed;
}

export function assertSafeOwnerWalletNetwork(network: string, cluster: string): MppWalletNetwork {
  const normalizedNetwork = network.trim().toUpperCase();
  const normalizedCluster = cluster.trim().toUpperCase();
  if (normalizedNetwork === "LOCAL" && normalizedCluster === "LOCAL") return "LOCAL";
  if (normalizedNetwork === "SOLANA_DEVNET" && normalizedCluster === "DEVNET") return "SOLANA_DEVNET";
  throw new Error("Owner wallet approval is restricted to LOCAL or Solana DEVNET; mainnet/wrong cluster is blocked");
}

export function buildOwnerApprovalMessage(scope: MppPaymentApprovalScope): string {
  const network = assertSafeOwnerWalletNetwork(scope.network, scope.cluster);
  const canonical = {
    schema: "rcao-owner-mpp-approval-v1",
    payment_id: nonEmpty(scope.paymentId, "paymentId"),
    task_id: nonEmpty(scope.taskId, "taskId"),
    service_id: nonEmpty(scope.serviceId, "serviceId"),
    recipient: nonEmpty(scope.recipient, "recipient"),
    token: nonEmpty(scope.token, "token"),
    amount_units: nonEmpty(scope.amountUnits, "amountUnits"),
    purpose: nonEmpty(scope.purpose, "purpose"),
    network,
    cluster: scope.cluster.trim().toUpperCase(),
    challenge_hash: nonEmpty(scope.challengeHash, "challengeHash"),
    expires_at: scope.expiresAt?.trim() || null,
    instruction: scope.instruction?.trim() || "Approve the exact task-bound MPP service payment shown in Owner Console.",
  };
  return JSON.stringify(canonical, null, 2);
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return globalThis.btoa(binary);
}

function injectedSolanaProvider(): SolanaProvider | null {
  if (typeof window === "undefined") return null;
  return window.phantom?.solana ?? window.solana ?? null;
}

export function walletConnectorAvailability(): Record<MppWalletConnector, { available: boolean; signing: boolean; note: string }> {
  const solana = injectedSolanaProvider();
  const metamask = typeof window !== "undefined" && Boolean(window.ethereum);
  return {
    WALLET_STANDARD: {
      available: Boolean(solana),
      signing: Boolean(solana),
      note: "Preferred Owner approval path. Uses an injected Solana wallet signMessage boundary; no secret key is read by R-CAO.",
    },
    WALLETCONNECT: {
      available: false,
      signing: false,
      note: "Reown/WalletConnect requires an explicit project adapter. R-CAO does not silently load a remote signer or hand Agent Runtime a session.",
    },
    METAMASK: {
      available: metamask,
      signing: false,
      note: "Presence is detected for compatibility review, but EVM personal_sign is not accepted as a Solana MPP authorization.",
    },
  };
}

export async function signOwnerPaymentApproval(scope: MppPaymentApprovalScope): Promise<OwnerWalletSignature> {
  const network = assertSafeOwnerWalletNetwork(scope.network, scope.cluster);
  if (network === "LOCAL") {
    throw new Error("LOCAL fixtures do not require a browser-wallet signature; use the authenticated Owner decision API instead");
  }
  const provider = injectedSolanaProvider();
  if (!provider) throw new Error("No compatible injected Solana Owner wallet is available");
  const connection = await provider.connect();
  const publicKey = connection.publicKey?.toString() || provider.publicKey?.toString();
  if (!publicKey) throw new Error("Connected wallet did not expose a public key");
  const signedMessage = buildOwnerApprovalMessage(scope);
  const signed = await provider.signMessage(new TextEncoder().encode(signedMessage), "utf8");
  if (!(signed.signature instanceof Uint8Array) || signed.signature.length === 0) {
    throw new Error("Wallet did not return a valid signature");
  }
  return {
    connector: "WALLET_STANDARD",
    publicKey,
    network,
    signature: bytesToBase64(signed.signature),
    signedMessage,
  };
}
