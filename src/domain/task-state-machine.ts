import type { AgentRole, Task, TaskState } from "./model";
import { authorizeTaskTransition, PolicyViolation } from "./policy";

const transitions: Record<TaskState, readonly TaskState[]> = {
  DRAFT: ["ISSUED", "CANCELLED"],
  ISSUED: ["IN_PROGRESS", "CANCELLED"],
  IN_PROGRESS: ["IN_REVIEW", "CANCELLED"],
  IN_REVIEW: ["ACCEPTED", "REJECTED", "CANCELLED"],
  ACCEPTED: ["REWARDED", "CANCELLED"],
  REJECTED: ["IN_PROGRESS", "CANCELLED"],
  REWARDED: [],
  CANCELLED: [],
};

export function transitionTask(task: Task, to: TaskState, actorRole: AgentRole): Task {
  if (!transitions[task.state].includes(to)) {
    throw new PolicyViolation(`Invalid Task transition ${task.state} -> ${to}`);
  }

  authorizeTaskTransition(actorRole, task.state, to);
  return { ...task, state: to };
}
