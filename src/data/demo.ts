import type { Agent, Task } from "@/domain/model";

export const demoAgents: Agent[] = [
  { id: "a-manager", name: "Orion", role: "MANAGER", capabilityHash: "sha256:manager", model: "policy-bound", status: "ACTIVE", reputation: 82, rank: 2 },
  { id: "a-research", name: "Lyra", role: "RESEARCHER", capabilityHash: "sha256:research", model: "research", status: "ACTIVE", reputation: 78, rank: 2 },
  { id: "a-builder", name: "Vega", role: "BUILDER", capabilityHash: "sha256:builder", model: "coding", status: "ACTIVE", reputation: 88, rank: 3 },
  { id: "a-review", name: "Astra", role: "REVIEWER", capabilityHash: "sha256:review", model: "review", status: "ACTIVE", reputation: 91, rank: 3 },
  { id: "a-treasury", name: "Solon", role: "TREASURY", capabilityHash: "sha256:treasury", model: "finance", status: "ACTIVE", reputation: 80, rank: 2 },
  { id: "a-audit", name: "Themis", role: "AUDITOR", capabilityHash: "sha256:audit", model: "audit", status: "ACTIVE", reputation: 94, rank: 4 },
];

export const demoTasks: Task[] = [
  { id: "T-001", title: "Phase 1 foundation", description: "Build the off-chain simulator foundation", rewardLamports: 1_000_000_000, difficulty: 4, state: "IN_REVIEW", deadline: "2026-09-05", acceptanceCriteria: ["Policy tests pass", "Audit evidence exists"], issuedBy: "owner-local" },
  { id: "T-002", title: "Treasury reinvestment memo", description: "Compare infrastructure options", rewardLamports: 300_000_000, difficulty: 2, state: "IN_PROGRESS", deadline: "2026-09-08", acceptanceCriteria: ["ROI and risk are documented"], issuedBy: "owner-local" },
  { id: "T-003", title: "Devnet evidence design", description: "Define the Phase 2 hash recording boundary", rewardLamports: 500_000_000, difficulty: 3, state: "DRAFT", deadline: "2026-09-15", acceptanceCriteria: ["No production transfer path"], issuedBy: null },
];

export const virtualTreasuryLamports = 12_500_000_000;
