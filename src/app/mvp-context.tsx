"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type {
  ApprovalDecision,
  MvpAgent,
  MvpApproval,
  MvpAudit,
  MvpAuditLog,
  MvpEvaluation,
  MvpExternalAction,
  MvpProposal,
  MvpReview,
  MvpReward,
  MvpSubTask,
  MvpTask,
} from "@/domain/model";
import type { OperationRecord, OperationScope } from "@/data/operations";
import {
  clearStoredSession,
  defaultApiBaseUrl,
  loadConsoleSnapshot,
  rcaoApi,
  readStoredSession,
  writeStoredSession,
  type ConsoleDashboard,
  type ConsoleSession,
  type OwnerActor,
  RcaoApiError,
} from "@/lib/rcao-api";

export type CreateTaskInput = Pick<
  MvpTask,
  "title" | "objective" | "deadline" | "rewardBudgetLamports" | "assignedExecutiveAgentId"
>;

interface MvpContextValue {
  actor: OwnerActor | null;
  session: ConsoleSession | null;
  dashboard: ConsoleDashboard | null;
  connected: boolean;
  loading: boolean;
  error: string | null;
  commandError: string | null;
  operationsLoading: boolean;
  operationsError: string | null;
  agents: MvpAgent[];
  tasks: MvpTask[];
  subtasks: MvpSubTask[];
  reviews: MvpReview[];
  audits: MvpAudit[];
  evaluations: MvpEvaluation[];
  rewards: MvpReward[];
  approvals: MvpApproval[];
  proposals: MvpProposal[];
  externalActions: MvpExternalAction[];
  auditLogs: MvpAuditLog[];
  operations: OperationRecord[];
  connect: (baseUrl: string, token: string) => Promise<void>;
  disconnect: () => void;
  refresh: () => Promise<void>;
  searchOperations: (query: string, scope: OperationScope) => Promise<void>;
  clearCommandError: () => void;
  createTask: (input: CreateTaskInput) => Promise<void>;
  setTaskStatus: (taskId: string, status: MvpTask["status"], reason?: string) => Promise<void>;
  evaluateTask: (taskId: string) => Promise<void>;
  decideApproval: (approvalId: string, decision: ApprovalDecision, comment?: string) => Promise<void>;
  approveReward: (rewardId: string, amount: number, comment?: string) => Promise<void>;
  setAgentStatus: (agentId: string, status: MvpAgent["status"], reason?: string) => Promise<void>;
}

const MvpContext = createContext<MvpContextValue | null>(null);

function errorMessage(cause: unknown): string {
  if (cause instanceof RcaoApiError) return cause.message;
  if (cause instanceof Error) return cause.message;
  return "Control Plane request failed";
}

