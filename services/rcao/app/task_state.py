from .models import AgentRole, Task, TaskState
from .policy import PolicyViolation, authorize_task_transition


TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.DRAFT: {TaskState.ISSUED, TaskState.CANCELLED},
    TaskState.ISSUED: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
    TaskState.IN_PROGRESS: {TaskState.IN_REVIEW, TaskState.CANCELLED},
    TaskState.IN_REVIEW: {
        TaskState.ACCEPTED,
        TaskState.REJECTED,
        TaskState.CANCELLED,
    },
    TaskState.ACCEPTED: {TaskState.REWARDED, TaskState.CANCELLED},
    TaskState.REJECTED: {TaskState.IN_PROGRESS, TaskState.CANCELLED},
    TaskState.REWARDED: set(),
    TaskState.CANCELLED: set(),
}


def transition_task(task: Task, to_state: TaskState, actor_role: AgentRole) -> Task:
    if to_state not in TRANSITIONS[task.state]:
        raise PolicyViolation(f"Invalid Task transition {task.state} -> {to_state}")
    authorize_task_transition(actor_role, task.state, to_state)
    return task.model_copy(update={"state": to_state})

