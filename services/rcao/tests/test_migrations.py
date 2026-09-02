from __future__ import annotations

from pathlib import Path

import pytest

from app.migrations import (
    DEFAULT_MIGRATION_DIR,
    MigrationError,
    discover_migrations,
    migrate,
    stamp_baseline,
)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.rows: list[tuple[int, str, str]] = []

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        self.connection.statements.append((statement, params))
        normalized = " ".join(statement.split()).lower()
        if "pg_advisory_unlock" in normalized and self.connection.transaction_failed:
            raise RuntimeError("cannot unlock failed transaction")
        if normalized.startswith("select version, name, checksum"):
            self.rows = list(self.connection.applied)
        elif normalized.startswith("insert into schema_migrations"):
            if self.connection.fail_on_history_insert:
                self.connection.transaction_failed = True
                raise RuntimeError("history insert failure")
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
        self.transaction_failed = False
        self.fail_on_history_insert = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.transaction_failed = False

    def rollback(self) -> None:
        self.rollbacks += 1
        self.transaction_failed = False


def test_repository_migrations_are_ordered_and_cover_schema_layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    migrations = discover_migrations(DEFAULT_MIGRATION_DIR)

    assert [(item.version, item.name) for item in migrations] == [
        (1, "phase1_foundation"),
        (2, "owner_directed_mvp"),
        (3, "transaction_boundaries"),
        (4, "idempotency_request_fingerprint"),
        (5, "audit_outbox_replay"),
        (6, "agent_registry_capabilities"),
        (7, "task_workflow_acceptance_history"),
        (8, "virtual_ledger_treasury"),
        (9, "a2a_message_gateway"),
        (10, "agent_runs"),
        (11, "evidence_memory"),
        (12, "observability_stop_incidents"),
        (13, "service_payment_boundary"),
        (14, "agent_payment_profiles"),
    ]
    assert "CREATE TABLE agents" in migrations[0].sql
    assert "CREATE TABLE mvp_tasks" in migrations[1].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_command_idempotency" in migrations[2].sql
    assert "ADD COLUMN IF NOT EXISTS request_fingerprint" in migrations[3].sql
    assert "ALTER COLUMN request_fingerprint SET NOT NULL" not in migrations[3].sql
    assert "legacy-unfingerprinted:" in migrations[3].sql
    assert "ADD COLUMN IF NOT EXISTS event_hash" in migrations[4].sql
    assert "delivery_status" in migrations[4].sql
    assert "SET delivery_status = 'PUBLISHED'" in migrations[4].sql
    assert "mvp_audit_task_created_idx" in migrations[4].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_agent_memberships" in migrations[5].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_agent_delegations" in migrations[5].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_task_acceptance_history" in migrations[6].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_treasury_accounts" in migrations[7].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_virtual_ledger_entries" in migrations[7].sql
    assert "CHECK (asset_type = 'VIRTUAL_REWARD')" in migrations[7].sql
    assert "CHECK (currency = 'VIRTUAL')" in migrations[7].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_agent_messages" in migrations[8].sql
    assert "message_fingerprint" in migrations[8].sql
    assert "mvp_agent_messages_envelope_immutable" in migrations[8].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_agent_runs" in migrations[9].sql
    assert "prompt_version" in migrations[9].sql
    assert "mvp_agent_runs_request_immutable" in migrations[9].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_evidence" in migrations[10].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_memory_items" in migrations[10].sql
    assert "mvp_evidence_content_immutable" in migrations[10].sql
    assert "mvp_memory_content_immutable" in migrations[10].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_stop_controls" in migrations[11].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_observability_events" in migrations[11].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_incidents" in migrations[11].sql
    assert "mvp_stop_control_history_no_mutation" in migrations[11].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_service_payments" in migrations[12].sql
    assert "CREATE TABLE IF NOT EXISTS mvp_service_payment_events" in migrations[12].sql
    assert "purpose = 'SERVICE_PAYMENT'" in migrations[12].sql
    assert "mvp_service_payments_identity_immutable" in migrations[12].sql
    assert "mvp_agent_payment_profile_versions" in migrations[13].sql
    assert "mvp_agent_payment_profiles_active_identity_idx" in migrations[13].sql
    assert "mvp_service_payments_profile_fk" in migrations[13].sql
    assert "mvp_agent_payment_profiles_no_secret_identity" in migrations[13].sql
    assert all(len(item.checksum) == 64 for item in migrations)


def test_migrate_is_idempotent_and_records_checksums(tmp_path: Path) -> None:
    (tmp_path / "0001_first.sql").write_text("CREATE TABLE first (id integer);", encoding="utf-8")
    connection = FakeConnection()

    first = migrate(connection, tmp_path)
    statement_count = len(connection.statements)
    second = migrate(connection, tmp_path)

    assert first == second
    assert len(connection.statements) == statement_count + 4
    assert sum("pg_advisory_lock" in statement.lower() for statement, _ in connection.statements) == 2
    assert sum("pg_advisory_unlock" in statement.lower() for statement, _ in connection.statements) == 2
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


def test_failed_baseline_rolls_back_before_unlocking(tmp_path: Path) -> None:
    (tmp_path / "0001_first.sql").write_text("CREATE TABLE first (id integer);", encoding="utf-8")
    connection = FakeConnection()
    connection.fail_on_history_insert = True

    with pytest.raises(RuntimeError, match="history insert failure"):
        stamp_baseline(connection, tmp_path, 1)

    assert connection.rollbacks >= 1
    unlocks = [
        statement
        for statement, _ in connection.statements
        if "pg_advisory_unlock" in statement.lower()
    ]
    assert unlocks


def test_stamp_baseline_records_existing_schema_without_executing_sql() -> None:
    connection = FakeConnection()

    history = stamp_baseline(connection, DEFAULT_MIGRATION_DIR, 3)

    assert [item.version for item in history] == [1, 2, 3]
    assert connection.applied == [
        (1, "phase1_foundation", history[0].checksum),
        (2, "owner_directed_mvp", history[1].checksum),
        (3, "transaction_boundaries", history[2].checksum),
    ]
    assert not any("CREATE TABLE agents" in statement for statement, _ in connection.statements)
