from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.repository import (
    IdempotencyConflictError,
    OptimisticConcurrencyError,
    PostgresRepository,
    RecordNotFoundError,
    TaskTransitionCommand,
)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.rows: list[tuple[object, ...]] = []

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        normalized = " ".join(statement.split()).lower()
        self.connection.statements.append((normalized, params))
        self.rows = []

        if normalized.startswith("insert into mvp_command_idempotency"):
            key, command_name, actor_id = params
            if key not in self.connection.idempotency:
                self.connection.idempotency[str(key)] = {
                    "command_name": str(command_name),
                    "actor_id": str(actor_id),
                    "response": None,
                }
                self.rows = [(key,)]
        elif normalized.startswith("select command_name, actor_id, response"):
            record = self.connection.idempotency.get(str(params[0]))
            if record:
                self.rows = [
                    (record["command_name"], record["actor_id"], record["response"])
                ]
        elif normalized.startswith("select id, status, progress, version, updated_at"):
            task = self.connection.tasks.get(str(params[0]))
            if task and task["version"] == int(params[1]):
                self.rows = [
                    (
                        params[0],
                        task["status"],
                        task["progress"],
                        task["version"],
                        task["updated_at"],
                    )
                ]
        elif normalized.startswith("update mvp_tasks"):
            status, progress, task_id, expected_version = params
            task = self.connection.tasks.get(str(task_id))
            if task and task["version"] == int(expected_version):
                task["status"] = str(status)
                task["progress"] = int(progress)
                task["version"] += 1
                task["updated_at"] = datetime.now(timezone.utc)
                self.rows = [
                    (
                        task_id,
                        task["status"],
                        task["progress"],
                        task["version"],
                        task["updated_at"],
                    )
                ]
        elif normalized.startswith("select id, version from mvp_tasks"):
            task = self.connection.tasks.get(str(params[0]))
            if task:
                self.rows = [(params[0], task["version"])]
        elif normalized.startswith("update mvp_command_idempotency"):
            response, key = params
            if str(key) in self.connection.idempotency:
                self.connection.idempotency[str(key)]["response"] = response
        elif normalized.startswith("insert into mvp_audit_logs"):
            self.connection.audit_rows.append(params)
        elif normalized.startswith("insert into mvp_outbox_events"):
            self.connection.outbox_rows.append(params)

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.tasks = {
            "T-001": {
                "status": "DRAFT",
                "progress": 0,
                "version": 1,
                "updated_at": datetime(2026, 8, 29, tzinfo=timezone.utc),
            }
        }
        self.idempotency: dict[str, dict[str, object]] = {}
        self.audit_rows: list[tuple[object, ...]] = []
        self.outbox_rows: list[tuple[object, ...]] = []
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def command(*, expected_version: int = 1, key: str = "cmd-001") -> TaskTransitionCommand:
    return TaskTransitionCommand(
        task_id="T-001",
        expected_version=expected_version,
        status="APPROVED",
        progress=10,
        actor_id="owner-local",
        actor_type="OWNER",
        reason="Owner approved task",
        correlation_id="corr-001",
        idempotency_key=key,
        audit_id=f"audit-{key}",
        outbox_event_id=f"event-{key}",
    )


def test_task_transition_commits_audit_and_outbox_in_one_unit_of_work() -> None:
    connection = FakeConnection()
    repository = PostgresRepository(lambda: connection)

    result = repository.transition_task(command())

    assert result.task.status == "APPROVED"
    assert result.task.version == 2
    assert result.replayed is False
    assert len(connection.audit_rows) == 1
    assert len(connection.outbox_rows) == 1
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed is True


def test_same_idempotency_key_replays_without_duplicate_side_effects() -> None:
    connection = FakeConnection()
    repository = PostgresRepository(lambda: connection)

    first = repository.transition_task(command())
    second = repository.transition_task(command())

    assert first.task.version == second.task.version == 2
    assert second.replayed is True
    assert len(connection.audit_rows) == 1
    assert len(connection.outbox_rows) == 1


def test_stale_version_is_rejected() -> None:
    connection = FakeConnection()
    repository = PostgresRepository(lambda: connection)
    repository.transition_task(command())

    with pytest.raises(OptimisticConcurrencyError):
        repository.transition_task(command(expected_version=1, key="cmd-002"))

    assert len(connection.audit_rows) == 1
    assert connection.rollbacks == 1


def test_unknown_task_is_rejected_and_rolled_back() -> None:
    connection = FakeConnection()
    repository = PostgresRepository(lambda: connection)
    missing = command(key="cmd-missing")
    missing = replace(missing, task_id="T-404")

    with pytest.raises(RecordNotFoundError):
        repository.transition_task(missing)

    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_idempotency_key_cannot_change_actor() -> None:
    connection = FakeConnection()
    repository = PostgresRepository(lambda: connection)
    repository.transition_task(command())
    conflicting = replace(command(), actor_id="agent-theo")

    with pytest.raises(IdempotencyConflictError):
        repository.transition_task(conflicting)
