from __future__ import annotations

from pathlib import Path

import pytest

from app.migrations import MigrationError, discover_migrations, migrate


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.rows: list[tuple[int, str, str]] = []

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        self.connection.statements.append((statement, params))
        normalized = " ".join(statement.split()).lower()
        if normalized.startswith("select version, name, checksum"):
            self.rows = list(self.connection.applied)
        elif normalized.startswith("insert into schema_migrations"):
            version, name, checksum = params
            self.connection.applied.append((int(version), str(name), str(checksum)))
        elif "raise migration failure" in normalized:
            raise RuntimeError("migration failure")

    def fetchall(self) -> list[tuple[int, str, str]]:
        return self.rows

    def close(self) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.applied: list[tuple[int, str, str]] = []
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_repository_migrations_are_ordered_and_cover_schema_layers() -> None:
    migrations = discover_migrations(Path("db/migrations"))

    assert [(item.version, item.name) for item in migrations] == [
        (1, "phase1_foundation"),
        (2, "owner_directed_mvp"),
        (3, "transaction_boundaries"),
        (4, "idempotency_request_fingerprint"),
        (5, "audit_outbox_replay"),
    ]
    assert "CREATE TABLE agents" in migrations[0].sql
    assert "CREATE TABLE mvp_tasks" in migrations[1].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_command_idempotency" in migrations[2].sql
    assert "ADD COLUMN IF NOT EXISTS request_fingerprint" in migrations[3].sql
    assert "ALTER COLUMN request_fingerprint SET NOT NULL" not in migrations[3].sql
    assert "legacy-unfingerprinted:" in migrations[3].sql
    assert "ADD COLUMN IF NOT EXISTS event_hash" in migrations[4].sql
    assert "delivery_status" in migrations[4].sql
    assert "mvp_audit_task_created_idx" in migrations[4].sql
    assert all(len(item.checksum) == 64 for item in migrations)


def test_migrate_is_idempotent_and_records_checksums(tmp_path: Path) -> None:
    (tmp_path / "0001_first.sql").write_text("CREATE TABLE first (id integer);", encoding="utf-8")
    connection = FakeConnection()

    first = migrate(connection, tmp_path)
    statement_count = len(connection.statements)
    second = migrate(connection, tmp_path)

    assert first == second
    assert len(connection.statements) == statement_count + 2
    assert connection.applied[0][1] == "first"
    assert connection.commits >= 3


def test_migrate_rejects_changed_applied_sql(tmp_path: Path) -> None:
    migration = tmp_path / "0001_first.sql"
    migration.write_text("CREATE TABLE first (id integer);", encoding="utf-8")
    connection = FakeConnection()
    migrate(connection, tmp_path)

    migration.write_text("CREATE TABLE first (id bigint);", encoding="utf-8")

    with pytest.raises(MigrationError, match="checksum"):
        migrate(connection, tmp_path)


def test_failed_migration_is_rolled_back(tmp_path: Path) -> None:
    (tmp_path / "0001_failure.sql").write_text("RAISE MIGRATION FAILURE", encoding="utf-8")
    connection = FakeConnection()

    with pytest.raises(RuntimeError, match="migration failure"):
        migrate(connection, tmp_path)

    assert connection.applied == []
    assert connection.rollbacks >= 1