export function MvpProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<ConsoleSession | null>(null);
  const [actor, setActor] = useState<OwnerActor | null>(null);
  const [snapshot, setSnapshot] = useState<Awaited<ReturnType<typeof loadConsoleSnapshot>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);
  const [operationsLoading, setOperationsLoading] = useState(false);
  const [operationsError, setOperationsError] = useState<string | null>(null);

  const applySnapshot = useCallback((next: Awaited<ReturnType<typeof loadConsoleSnapshot>>) => {
    setActor(next.actor);
    setSnapshot(next);
  }, []);

  const load = useCallback(async (currentSession: ConsoleSession): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const next = await loadConsoleSnapshot(currentSession);
      applySnapshot(next);
    } catch (cause) {
      if (cause instanceof RcaoApiError && cause.kind === "AUTHENTICATION") {
        setSession(null);
        setActor(null);
        setSnapshot(null);
        clearStoredSession();
      }
      setError(errorMessage(cause));
      throw cause;
    } finally {
      setLoading(false);
    }
  }, [applySnapshot]);

  useEffect(() => {
    const stored = readStoredSession();
    if (!stored) {
      setLoading(false);
      return;
    }
    setSession(stored);
    void load(stored).catch(() => undefined);
  }, [load]);

  const connect = useCallback(async (baseUrl: string, token: string): Promise<void> => {
    const candidate = { baseUrl: baseUrl.trim() || defaultApiBaseUrl(), token: token.trim() };
    if (!candidate.token) {
      setError("OwnerのBearer tokenを入力してください。");
      return;
    }
    setLoading(true);
    setError(null);
    setCommandError(null);
    try {
      const next = await loadConsoleSnapshot(candidate);
      writeStoredSession(candidate);
      setSession(candidate);
      applySnapshot(next);
    } catch (cause) {
      setError(errorMessage(cause));
      throw cause;
    } finally {
      setLoading(false);
    }
  }, [applySnapshot]);

  const disconnect = useCallback(() => {
    clearStoredSession();
    setSession(null);
    setActor(null);
    setSnapshot(null);
    setError(null);
    setCommandError(null);
    setOperationsError(null);
  }, []);

  const refresh = useCallback(async (): Promise<void> => {
    if (!session) return;
    setCommandError(null);
    await load(session);
  }, [load, session]);

  const searchOperations = useCallback(async (query: string, scope: OperationScope): Promise<void> => {
    if (!session || !snapshot) {
      setOperationsError("Owner ConsoleをControl Planeへ接続してください。");
      return;
    }
    setOperationsLoading(true);
    setOperationsError(null);
    try {
      const operations = await rcaoApi.searchOperations(session, query, scope);
      setSnapshot((current) => current ? { ...current, operations } : current);
    } catch (cause) {
      setOperationsError(errorMessage(cause));
    } finally {
      setOperationsLoading(false);
    }
  }, [session, snapshot]);

  const executeCommand = useCallback(async (command: (currentSession: ConsoleSession) => Promise<unknown>): Promise<void> => {
    setCommandError(null);
    if (!session) {
      setCommandError("Owner ConsoleをControl Planeへ接続してください。");
      return;
    }
    try {
      await command(session);
      await load(session);
    } catch (cause) {
      setCommandError(errorMessage(cause));
    }
  }, [load, session]);

  const clearCommandError = useCallback(() => setCommandError(null), []);

  const createTask = useCallback((input: CreateTaskInput) => executeCommand((currentSession) => rcaoApi.createTask(currentSession, input)), [executeCommand]);
  const setTaskStatus = useCallback((taskId: string, status: MvpTask["status"], reason = "Owner Console action") => executeCommand((currentSession) => rcaoApi.setTaskStatus(currentSession, taskId, status, reason)), [executeCommand]);
  const evaluateTask = useCallback((taskId: string) => executeCommand((currentSession) => rcaoApi.evaluateTask(currentSession, taskId)), [executeCommand]);
  const decideApproval = useCallback((approvalId: string, decision: ApprovalDecision, comment = "") => executeCommand((currentSession) => rcaoApi.decideApproval(currentSession, approvalId, decision, comment)), [executeCommand]);
  const approveReward = useCallback((rewardId: string, amount: number, comment = "Owner approved virtual Reward") => executeCommand((currentSession) => rcaoApi.approveReward(currentSession, rewardId, amount, comment)), [executeCommand]);
  const setAgentStatus = useCallback((agentId: string, status: MvpAgent["status"], reason = "Owner Console action") => executeCommand((currentSession) => rcaoApi.setAgentStatus(currentSession, agentId, status, reason)), [executeCommand]);

  const value = useMemo<MvpContextValue>(() => ({
    actor,
    session,
    dashboard: snapshot?.dashboard ?? null,
    connected: Boolean(session && actor),
    loading,
    error,
    commandError,
    operationsLoading,
    operationsError,
    agents: snapshot?.agents ?? [],
    tasks: snapshot?.tasks ?? [],
    subtasks: snapshot?.subtasks ?? [],
    reviews: snapshot?.reviews ?? [],
    audits: snapshot?.audits ?? [],
    evaluations: snapshot?.evaluations ?? [],
    rewards: snapshot?.rewards ?? [],
    approvals: snapshot?.approvals ?? [],
    proposals: snapshot?.proposals ?? [],
    externalActions: snapshot?.externalActions ?? [],
    auditLogs: snapshot?.auditLogs ?? [],
    operations: snapshot?.operations ?? [],
    connect,
    disconnect,
    refresh,
    searchOperations,
    clearCommandError,
    createTask,
    setTaskStatus,
    evaluateTask,
    decideApproval,
    approveReward,
    setAgentStatus,
  }), [
    actor,
    session,
    snapshot,
    loading,
    error,
    commandError,
    operationsLoading,
    operationsError,
    connect,
    disconnect,
    refresh,
    searchOperations,
    clearCommandError,
    createTask,
    setTaskStatus,
    evaluateTask,
    decideApproval,
    approveReward,
    setAgentStatus,
  ]);

  return <MvpContext.Provider value={value}>{children}</MvpContext.Provider>;
}

export function useMvp(): MvpContextValue {
  const context = useContext(MvpContext);
  if (!context) throw new Error("useMvp must be used inside MvpProvider");
  return context;
}
