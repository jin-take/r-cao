"""Transactional PostgreSQL repository primitives for the control plane.

The Owner-Directed MVP still has an in-memory reference store, but all future
commands need one durable boundary.  This module provides that boundary without
making the domain depend on a concrete connection pool or on psycopg at import
time.  A DB-API connection factory is supplied by the application composition
root; tests can use a small fake connection.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Iterator, Protocol


class RepositoryError(RuntimeError):
    """Base class for repository and transaction failures."""


class RecordNotFoundError(RepositoryError):
    """The requested aggregate does not exist."""


class OptimisticConcurrencyError(RepositoryError):
    """The aggregate changed after the caller read its version."""


class IdempotencyConflictError(RepositoryError):
    """A key was reused for a different command or actor."""


class CommandInProgressError(RepositoryError):
    """A pre-existing command has not completed yet."""


class ConnectionFactory(Protocol):
    def __call__(self) -> Any: ...


@dataclass(frozen=True)
class TaskTransitionCommand:
    task_id: str
    expected_version: int
    status: str
    progress: int
    actor_id: str
    actor_type: str
    reason: str
    correlation_id: str
    idempotency_key: str
    audit_id: str
    outbox_event_id: str

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id is required")
        if self.expected_version < 1:
            raise ValueError("expected_version must be positive")
        if not 0 <= self.progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        for field_name in (
            "actor_id",
            "actor_type",
            "reason",
            "correlation_id",
            "idempotency_key",
            "audit_id",
            "outbox_event_id",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} is required")


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    status: str
    progress: int
    version: int
    updated_at: datetime | str | None


@dataclass(frozen=True)
class TaskTransitionResult:
    task: TaskSnapshot
    replayed: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self.task)
        updated_at = payload["updated_at"]
        if isinstance(updated_at, datetime):
            payload["updated_at"] = updated_at.isoformat()
        return {"task": payload, "replayed": self.replayed}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TaskTransitionResult":
        task = payload["task"]
        return cls(
            task=TaskSnapshot(
                task_id=str(task["task_id"]),
                status=str(task["status"]),
                progress=int(task["progress"]),
                version=int(task["version"]),
                updated_at=task.get("updated_at"),
            ),
            replayed=True,
        )


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row[key]
    return row[index]


class RepositoryTransaction:
    """A unit of work bound to one database connection."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(statement, params)
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def _fetchone(self, statement: str, params: tuple[Any, ...] = ()) -> Any:
        cursor = self.connection.cursor()
        try:
            cursor.execute(statement, params)
            return cursor.fetchone()
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def _claim_idempotency(self, command: TaskTransitionCommand) -> TaskTransitionResult | None:
        inserted = self._fetchone(
            """
            INSERT INTO mvp_command_idempotency
              (idempotency_key, command_name, actor_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING idempotency_key
            """,
            (command.idempotency_key, "TRANSITION_TASK", command.actor_id),
        )
        if inserted is not None:
            return None

        existing = self._fetchone(
            """
            SELECT command_name, actor_id, response
            FROM mvp_command_idempotency
            WHERE idempotency_key = %s
            FOR UPDATE
            """,
            (command.idempotency_key,),
        )
        if existing is None:
            # The conflicting insert may have been rolled back between the two
            # statements. The caller may safely retry the whole transaction.
            raise CommandInProgressError("idempotency record is not available")

        command_name = str(_row_value(existing, "command_name", 0))
        actor_id = str(_row_value(existing, "actor_id", 1))
        if command_name != "TRANSITION_TASK" or actor_id != command.actor_id:
            raise IdempotencyConflictError(
                "idempotency key is already bound to another command or actor"
            )

        response = _row_value(existing, "response", 2)
        if response is None:
            raise CommandInProgressError("idempotent command has not completed")
        if isinstance(response, str):
            response = json.loads(response)
        return TaskTransitionResult.from_payload(response)

    def _task_snapshot(self, row: Any) -> TaskSnapshot:
        return TaskSnapshot(
            task_id=str(_row_value(row, "id", 0)),
            status=str(_row_value(row, "status", 1)),
            progress=int(_row_value(row, "progress", 2)),
            version=int(_row_value(row, "version", 3)),
            updated_at=_row_value(row, "updated_at", 4),
        )

    def _update_task(self, command: TaskTransitionCommand) -> TaskSnapshot:
        row = self._fetchone(
            """
            UPDATE mvp_tasks
            SET status = %s::mvp_task_status,
                progress = %s,
                version = version + 1,
                updated_at = now()
            WHERE id = %s AND version = %s
            RETURNING id, status, progress, version, updated_at
            """,
            (
                command.status,
                command.progress,
                command.task_id,
                command.expected_version,
            ),
        )
        if row is not None:
            return self._task_snapshot(row)

        exists = self._fetchone(
            "SELECT id, version FROM mvp_tasks WHERE id = %s",
            (command.task_id,),
        )
        if exists is None:
            raise RecordNotFoundError(f"task not found: {command.task_id}")
        raise OptimisticConcurrencyError(
            f"task {command.task_id} changed; expected version "
            f"{command.expected_version}, current version {_row_value(exists, 'version', 1)}"
        )

    def append_task_audit(
        self,
        command: TaskTransitionCommand,
        before: TaskSnapshot,
        after: TaskSnapshot,
    ) -> None:
        self.execute(
            """
            INSERT INTO mvp_audit_logs
              (id, actor, actor_type, action, target_type, target_id,
               before_state, after_state, policy_result, reason, correlation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    'ALLOW'::mvp_policy_result, %s, %s)
            """,
            (
                command.audit_id,
                command.actor_id,
                command.actor_type,
                "TRANSITION_TASK",
                "TASK",
                command.task_id,
                json.dumps(asdict(before), default=str, sort_keys=True),
                json.dumps(asdict(after), default=str, sort_keys=True),
                command.reason,
                command.correlation_id,
            ),
        )

    def enqueue_task_event(
        self,
        command: TaskTransitionCommand,
        before: TaskSnapshot,
        after: TaskSnapshot,
    ) -> None:
        payload = {
            "task_id": command.task_id,
            "before": asdict(before),
            "after": asdict(after),
            "actor_id": command.actor_id,
            "correlation_id": command.correlation_id,
        }
        self.execute(
            """
            INSERT INTO mvp_outbox_events
              (id, aggregate_type, aggregate_id, event_type, idempotency_key, payload)
            VALUES (%s, 'TASK', %s, 'TASK_TRANSITION', %s, %s::jsonb)
            """,
            (
                command.outbox_event_id,
                command.task_id,
                command.idempotency_key,
                json.dumps(payload, default=str, sort_keys=True),
            ),
        )

    def _complete_idempotency(
        self, command: TaskTransitionCommand, result: TaskTransitionResult
    ) -> None:
        self.execute(
            """
            UPDATE mvp_command_idempotency
            SET response = %s::jsonb, completed_at = now()
            WHERE idempotency_key = %s
            """,
            (
                json.dumps(result.to_payload(), default=str, sort_keys=True),
                command.idempotency_key,
            ),
        )

    def transition_task(self, command: TaskTransitionCommand) -> TaskTransitionResult:
        """Update a task and append its audit/outbox records atomically."""

        replay = self._claim_idempotency(command)
        if replay is not None:
            return replay

        before_row = self._fetchone(
            """
            SELECT id, status, progress, version, updated_at
            FROM mvp_tasks
            WHERE id = %s AND version = %s
            FOR UPDATE
            """,
            (command.task_id, command.expected_version),
        )
        if before_row is None:
            exists = self._fetchone(
                "SELECT id, version FROM mvp_tasks WHERE id = %s",
                (command.task_id,),
            )
            if exists is None:
                raise RecordNotFoundError(f"task not found: {command.task_id}")
            raise OptimisticConcurrencyError(
                f"task {command.task_id} changed before transition"
            )
        before = self._task_snapshot(before_row)
        after = self._update_task(command)
        self.append_task_audit(command, before, after)
        self.enqueue_task_event(command, before, after)
        result = TaskTransitionResult(task=after)
        self._complete_idempotency(command, result)
        return result


class PostgresRepository:
    """Open one connection per unit of work and own commit/rollback."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def transaction(self) -> Iterator[RepositoryTransaction]:
        connection = self._connection_factory()
        try:
            yield RepositoryTransaction(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            close = getattr(connection, "close", None)
            if close is not None:
                close()

    def run(self, callback: Callable[[RepositoryTransaction], Any]) -> Any:
        with self.transaction() as transaction:
            return callback(transaction)

    def transition_task(self, command: TaskTransitionCommand) -> TaskTransitionResult:
        return self.run(lambda transaction: transaction.transition_task(command))
