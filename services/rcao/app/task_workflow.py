"""PostgreSQL-backed Owner Task command workflow.

The MVP store remains useful as a deterministic UI fixture, but it is not the
source of truth for commands once this service is composed into the control
plane.  Every method below executes inside one ``PostgresRepository`` unit of
work and records the resulting Audit and Outbox event before committing.

This module deliberately contains no provider, wallet, or external API call.
Those effects are downstream of the durable command boundary and can only be
triggered by consuming the Outbox after the transaction commits.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from .agent_registry import (
    AgentRegistryPolicy,
    AgentRegistryRepository,
)
from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter
from .auth import ActorContext, ActorType
from .mvp import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalType,
    AuditCommand,
    AuditRecord,
    AuditResult,
    OwnerEvaluation,
    OwnerEvaluationCommand,
    ReviewCommand,
    ReviewRecord,
    SubTaskCreateCommand,
    SubTaskRecord,
    TaskCreateCommand,
    TaskRecord,
    TaskStatus,
)
from .repository import PostgresRepository, RepositoryTransaction


class TaskWorkflowError(ValueError):
    """Base error for rejected persistent Task commands."""


class WorkflowAuthorizationError(TaskWorkflowError):
    """The actor is outside the command boundary."""


class InvalidTaskTransition(TaskWorkflowError):
    """The requested status transition is not part of the workflow."""


class WorkflowConflict(TaskWorkflowError):
    """The command conflicts with an existing durable state."""


class WorkflowNotReady(TaskWorkflowError):
    """A prerequisite such as review, audit, or Owner Evaluation is missing."""


TASK_RECORD_COLUMNS = (
    "id",
    "title",
    "objective",
    "background",
    "priority",
    "deadline",
    "acceptance_criteria",
    "reward_budget_lamports",
    "assigned_executive_agent_id",
    "risk_level",
    "external_action_allowed",
    "owner_approval_required",
    "status",
    "progress",
    "created_by",
    "created_at",
    "updated_at",
    "version",
)


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _as_utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _state(task: "PersistedTask") -> dict[str, Any]:
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": task.progress,
        "version": task.version,
        "assigned_executive_agent_id": task.assigned_executive_agent_id,
        "reward_budget_lamports": task.reward_budget_lamports,
        "acceptance_criteria": list(task.acceptance_criteria),
    }


@dataclass(frozen=True)
class PersistedTask:
    """Task row including the optimistic-concurrency version."""

    id: str
    title: str
    objective: str
    background: str
    priority: str
    deadline: datetime | str
    acceptance_criteria: tuple[str, ...]
    reward_budget_lamports: int
    assigned_executive_agent_id: str
    risk_level: str
    external_action_allowed: bool
    owner_approval_required: bool
    status: str
    progress: int
    created_by: str
    created_at: datetime | str
    updated_at: datetime | str
    version: int

    @classmethod
    def from_record(cls, row: Any) -> "PersistedTask":
        values = (
            dict(row)
            if isinstance(row, Mapping)
            else dict(zip(TASK_RECORD_COLUMNS, row, strict=True))
        )
        criteria = _json_value(values["acceptance_criteria"], [])
        if not isinstance(criteria, list) or not all(isinstance(item, str) for item in criteria):
            raise TaskWorkflowError("acceptance_criteria must be a list of strings")
        return cls(
            id=str(values["id"]),
            title=str(values["title"]),
            objective=str(values["objective"]),
            background=str(values["background"] or ""),
            priority=str(values["priority"]),
            deadline=values["deadline"],
            acceptance_criteria=tuple(criteria),
            reward_budget_lamports=int(values["reward_budget_lamports"]),
            assigned_executive_agent_id=str(values["assigned_executive_agent_id"]),
            risk_level=str(values["risk_level"]),
            external_action_allowed=bool(values["external_action_allowed"]),
            owner_approval_required=bool(values["owner_approval_required"]),
            status=str(values["status"]),
            progress=int(values["progress"]),
            created_by=str(values["created_by"]),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
            version=int(values["version"]),
        )

    def to_model(self) -> TaskRecord:
        return TaskRecord(
            id=self.id,
            title=self.title,
            objective=self.objective,
            background=self.background,
            priority=self.priority,
            deadline=_as_utc(self.deadline),
            acceptance_criteria=list(self.acceptance_criteria),
            reward_budget_lamports=self.reward_budget_lamports,
            assigned_executive_agent_id=self.assigned_executive_agent_id,
            risk_level=self.risk_level,
            external_action_allowed=self.external_action_allowed,
            owner_approval_required=self.owner_approval_required,
            status=self.status,
            progress=self.progress,
            created_by=self.created_by,
            created_at=_as_utc(self.created_at),
            updated_at=_as_utc(self.updated_at),
        )


ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"APPROVED", "CANCELLED"}),
    "APPROVED": frozenset({"PLANNING", "CANCELLED"}),
    "PLANNING": frozenset({"IN_PROGRESS", "BLOCKED", "CANCELLED"}),
    "IN_PROGRESS": frozenset({"REVIEW", "BLOCKED", "CANCELLED"}),
    "REVIEW": frozenset({"AUDIT", "REWORK", "BLOCKED"}),
    "AUDIT": frozenset({"OWNER_REVIEW", "REWORK", "BLOCKED"}),
    "OWNER_REVIEW": frozenset({"COMPLETED", "REWORK", "REJECTED", "BLOCKED"}),
    "REWORK": frozenset({"IN_PROGRESS", "CANCELLED", "BLOCKED"}),
    "BLOCKED": frozenset({"IN_PROGRESS", "CANCELLED"}),
    "COMPLETED": frozenset(),
    "REJECTED": frozenset(),
    "CANCELLED": frozenset(),
}

OWNER_TRANSITION_TARGETS = frozenset(
    {"APPROVED", "CANCELLED", "COMPLETED", "REJECTED", "REWORK"}
)


class TaskWorkflowRepository:
    """Command handlers operating on one ``RepositoryTransaction``."""

    def __init__(self, transaction: RepositoryTransaction, *, owner_id: str = "owner-local") -> None:
        self.transaction = transaction
        self.owner_id = owner_id

    def _fetch_task(self, task_id: str, *, for_update: bool = False) -> PersistedTask:
        lock = " FOR UPDATE" if for_update else ""
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(TASK_RECORD_COLUMNS)}
            FROM mvp_tasks
            WHERE id = %s{lock}
            """,
            (task_id,),
        )
        if row is None:
            raise TaskWorkflowError(f"task is not registered: {task_id}")
        return PersistedTask.from_record(row)

    def _require_owner(self, actor: ActorContext) -> None:
        if (
            actor.actor_type is not ActorType.OWNER
            or actor.actor_id != self.owner_id
            or actor.role.value != "OWNER"
        ):
            raise WorkflowAuthorizationError("canonical Owner authority is required")

    def _require_registered_agent(self, agent_id: str):
        agent = AgentRegistryRepository(self.transaction).require_agent(agent_id)
        AgentRegistryPolicy.ensure_active(agent)
        return agent

    def _require_agent_member(
        self,
        actor: ActorContext,
        task: PersistedTask,
        *,
        action: str,
        required_role: str | None = None,
        required_agent_type: str | None = None,
    ):
        if actor.actor_type is not ActorType.AGENT:
            raise WorkflowAuthorizationError("a registered Agent identity is required")
        registry = AgentRegistryRepository(self.transaction)
        agent = registry.ensure_can_participate(
            actor.actor_id,
            task_id=task.id,
            action=action,
            risk_level=task.risk_level,
        )
        if required_role is not None and agent.role != required_role:
            raise WorkflowAuthorizationError(f"{required_role} Agent authority is required")
        if required_agent_type is not None and agent.agent_type != required_agent_type:
            raise WorkflowAuthorizationError(f"{required_agent_type} Agent authority is required")
        return agent

    def _fingerprint(self, command_name: str, actor: ActorContext, request: Any) -> str:
        payload = {
            "command_name": command_name,
            "actor_id": actor.actor_id,
            "request": request,
        }
        encoded = json.dumps(
            payload,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _claim_idempotency(
        self,
        *,
        key: str,
        command_name: str,
        actor: ActorContext,
        request: Any,
    ) -> dict[str, Any] | None:
        fingerprint = self._fingerprint(command_name, actor, request)
        inserted = self.transaction.fetch_one(
            """
            INSERT INTO mvp_command_idempotency
              (idempotency_key, command_name, actor_id, request_fingerprint)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING idempotency_key
            """,
            (key, command_name, actor.actor_id, fingerprint),
        )
        if inserted is not None:
            return None
        row = self.transaction.fetch_one(
            """
            SELECT command_name, actor_id, request_fingerprint, response
            FROM mvp_command_idempotency
            WHERE idempotency_key = %s
            FOR UPDATE
            """,
            (key,),
        )
        if row is None:
            raise WorkflowConflict("idempotency record is not available")
        command_name_value = str(_row_value(row, "command_name", 0))
        actor_id = str(_row_value(row, "actor_id", 1))
        existing_fingerprint = _row_value(row, "request_fingerprint", 2)
        if command_name_value != command_name or actor_id != actor.actor_id:
            raise WorkflowConflict("idempotency key is bound to another command or actor")
        if not existing_fingerprint or str(existing_fingerprint).startswith("legacy-unfingerprinted:"):
            raise WorkflowConflict("idempotency record predates request fingerprinting")
        if str(existing_fingerprint) != fingerprint:
            raise WorkflowConflict("idempotency key is bound to a different request")
        response = _row_value(row, "response", 3)
        if response is None:
            raise WorkflowConflict("idempotent command has not completed")
        return json.loads(response) if isinstance(response, str) else response

    def _complete_idempotency(self, key: str, response: Any) -> None:
        self.transaction.execute(
            """
            UPDATE mvp_command_idempotency
            SET response = %s::jsonb, completed_at = now()
            WHERE idempotency_key = %s
            """,
            (json.dumps(response, default=str, sort_keys=True), key),
        )

    def _emit(
        self,
        *,
        actor: ActorContext,
        action: str,
        target_type: str,
        target_id: str,
        task_id: str | None,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        reason: str,
        idempotency_key: str,
    ) -> None:
        correlation_id = f"corr-{uuid4().hex}"
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                event_version=1,
                event_type="TASK_WORKFLOW_COMMAND",
                actor_id=actor.actor_id,
                actor_type=actor.actor_type.value,
                action=action,
                target_type=target_type,
                target_id=target_id,
                before_state=before,
                after_state=after,
                policy_result="ALLOW",
                reason=reason,
                correlation_id=correlation_id,
                transaction_id=correlation_id,
                task_id=task_id,
            ),
        )
        OutboxWriter.enqueue(
            self.transaction,
            OutboxEvent(
                event_id=f"outbox-{uuid4().hex}",
                aggregate_type=target_type,
                aggregate_id=target_id,
                event_type=action,
                idempotency_key=idempotency_key,
                payload={
                    "action": action,
                    "target_type": target_type,
                    "target_id": target_id,
                    "task_id": task_id,
                    "actor_id": actor.actor_id,
                    "before": dict(before),
                    "after": dict(after),
                },
                event_version=1,
                transaction_id=correlation_id,
            ),
        )

    def _update_task(
        self,
        task: PersistedTask,
        *,
        status: str,
        progress: int | None = None,
    ) -> PersistedTask:
        row = self.transaction.fetch_one(
            f"""
            UPDATE mvp_tasks
            SET status = %s::mvp_task_status,
                progress = %s,
                version = version + 1,
                updated_at = now()
            WHERE id = %s AND version = %s
            RETURNING {', '.join(TASK_RECORD_COLUMNS)}
            """,
            (
                status,
                task.progress if progress is None else progress,
                task.id,
                task.version,
            ),
        )
        if row is None:
            raise WorkflowConflict(f"task changed during command: {task.id}")
        return PersistedTask.from_record(row)

    def create_task(
        self,
        actor: ActorContext,
        command: TaskCreateCommand,
        *,
        idempotency_key: str | None = None,
    ) -> TaskRecord:
        self._require_owner(actor)
        key = idempotency_key or f"task-create-{uuid4().hex}"
        request = command.model_dump(mode="json")
        replay = self._claim_idempotency(
            key=key,
            command_name="CREATE_TASK",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return TaskRecord.model_validate(replay)

        executive = self._require_registered_agent(command.assigned_executive_agent_id)
        if executive.agent_type != "EXECUTIVE":
            raise WorkflowAuthorizationError("Task must be assigned to an Executive Agent")
        now = datetime.now(timezone.utc)
        task_id = f"T-{uuid4().hex}"
        risk = command.risk_level.value
        self.transaction.execute(
            """
            INSERT INTO mvp_tasks
              (id, title, objective, background, priority, deadline,
               acceptance_criteria, reward_budget_lamports,
               assigned_executive_agent_id, risk_level, external_action_allowed,
               owner_approval_required, status, progress, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s,
                    %s, TRUE, 'DRAFT', 0, %s)
            """,
            (
                task_id,
                command.title,
                command.objective,
                command.background,
                command.priority.value,
                command.deadline,
                json.dumps(command.acceptance_criteria),
                command.reward_budget_lamports,
                command.assigned_executive_agent_id,
                risk,
                command.external_action_allowed,
                actor.actor_id,
            ),
        )
        self.transaction.execute(
            """
            INSERT INTO reward_budgets (task_id, amount_lamports, defined_by)
            VALUES (%s, %s, %s)
            """,
            (task_id, command.reward_budget_lamports, actor.actor_id),
        )
        allocation_id = str(uuid4())
        self.transaction.execute(
            """
            INSERT INTO reward_allocations
              (id, task_id, agent_id, reward_budget_lamports)
            VALUES (%s, %s, %s, %s)
            """,
            (allocation_id, task_id, command.assigned_executive_agent_id, command.reward_budget_lamports),
        )
        self.transaction.execute(
            """
            INSERT INTO mvp_task_assignments (task_id, agent_id, assigned_by)
            VALUES (%s, %s, %s)
            """,
            (task_id, command.assigned_executive_agent_id, actor.actor_id),
        )
        self.transaction.execute(
            """
            INSERT INTO mvp_agent_memberships
              (task_id, agent_id, membership_role, assigned_by)
            VALUES (%s, %s, 'EXECUTIVE', %s)
            """,
            (task_id, command.assigned_executive_agent_id, actor.actor_id),
        )
        self.transaction.execute(
            """
            INSERT INTO mvp_task_acceptance_history
              (id, task_id, acceptance_criteria, changed_by, change_type, reason)
            VALUES (%s, %s, %s::jsonb, %s, 'INITIAL', %s)
            """,
            (
                f"criteria-{uuid4().hex}",
                task_id,
                json.dumps(command.acceptance_criteria),
                actor.actor_id,
                "Initial acceptance criteria fixed by Owner",
            ),
        )
        task = self._fetch_task(task_id)
        self._emit(
            actor=actor,
            action="CREATE_TASK",
            target_type="TASK",
            target_id=task_id,
            task_id=task_id,
            before={},
            after=_state(task),
            reason="Owner created a persistent draft Task",
            idempotency_key=key,
        )
        model = task.to_model()
        self._complete_idempotency(key, model.model_dump(mode="json"))
        return model

    def update_acceptance_criteria(
        self,
        actor: ActorContext,
        task_id: str,
        acceptance_criteria: list[str],
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> TaskRecord:
        self._require_owner(actor)
        if not acceptance_criteria or not all(isinstance(item, str) and item for item in acceptance_criteria):
            raise TaskWorkflowError("acceptance_criteria must contain at least one non-empty string")
        key = idempotency_key or f"criteria-{uuid4().hex}"
        request = {"task_id": task_id, "acceptance_criteria": acceptance_criteria, "reason": reason}
        replay = self._claim_idempotency(
            key=key,
            command_name="UPDATE_ACCEPTANCE_CRITERIA",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return TaskRecord.model_validate(replay)
        task = self._fetch_task(task_id, for_update=True)
        if task.status != "DRAFT":
            raise WorkflowConflict("Acceptance Criteria can only change while Task is DRAFT")
        before = _state(task)
        self.transaction.execute(
            """
            UPDATE mvp_tasks
            SET acceptance_criteria = %s::jsonb, version = version + 1, updated_at = now()
            WHERE id = %s AND version = %s
            """,
            (json.dumps(acceptance_criteria), task_id, task.version),
        )
        self.transaction.execute(
            """
            INSERT INTO mvp_task_acceptance_history
              (id, task_id, acceptance_criteria, changed_by, change_type, reason)
            VALUES (%s, %s, %s::jsonb, %s, 'AMENDMENT', %s)
            """,
            (f"criteria-{uuid4().hex}", task_id, json.dumps(acceptance_criteria), actor.actor_id, reason),
        )
        updated = self._fetch_task(task_id)
        self._emit(
            actor=actor,
            action="UPDATE_ACCEPTANCE_CRITERIA",
            target_type="TASK",
            target_id=task_id,
            task_id=task_id,
            before=before,
            after=_state(updated),
            reason=reason,
            idempotency_key=key,
        )
        model = updated.to_model()
        self._complete_idempotency(key, model.model_dump(mode="json"))
        return model

    def assign_executive(
        self,
        actor: ActorContext,
        task_id: str,
        executive_agent_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> TaskRecord:
        self._require_owner(actor)
        key = idempotency_key or f"assign-{uuid4().hex}"
        request = {"task_id": task_id, "executive_agent_id": executive_agent_id}
        replay = self._claim_idempotency(
            key=key,
            command_name="ASSIGN_EXECUTIVE",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return TaskRecord.model_validate(replay)
        task = self._fetch_task(task_id, for_update=True)
        if task.status != "DRAFT":
            raise WorkflowConflict("Executive assignment can only change while Task is DRAFT")
        executive = self._require_registered_agent(executive_agent_id)
        if executive.agent_type != "EXECUTIVE":
            raise WorkflowAuthorizationError("only an Executive Agent can receive an Owner Task")
        before = _state(task)
        self.transaction.execute(
            """
            UPDATE mvp_tasks
            SET assigned_executive_agent_id = %s, version = version + 1, updated_at = now()
            WHERE id = %s AND version = %s
            """,
            (executive_agent_id, task_id, task.version),
        )
        self.transaction.execute(
            """
            UPDATE mvp_agent_memberships
            SET active = FALSE
            WHERE task_id = %s AND agent_id <> %s
            """,
            (task_id, executive_agent_id),
        )
        self.transaction.execute(
            """
            INSERT INTO mvp_task_assignments (task_id, agent_id, assigned_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (task_id, agent_id) DO UPDATE SET assigned_by = EXCLUDED.assigned_by
            """,
            (task_id, executive_agent_id, actor.actor_id),
        )
        self.transaction.execute(
            """
            INSERT INTO mvp_agent_memberships
              (task_id, agent_id, membership_role, assigned_by, active)
            VALUES (%s, %s, 'EXECUTIVE', %s, TRUE)
            ON CONFLICT (task_id, agent_id) DO UPDATE SET
              membership_role = EXCLUDED.membership_role,
              assigned_by = EXCLUDED.assigned_by,
              active = TRUE,
              expires_at = NULL
            """,
            (task_id, executive_agent_id, actor.actor_id),
        )
        self.transaction.execute(
            """
            UPDATE reward_allocations
            SET agent_id = %s
            WHERE task_id = %s AND status = 'Pending'::mvp_reward_status
            """,
            (executive_agent_id, task_id),
        )
        updated = self._fetch_task(task_id)
        self._emit(
            actor=actor,
            action="ASSIGN_EXECUTIVE",
            target_type="TASK",
            target_id=task_id,
            task_id=task_id,
            before=before,
            after=_state(updated),
            reason="Owner assigned the Executive Agent",
            idempotency_key=key,
        )
        model = updated.to_model()
        self._complete_idempotency(key, model.model_dump(mode="json"))
        return model

    def create_subtask(
        self,
        actor: ActorContext,
        task_id: str,
        command: SubTaskCreateCommand,
        *,
        idempotency_key: str | None = None,
    ) -> SubTaskRecord:
        key = idempotency_key or f"subtask-{uuid4().hex}"
        request = {"task_id": task_id, **command.model_dump(mode="json")}
        replay = self._claim_idempotency(
            key=key,
            command_name="CREATE_SUBTASK",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return SubTaskRecord.model_validate(replay)
        task = self._fetch_task(task_id, for_update=True)
        if actor.actor_type is ActorType.OWNER:
            self._require_owner(actor)
        else:
            executive = self._require_agent_member(
                actor,
                task,
                action="CREATE_SUBTASK",
                required_agent_type="EXECUTIVE",
            )
            if executive.agent_id != task.assigned_executive_agent_id:
                raise WorkflowAuthorizationError("assigned Executive authority is required")
        assigned = self._require_registered_agent(command.assigned_agent_id)
        if assigned.agent_type == "EXECUTIVE" and assigned.agent_id != task.assigned_executive_agent_id:
            raise WorkflowAuthorizationError("Sub Task cannot silently reassign the Owner Task")
        membership = AgentRegistryRepository(self.transaction).get_membership(task_id, assigned.agent_id)
        if membership is None:
            if actor.actor_type is not ActorType.OWNER:
                raise WorkflowAuthorizationError("Owner must assign an Agent to the Task before subtask creation")
            self.transaction.execute(
                """
                INSERT INTO mvp_agent_memberships
                  (task_id, agent_id, membership_role, assigned_by)
                VALUES (%s, %s, 'SUB_TASK', %s)
                """,
                (task_id, assigned.agent_id, actor.actor_id),
            )
        elif (
            not membership.active
            or (
                membership.expires_at is not None
                and _as_utc(membership.expires_at) <= datetime.now(timezone.utc)
            )
        ):
            if actor.actor_type is not ActorType.OWNER:
                raise WorkflowAuthorizationError("Agent Task membership is inactive or expired")
            self.transaction.execute(
                """
                UPDATE mvp_agent_memberships
                SET active = TRUE, assigned_by = %s, expires_at = NULL
                WHERE task_id = %s AND agent_id = %s
                """,
                (actor.actor_id, task_id, assigned.agent_id),
            )
        subtask_id = f"ST-{uuid4().hex}"
        self.transaction.execute(
            """
            INSERT INTO mvp_sub_tasks
              (id, parent_task_id, title, description, assigned_agent_id, dependencies)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                subtask_id,
                task_id,
                command.title,
                command.description,
                assigned.agent_id,
                json.dumps(command.dependencies),
            ),
        )
        row = self.transaction.fetch_one(
            """
            SELECT id, parent_task_id, title, description, assigned_agent_id,
                   status, progress, dependencies, artifact, review_result,
                   audit_result, created_at, updated_at
            FROM mvp_sub_tasks WHERE id = %s
            """,
            (subtask_id,),
        )
        if row is None:
            raise TaskWorkflowError("Sub Task was not persisted")
        subtask = _subtask_from_row(row)
        self._emit(
            actor=actor,
            action="CREATE_SUBTASK",
            target_type="SUB_TASK",
            target_id=subtask_id,
            task_id=task_id,
            before={},
            after=subtask.model_dump(mode="json"),
            reason="Sub Task created inside the persistent Owner Task boundary",
            idempotency_key=key,
        )
        self._complete_idempotency(key, subtask.model_dump(mode="json"))
        return subtask

    def transition_task(
        self,
        actor: ActorContext,
        task_id: str,
        to_status: TaskStatus,
        *,
        progress: int | None = None,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> TaskRecord:
        key = idempotency_key or f"transition-{uuid4().hex}"
        request = {
            "task_id": task_id,
            "to_status": to_status.value,
            "progress": progress,
            "reason": reason,
        }
        replay = self._claim_idempotency(
            key=key,
            command_name="TRANSITION_TASK",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return TaskRecord.model_validate(replay)
        task = self._fetch_task(task_id, for_update=True)
        target = to_status.value
        if target not in ALLOWED_TRANSITIONS.get(task.status, frozenset()):
            raise InvalidTaskTransition(f"Invalid Task transition {task.status} -> {target}")
        if target in OWNER_TRANSITION_TARGETS:
            self._require_owner(actor)
            if target == "COMPLETED":
                self._require_owner_evaluation(task_id)
        elif target in {"PLANNING", "IN_PROGRESS", "REVIEW"}:
            agent = self._require_agent_member(
                actor,
                task,
                action="TRANSITION_TASK",
                required_agent_type="EXECUTIVE",
            )
            if agent.agent_id != task.assigned_executive_agent_id:
                raise WorkflowAuthorizationError("assigned Executive authority is required")
        elif target == "AUDIT":
            auditor = self._require_agent_member(actor, task, action="TRANSITION_TASK")
            if auditor.role != "REVIEWER":
                raise WorkflowAuthorizationError("independent Reviewer authority is required")
            if auditor.agent_id == task.assigned_executive_agent_id:
                raise WorkflowAuthorizationError("Reviewer must be independent from the Task executor")
        elif target == "OWNER_REVIEW":
            auditor = self._require_agent_member(actor, task, action="TRANSITION_TASK")
            if auditor.role != "AUDITOR":
                raise WorkflowAuthorizationError("independent Auditor authority is required")
            if auditor.agent_id == task.assigned_executive_agent_id:
                raise WorkflowAuthorizationError("Auditor must be independent from the Task executor")
        elif target == "BLOCKED":
            if actor.actor_type is ActorType.OWNER:
                self._require_owner(actor)
            elif actor.actor_id == task.assigned_executive_agent_id:
                self._require_agent_member(
                    actor,
                    task,
                    action="TRANSITION_TASK",
                    required_agent_type="EXECUTIVE",
                )
            else:
                auditor = self._require_agent_member(actor, task, action="TRANSITION_TASK")
                if auditor.role != "AUDITOR":
                    raise WorkflowAuthorizationError("Owner, assigned Executive, or Auditor authority is required")
        before = _state(task)
        updated = self._update_task(
            task,
            status=target,
            progress=100 if target == "COMPLETED" else progress,
        )
        self._emit(
            actor=actor,
            action="TRANSITION_TASK",
            target_type="TASK",
            target_id=task_id,
            task_id=task_id,
            before=before,
            after=_state(updated),
            reason=reason or f"Task moved to {target}",
            idempotency_key=key,
        )
        model = updated.to_model()
        self._complete_idempotency(key, model.model_dump(mode="json"))
        return model

    def submit_evidence(
        self,
        actor: ActorContext,
        task_id: str,
        sub_task_id: str,
        uri: str,
        *,
        content_hash: str | None = None,
        idempotency_key: str | None = None,
    ) -> SubTaskRecord:
        key = idempotency_key or f"evidence-{uuid4().hex}"
        request = {
            "task_id": task_id,
            "sub_task_id": sub_task_id,
            "uri": uri,
            "content_hash": content_hash,
        }
        replay = self._claim_idempotency(
            key=key,
            command_name="SUBMIT_EVIDENCE",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return SubTaskRecord.model_validate(replay)
        task = self._fetch_task(task_id, for_update=True)
        agent = self._require_agent_member(actor, task, action="SUBMIT_EVIDENCE")
        row = self.transaction.fetch_one(
            """
            SELECT id, parent_task_id, title, description, assigned_agent_id,
                   status, progress, dependencies, artifact, review_result,
                   audit_result, created_at, updated_at
            FROM mvp_sub_tasks WHERE id = %s AND parent_task_id = %s FOR UPDATE
            """,
            (sub_task_id, task_id),
        )
        if row is None:
            raise TaskWorkflowError("Sub Task is not part of the requested Task")
        subtask = _subtask_from_row(row)
        if subtask.assigned_agent_id != agent.agent_id:
            raise WorkflowAuthorizationError("only the assigned Agent can submit Sub Task evidence")
        self.transaction.execute(
            """
            INSERT INTO task_artifacts (id, task_id, sub_task_id, uri, content_hash, submitted_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (str(uuid4()), task_id, sub_task_id, uri, content_hash, actor.actor_id),
        )
        self.transaction.execute(
            """
            UPDATE mvp_sub_tasks
            SET artifact = %s, status = 'COMPLETED', progress = 100, updated_at = now()
            WHERE id = %s
            """,
            (uri, sub_task_id),
        )
        updated_row = self.transaction.fetch_one(
            """
            SELECT id, parent_task_id, title, description, assigned_agent_id,
                   status, progress, dependencies, artifact, review_result,
                   audit_result, created_at, updated_at
            FROM mvp_sub_tasks WHERE id = %s
            """,
            (sub_task_id,),
        )
        if updated_row is None:
            raise TaskWorkflowError("Sub Task evidence update was not persisted")
        updated = _subtask_from_row(updated_row)
        self._emit(
            actor=actor,
            action="SUBMIT_EVIDENCE",
            target_type="SUB_TASK",
            target_id=sub_task_id,
            task_id=task_id,
            before=subtask.model_dump(mode="json"),
            after=updated.model_dump(mode="json"),
            reason="Evidence was submitted by the assigned Agent",
            idempotency_key=key,
        )
        self._complete_idempotency(key, updated.model_dump(mode="json"))
        return updated

    def submit_review(
        self,
        actor: ActorContext,
        task_id: str,
        command: ReviewCommand,
        *,
        idempotency_key: str | None = None,
    ) -> ReviewRecord:
        key = idempotency_key or f"review-{uuid4().hex}"
        request = {"task_id": task_id, **command.model_dump(mode="json")}
        replay = self._claim_idempotency(
            key=key,
            command_name="SUBMIT_REVIEW",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return ReviewRecord.model_validate(replay)
        task = self._fetch_task(task_id, for_update=True)
        if task.status != "REVIEW":
            raise InvalidTaskTransition("Review can only be submitted from REVIEW")
        reviewer = self._require_agent_member(actor, task, action="SUBMIT_REVIEW", required_role="REVIEWER")
        if reviewer.agent_id == task.assigned_executive_agent_id:
            raise WorkflowAuthorizationError("Reviewer must be independent from the Task executor")
        review_id = str(uuid4())
        self.transaction.execute(
            """
            INSERT INTO mvp_reviews
              (id, task_id, reviewer, quality, completeness, correctness,
               required_changes, comment)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                review_id,
                task_id,
                actor.actor_id,
                command.quality,
                command.completeness,
                command.correctness,
                json.dumps(command.required_changes),
                command.comment,
            ),
        )
        before = _state(task)
        updated = self._update_task(task, status="REWORK" if command.required_changes else "AUDIT")
        row = self.transaction.fetch_one(
            """
            SELECT task_id, reviewer, quality, completeness, correctness,
                   required_changes, comment, reviewed_at
            FROM mvp_reviews WHERE id = %s
            """,
            (review_id,),
        )
        if row is None:
            raise TaskWorkflowError("Review was not persisted")
        review = _review_from_row(row)
        self._emit(
            actor=actor,
            action="SUBMIT_REVIEW",
            target_type="TASK",
            target_id=task_id,
            task_id=task_id,
            before=before,
            after={**_state(updated), "review_id": review_id},
            reason="Independent review recorded",
            idempotency_key=key,
        )
        self._complete_idempotency(key, review.model_dump(mode="json"))
        return review

    def record_audit(
        self,
        actor: ActorContext,
        task_id: str,
        command: AuditCommand,
        *,
        idempotency_key: str | None = None,
    ) -> AuditRecord:
        key = idempotency_key or f"audit-{uuid4().hex}"
        request = {"task_id": task_id, **command.model_dump(mode="json")}
        replay = self._claim_idempotency(
            key=key,
            command_name="RECORD_AUDIT",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return AuditRecord.model_validate(replay)
        task = self._fetch_task(task_id, for_update=True)
        if task.status != "AUDIT":
            raise InvalidTaskTransition("Audit can only be recorded from AUDIT")
        auditor = self._require_agent_member(actor, task, action="RECORD_AUDIT", required_role="AUDITOR")
        if auditor.agent_id == task.assigned_executive_agent_id:
            raise WorkflowAuthorizationError("Auditor must be independent from the Task executor")
        audit_id = str(uuid4())
        self.transaction.execute(
            """
            INSERT INTO mvp_audits
              (id, task_id, auditor, policy_compliance, security_risk,
               external_action_check, reward_manipulation_check,
               authority_violation_check, result, comment)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::mvp_audit_result, %s)
            """,
            (
                audit_id,
                task_id,
                actor.actor_id,
                command.policy_compliance,
                command.security_risk.value,
                command.external_action_check,
                command.reward_manipulation_check,
                command.authority_violation_check,
                command.result.value,
                command.comment,
            ),
        )
        before = _state(task)
        next_status = "OWNER_REVIEW" if command.result in {AuditResult.PASS, AuditResult.PASS_WITH_CONDITIONS} else "REWORK"
        updated = self._update_task(task, status=next_status)
        if next_status == "OWNER_REVIEW":
            self.transaction.execute(
                """
                INSERT INTO approval_requests
                  (id, approval_type, target_id, requested_by)
                VALUES (%s, 'TASK_COMPLETION', %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (f"approval-task-{task_id}", task_id, task.assigned_executive_agent_id),
            )
            self.transaction.execute(
                """
                INSERT INTO approval_requests
                  (id, approval_type, target_id, requested_by)
                SELECT %s || id, 'REWARD', id::text, %s
                FROM reward_allocations
                WHERE task_id = %s
                ON CONFLICT (id) DO NOTHING
                """,
                (f"approval-reward-", task.assigned_executive_agent_id, task_id),
            )
        row = self.transaction.fetch_one(
            """
            SELECT task_id, auditor, policy_compliance, security_risk,
                   external_action_check, reward_manipulation_check,
                   authority_violation_check, result, comment, audited_at
            FROM mvp_audits WHERE id = %s
            """,
            (audit_id,),
        )
        if row is None:
            raise TaskWorkflowError("Audit was not persisted")
        audit = _audit_from_row(row)
        self._emit(
            actor=actor,
            action="RECORD_AUDIT",
            target_type="TASK",
            target_id=task_id,
            task_id=task_id,
            before=before,
            after={**_state(updated), "audit_id": audit_id, "audit_result": command.result.value},
            reason="Audit result recorded; FAIL cannot advance to Owner Review",
            idempotency_key=key,
        )
        self._complete_idempotency(key, audit.model_dump(mode="json"))
        return audit

    def evaluate_task(
        self,
        actor: ActorContext,
        task_id: str,
        command: OwnerEvaluationCommand,
        *,
        idempotency_key: str | None = None,
    ) -> OwnerEvaluation:
        self._require_owner(actor)
        key = idempotency_key or f"evaluation-{uuid4().hex}"
        request = {"task_id": task_id, **command.model_dump(mode="json")}
        replay = self._claim_idempotency(
            key=key,
            command_name="OWNER_EVALUATE_TASK",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return OwnerEvaluation.model_validate(replay)
        task = self._fetch_task(task_id, for_update=True)
        if task.status != "OWNER_REVIEW":
            raise WorkflowNotReady("Owner Evaluation requires OWNER_REVIEW")
        if self._has_owner_evaluation(task_id):
            raise WorkflowConflict("Owner Evaluation already exists for the Task")
        evaluation_id = str(uuid4())
        self.transaction.execute(
            """
            INSERT INTO owner_evaluations
              (id, task_id, quality, difficulty, contribution, timeliness,
               rework, strategic_value, owner_comment, evaluated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                evaluation_id,
                task_id,
                command.quality,
                command.difficulty,
                command.contribution,
                command.timeliness,
                command.rework,
                command.strategic_value,
                command.owner_comment,
                actor.actor_id,
            ),
        )
        self.transaction.execute(
            """
            UPDATE reward_allocations
            SET proposed_reward_lamports = reward_budget_lamports * %s / 100,
                status = 'Proposed'::mvp_reward_status
            WHERE task_id = %s AND status = 'Pending'::mvp_reward_status
            """,
            (command.quality, task_id),
        )
        row = self.transaction.fetch_one(
            """
            SELECT task_id, quality, difficulty, contribution, timeliness,
                   rework, strategic_value, owner_comment, evaluated_by, evaluated_at
            FROM owner_evaluations WHERE id = %s
            """,
            (evaluation_id,),
        )
        if row is None:
            raise TaskWorkflowError("Owner Evaluation was not persisted")
        evaluation = _evaluation_from_row(row)
        self._emit(
            actor=actor,
            action="OWNER_EVALUATE_TASK",
            target_type="TASK",
            target_id=task_id,
            task_id=task_id,
            before=_state(task),
            after={**_state(task), "evaluation_id": evaluation_id},
            reason="Owner Evaluation recorded; Reward remains unapproved",
            idempotency_key=key,
        )
        self._complete_idempotency(key, evaluation.model_dump(mode="json"))
        return evaluation

    def decide_approval(
        self,
        actor: ActorContext,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        comment: str = "",
        reward_amount_lamports: int | None = None,
        idempotency_key: str | None = None,
    ) -> ApprovalRequest:
        self._require_owner(actor)
        key = idempotency_key or f"approval-{uuid4().hex}"
        request = {
            "approval_id": approval_id,
            "decision": decision.value,
            "comment": comment,
            "reward_amount_lamports": reward_amount_lamports,
        }
        replay = self._claim_idempotency(
            key=key,
            command_name="DECIDE_APPROVAL",
            actor=actor,
            request=request,
        )
        if replay is not None:
            return ApprovalRequest.model_validate(replay)
        row = self.transaction.fetch_one(
            """
            SELECT id, approval_type, target_id, requested_by, owner_decision,
                   comment, created_at, decided_at
            FROM approval_requests WHERE id = %s FOR UPDATE
            """,
            (approval_id,),
        )
        if row is None:
            raise TaskWorkflowError(f"approval is not registered: {approval_id}")
        approval = _approval_from_row(row)
        if approval.owner_decision is not None:
            raise WorkflowConflict("approval already has an Owner decision")
        if approval.approval_type is ApprovalType.TASK_COMPLETION:
            task = self._fetch_task(approval.target_id, for_update=True)
            if decision is ApprovalDecision.APPROVE:
                self._require_owner_evaluation(task.id)
                updated = self._update_task(task, status="COMPLETED", progress=100)
            elif decision is ApprovalDecision.REQUEST_CHANGES:
                updated = self._update_task(task, status="REWORK")
            elif decision is ApprovalDecision.REJECT:
                updated = self._update_task(task, status="REJECTED")
            else:
                updated = task
            target_type, target_id, task_id = "TASK", task.id, task.id
            before = _state(task)
            after = _state(updated)
        elif approval.approval_type is ApprovalType.REWARD:
            allocation = self.transaction.fetch_one(
                """
                SELECT id, task_id, agent_id, reward_budget_lamports,
                       proposed_reward_lamports, status
                FROM reward_allocations WHERE id::text = %s FOR UPDATE
                """,
                (approval.target_id,),
            )
            if allocation is None:
                raise TaskWorkflowError("Reward allocation is not registered")
            allocation_id = str(_row_value(allocation, "id", 0))
            task_id = str(_row_value(allocation, "task_id", 1))
            if decision is ApprovalDecision.APPROVE:
                self._require_owner_evaluation(task_id)
                proposed = int(_row_value(allocation, "proposed_reward_lamports", 4))
                amount = proposed if reward_amount_lamports is None else reward_amount_lamports
                budget = int(_row_value(allocation, "reward_budget_lamports", 3))
                if amount < 0 or amount > budget:
                    raise WorkflowConflict("approved Reward exceeds its Task budget")
                self.transaction.execute(
                    """
                    UPDATE reward_allocations
                    SET approved_reward_lamports = %s,
                        status = 'Approved'::mvp_reward_status,
                        approved_by = %s,
                        approved_at = now(),
                        comment = %s
                    WHERE id = %s
                    """,
                    (amount, actor.actor_id, comment, allocation_id),
                )
                self.transaction.execute(
                    """
                    INSERT INTO reward_ledger
                      (id, allocation_id, task_id, agent_id, amount_lamports, status, recorded_by)
                    VALUES (%s, %s, %s, %s, %s, 'Approved'::mvp_reward_status, %s)
                    """,
                    (
                        str(uuid4()),
                        allocation_id,
                        task_id,
                        str(_row_value(allocation, "agent_id", 2)),
                        amount,
                        actor.actor_id,
                    ),
                )
            elif decision is ApprovalDecision.REJECT:
                self.transaction.execute(
                    """
                    UPDATE reward_allocations
                    SET status = 'Cancelled'::mvp_reward_status, comment = %s
                    WHERE id = %s
                    """,
                    (comment, allocation_id),
                )
            target_type, target_id = "REWARD", allocation_id
            before = {"status": str(_row_value(allocation, "status", 5))}
            after = {"status": decision.value, "comment": comment}
        else:
            raise WorkflowConflict("approval type is outside the persistent Task workflow")

        self.transaction.execute(
            """
            UPDATE approval_requests
            SET owner_decision = %s::mvp_approval_decision,
                comment = %s,
                decided_at = now()
            WHERE id = %s
            """,
            (decision.value, comment, approval_id),
        )
        updated_row = self.transaction.fetch_one(
            """
            SELECT id, approval_type, target_id, requested_by, owner_decision,
                   comment, created_at, decided_at
            FROM approval_requests WHERE id = %s
            """,
            (approval_id,),
        )
        if updated_row is None:
            raise TaskWorkflowError("Approval decision was not persisted")
        updated_approval = _approval_from_row(updated_row)
        self._emit(
            actor=actor,
            action="DECIDE_APPROVAL",
            target_type=target_type,
            target_id=target_id,
            task_id=task_id,
            before=before,
            after=after,
            reason=comment or f"Owner decided {decision.value}",
            idempotency_key=key,
        )
        self._complete_idempotency(key, updated_approval.model_dump(mode="json"))
        return updated_approval

    def _has_owner_evaluation(self, task_id: str) -> bool:
        row = self.transaction.fetch_one(
            "SELECT 1 FROM owner_evaluations WHERE task_id = %s LIMIT 1",
            (task_id,),
        )
        return row is not None

    def _require_owner_evaluation(self, task_id: str) -> None:
        if not self._has_owner_evaluation(task_id):
            raise WorkflowNotReady("Owner Evaluation is required before final Task or Reward approval")


def _subtask_from_row(row: Any) -> SubTaskRecord:
    values = dict(row) if isinstance(row, Mapping) else dict(
        zip(
            (
                "id",
                "parent_task_id",
                "title",
                "description",
                "assigned_agent_id",
                "status",
                "progress",
                "dependencies",
                "artifact",
                "review_result",
                "audit_result",
                "created_at",
                "updated_at",
            ),
            row,
            strict=True,
        )
    )
    return SubTaskRecord(
        id=str(values["id"]),
        parent_task_id=str(values["parent_task_id"]),
        title=str(values["title"]),
        description=str(values["description"]),
        assigned_agent_id=str(values["assigned_agent_id"]),
        status=values["status"],
        progress=int(values["progress"]),
        dependencies=_json_value(values.get("dependencies"), []),
        artifact=values.get("artifact"),
        review_result=values.get("review_result"),
        audit_result=values.get("audit_result"),
        created_at=_as_utc(values["created_at"]),
        updated_at=_as_utc(values["updated_at"]),
    )


def _review_from_row(row: Any) -> ReviewRecord:
    values = dict(row) if isinstance(row, Mapping) else dict(
        zip(
            (
                "task_id",
                "reviewer",
                "quality",
                "completeness",
                "correctness",
                "required_changes",
                "comment",
                "reviewed_at",
            ),
            row,
            strict=True,
        )
    )
    return ReviewRecord(
        task_id=str(values["task_id"]),
        reviewer=str(values["reviewer"]),
        quality=int(values["quality"]),
        completeness=int(values["completeness"]),
        correctness=int(values["correctness"]),
        required_changes=_json_value(values.get("required_changes"), []),
        comment=str(values.get("comment") or ""),
        reviewed_at=_as_utc(values["reviewed_at"]),
    )


def _audit_from_row(row: Any) -> AuditRecord:
    values = dict(row) if isinstance(row, Mapping) else dict(
        zip(
            (
                "task_id",
                "auditor",
                "policy_compliance",
                "security_risk",
                "external_action_check",
                "reward_manipulation_check",
                "authority_violation_check",
                "result",
                "comment",
                "audited_at",
            ),
            row,
            strict=True,
        )
    )
    return AuditRecord(
        task_id=str(values["task_id"]),
        auditor=str(values["auditor"]),
        policy_compliance=bool(values["policy_compliance"]),
        security_risk=values["security_risk"],
        external_action_check=bool(values["external_action_check"]),
        reward_manipulation_check=bool(values["reward_manipulation_check"]),
        authority_violation_check=bool(values["authority_violation_check"]),
        result=values["result"],
        comment=str(values.get("comment") or ""),
        audited_at=_as_utc(values["audited_at"]),
    )


def _evaluation_from_row(row: Any) -> OwnerEvaluation:
    values = dict(row) if isinstance(row, Mapping) else dict(
        zip(
            (
                "task_id",
                "quality",
                "difficulty",
                "contribution",
                "timeliness",
                "rework",
                "strategic_value",
                "owner_comment",
                "evaluated_by",
                "evaluated_at",
            ),
            row,
            strict=True,
        )
    )
    return OwnerEvaluation(
        task_id=str(values["task_id"]),
        quality=int(values["quality"]),
        difficulty=int(values["difficulty"]),
        contribution=int(values["contribution"]),
        timeliness=int(values["timeliness"]),
        rework=int(values["rework"]),
        strategic_value=int(values["strategic_value"]),
        owner_comment=str(values.get("owner_comment") or ""),
        evaluated_by=str(values["evaluated_by"]),
        evaluated_at=_as_utc(values["evaluated_at"]),
    )


def _approval_from_row(row: Any) -> ApprovalRequest:
    values = dict(row) if isinstance(row, Mapping) else dict(
        zip(
            (
                "id",
                "approval_type",
                "target_id",
                "requested_by",
                "owner_decision",
                "comment",
                "created_at",
                "decided_at",
            ),
            row,
            strict=True,
        )
    )
    return ApprovalRequest(
        id=str(values["id"]),
        approval_type=values["approval_type"],
        target_id=str(values["target_id"]),
        requested_by=str(values["requested_by"]),
        owner_decision=values.get("owner_decision"),
        comment=str(values.get("comment") or ""),
        created_at=_as_utc(values["created_at"]),
        decided_at=_as_utc(values["decided_at"]) if values.get("decided_at") is not None else None,
    )


class PersistentTaskWorkflow:
    """Application-facing facade that gives every command its own UoW."""

    def __init__(self, repository: PostgresRepository, *, owner_id: str = "owner-local") -> None:
        self.repository = repository
        self.owner_id = owner_id

    def _run(self, callback):
        return self.repository.run(callback)

    def create_task(self, actor: ActorContext, command: TaskCreateCommand, *, idempotency_key: str | None = None) -> TaskRecord:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).create_task(actor, command, idempotency_key=idempotency_key))

    def update_acceptance_criteria(self, actor: ActorContext, task_id: str, acceptance_criteria: list[str], *, reason: str, idempotency_key: str | None = None) -> TaskRecord:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).update_acceptance_criteria(actor, task_id, acceptance_criteria, reason=reason, idempotency_key=idempotency_key))

    def assign_executive(self, actor: ActorContext, task_id: str, executive_agent_id: str, *, idempotency_key: str | None = None) -> TaskRecord:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).assign_executive(actor, task_id, executive_agent_id, idempotency_key=idempotency_key))

    def create_subtask(self, actor: ActorContext, task_id: str, command: SubTaskCreateCommand, *, idempotency_key: str | None = None) -> SubTaskRecord:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).create_subtask(actor, task_id, command, idempotency_key=idempotency_key))

    def transition_task(self, actor: ActorContext, task_id: str, to_status: TaskStatus, *, progress: int | None = None, reason: str = "", idempotency_key: str | None = None) -> TaskRecord:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).transition_task(actor, task_id, to_status, progress=progress, reason=reason, idempotency_key=idempotency_key))

    def submit_evidence(self, actor: ActorContext, task_id: str, sub_task_id: str, uri: str, *, content_hash: str | None = None, idempotency_key: str | None = None) -> SubTaskRecord:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).submit_evidence(actor, task_id, sub_task_id, uri, content_hash=content_hash, idempotency_key=idempotency_key))

    def submit_review(self, actor: ActorContext, task_id: str, command: ReviewCommand, *, idempotency_key: str | None = None) -> ReviewRecord:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).submit_review(actor, task_id, command, idempotency_key=idempotency_key))

    def record_audit(self, actor: ActorContext, task_id: str, command: AuditCommand, *, idempotency_key: str | None = None) -> AuditRecord:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).record_audit(actor, task_id, command, idempotency_key=idempotency_key))

    def evaluate_task(self, actor: ActorContext, task_id: str, command: OwnerEvaluationCommand, *, idempotency_key: str | None = None) -> OwnerEvaluation:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).evaluate_task(actor, task_id, command, idempotency_key=idempotency_key))

    def decide_approval(self, actor: ActorContext, approval_id: str, decision: ApprovalDecision, *, comment: str = "", reward_amount_lamports: int | None = None, idempotency_key: str | None = None) -> ApprovalRequest:
        return self._run(lambda tx: TaskWorkflowRepository(tx, owner_id=self.owner_id).decide_approval(actor, approval_id, decision, comment=comment, reward_amount_lamports=reward_amount_lamports, idempotency_key=idempotency_key))


def postgres_task_workflow(database_url: str, *, owner_id: str = "owner-local") -> PersistentTaskWorkflow:
    """Build the PostgreSQL facade without importing psycopg at module load."""

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - composition-only path
        raise RuntimeError("psycopg is required for the PostgreSQL Task workflow") from exc
    return PersistentTaskWorkflow(
        PostgresRepository(lambda: psycopg.connect(database_url)),
        owner_id=owner_id,
    )
