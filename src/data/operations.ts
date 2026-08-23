export type OperationScope = "TASKS" | "RUNS" | "MESSAGES" | "MEMORY" | "AUDIT";

export interface OperationRecord {
  recordId: string;
  scope: OperationScope;
  title: string;
  body: string;
  taskId: string | null;
  runId: string | null;
  agentId: string | null;
  status: string;
  createdAt: string;
  refs: string[];
}

// A read-only fixture for the console until the Python search endpoint is
// connected to PostgreSQL. It mirrors the searchable fields in the API.
export const demoOperations: OperationRecord[] = [
  {
    recordId: "msg-7f31",
    scope: "MESSAGES",
    title: "Independent review request",
    body: "Review task T-001 against its acceptance criteria.",
    taskId: "T-001",
    runId: "run-001-review",
    agentId: "a-review",
    status: "SENT",
    createdAt: "2026-08-23T09:14:00Z",
    refs: ["evidence://task/T-001/review"],
  },
  {
    recordId: "run-001-builder",
    scope: "RUNS",
    title: "Builder Agent run",
    body: "Proposed a safe off-chain implementation boundary.",
    taskId: "T-001",
    runId: "run-001-builder",
    agentId: "a-builder",
    status: "COMPLETED",
    createdAt: "2026-08-23T08:42:00Z",
    refs: ["trace://run/run-001-builder"],
  },
  {
    recordId: "memory-003",
    scope: "MEMORY",
    title: "Policy memory: Owner approval",
    body: "Agent proposals never become authority without an Owner decision.",
    taskId: null,
    runId: null,
    agentId: null,
    status: "ACTIVE",
    createdAt: "2026-08-22T11:00:00Z",
    refs: ["audit://policy/owner-approval"],
  },
];
