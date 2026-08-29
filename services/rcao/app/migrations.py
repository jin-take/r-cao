"""Small, deterministic PostgreSQL migration runner for the R-CAO control plane.

The runner intentionally keeps the migration contract narrow: migrations are
ordered UTF-8 SQL files, each file is applied once, and its SHA-256 checksum is
recorded in ``schema_migrations``.  It uses a DB-API connection so the domain
package can be tested without requiring PostgreSQL; the optional ``postgres``
extra supplies psycopg for the CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MIGRATION_FILENAME = re.compile(r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9_]+)\.sql$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MIGRATION_DIR = REPOSITORY_ROOT / "db" / "migrations"
# Stable namespace value for session-level PostgreSQL advisory locking. The
# lock is held across each migration commit and released after the full run.
MIGRATION_ADVISORY_LOCK_KEY = 0x5243414F4D494752


class MigrationError(RuntimeError):
    """Raised when the migration history cannot be applied safely."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


@dataclass(frozen=True)
class AppliedMigration:
    version: int
    name: str
    checksum: str


SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  checksum TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def discover_migrations(directory: Path | str) -> tuple[Migration, ...]:
    """Load and validate all migration files in numeric order."""

    root = Path(directory)
    if not root.is_dir():
        raise MigrationError(f"migration directory does not exist: {root}")

    migrations: list[Migration] = []
    versions: set[int] = set()
    for path in sorted(root.glob("*.sql")):
        match = MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(
                f"invalid migration filename {path.name!r}; "
                "expected NNNN_lower_snake_case.sql"
            )
        version = int(match.group("version"))
        if version in versions:
            raise MigrationError(f"duplicate migration version: {version}")
        sql = path.read_text(encoding="utf-8")
        if not sql.strip():
            raise MigrationError(f"migration is empty: {path}")
        versions.add(version)
        migrations.append(
            Migration(
                version=version,
                name=match.group("name"),
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )

    if not migrations:
        raise MigrationError(f"no migrations found in {root}")
    return tuple(sorted(migrations, key=lambda item: item.version))


def _execute(connection: Any, statement: str, params: Iterable[Any] = ()) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute(statement, tuple(params))
    finally:
        close = getattr(cursor, "close", None)
        if close is not None:
            close()


def _fetchall(connection: Any, statement: str) -> list[Any]:
    cursor = connection.cursor()
    try:
        cursor.execute(statement)
        return list(cursor.fetchall())
    finally:
        close = getattr(cursor, "close", None)
        if close is not None:
            close()


@contextmanager
def migration_lock(connection: Any) -> Any:
    """Serialize migration runners for the lifetime of their DB session."""

    locked = False
    try:
        _execute(
            connection,
            "SELECT pg_advisory_lock(%s)",
            (MIGRATION_ADVISORY_LOCK_KEY,),
        )
        locked = True
        yield
    finally:
        if locked:
            _execute(
                connection,
                "SELECT pg_advisory_unlock(%s)",
                (MIGRATION_ADVISORY_LOCK_KEY,),
            )


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, dict):
        return row[key]
    return row[index]


def ensure_migration_table(connection: Any) -> None:
    """Create the history table in the same connection used for migration."""

    _execute(connection, SCHEMA_MIGRATIONS_SQL)


def load_applied_migrations(connection: Any) -> dict[int, AppliedMigration]:
    rows = _fetchall(
        connection,
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version",
    )
    return {
        int(_row_value(row, "version", 0)): AppliedMigration(
            version=int(_row_value(row, "version", 0)),
            name=str(_row_value(row, "name", 1)),
            checksum=str(_row_value(row, "checksum", 2)),
        )
        for row in rows
    }


def _validate_applied(
    migrations: tuple[Migration, ...], applied: dict[int, AppliedMigration]
) -> None:
    known = {migration.version: migration for migration in migrations}
    for version, record in applied.items():
        migration = known.get(version)
        if migration is None:
            raise MigrationError(
                f"database contains unknown migration version {version}; "
                "restore the migration file before continuing"
            )
        if record.name != migration.name or record.checksum != migration.checksum:
            raise MigrationError(
                f"migration {version:04d} does not match its recorded checksum; "
                "never edit an applied migration"
            )


def stamp_baseline(
    connection: Any, directory: Path | str, version: int
) -> tuple[AppliedMigration, ...]:
    """Record an already-provisioned schema as applied through ``version``.

    This is an explicit adoption path for databases initialized by the
    consolidated ``db/schema.sql`` (or an equivalent previously managed
    schema). It never executes migration SQL and therefore must only be used
    after the operator has verified that the target schema already contains
    every object represented by the selected migrations.
    """

    migrations = discover_migrations(directory)
    selected = [migration for migration in migrations if migration.version <= version]
    expected_versions = list(range(1, version + 1))
    if version < 1 or [migration.version for migration in selected] != expected_versions:
        raise MigrationError(
            f"baseline version {version} is not a contiguous known migration history"
        )

    try:
        with migration_lock(connection):
            ensure_migration_table(connection)
            connection.commit()
            applied = load_applied_migrations(connection)
            _validate_applied(migrations, applied)
            if any(applied_version > version for applied_version in applied):
                raise MigrationError(
                    "cannot stamp an older baseline over newer applied migrations"
                )

            for migration in selected:
                if migration.version in applied:
                    continue
                _execute(
                    connection,
                    """
                    INSERT INTO schema_migrations (version, name, checksum)
                    VALUES (%s, %s, %s)
                    """,
                    (migration.version, migration.name, migration.checksum),
                )
                applied[migration.version] = AppliedMigration(
                    version=migration.version,
                    name=migration.name,
                    checksum=migration.checksum,
                )
            connection.commit()
            return tuple(applied[applied_version] for applied_version in sorted(applied))
    except BaseException:
        connection.rollback()
        raise


def migrate(connection: Any, directory: Path | str) -> tuple[AppliedMigration, ...]:
    """Apply pending migrations and return the complete applied history.

    Each migration SQL statement and its history row are committed together.
    A failing migration is rolled back, so a partially applied schema is never
    reported as complete.
    """

    migrations = discover_migrations(directory)
    try:
        with migration_lock(connection):
            ensure_migration_table(connection)
            connection.commit()
            applied = load_applied_migrations(connection)
            _validate_applied(migrations, applied)

            last_version = max(applied, default=0)
            for migration in migrations:
                if migration.version in applied:
                    continue
                if migration.version <= last_version:
                    raise MigrationError(
                        f"migration {migration.version:04d} is out of order; "
                        "add a new version above the current database version"
                    )

                cursor = connection.cursor()
                try:
                    cursor.execute(migration.sql)
                    cursor.execute(
                        """
                        INSERT INTO schema_migrations (version, name, checksum)
                        VALUES (%s, %s, %s)
                        """,
                        (migration.version, migration.name, migration.checksum),
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    close = getattr(cursor, "close", None)
                    if close is not None:
                        close()

                applied[migration.version] = AppliedMigration(
                    version=migration.version,
                    name=migration.name,
                    checksum=migration.checksum,
                )
                last_version = migration.version

            return tuple(applied[version] for version in sorted(applied))
    except BaseException:
        # The history-table setup can also fail (for example, because the
        # database is read-only). Do not leave an open transaction behind.
        connection.rollback()
        raise


def _connect(database_url: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - exercised by the CLI only
        raise MigrationError(
            "psycopg is required for the migration CLI; "
            "install services/rcao[postgres]"
        ) from exc
    return psycopg.connect(database_url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply R-CAO PostgreSQL migrations")
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(os.environ.get("RCAO_MIGRATIONS_DIR", DEFAULT_MIGRATION_DIR)),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
    )
    parser.add_argument(
        "--baseline-version",
        type=int,
        help=(
            "stamp an already-provisioned schema through this migration "
            "version without executing migration SQL"
        ),
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")

    connection = None
    try:
        connection = _connect(args.database_url)
        if args.baseline_version is not None:
            history = stamp_baseline(connection, args.directory, args.baseline_version)
            print(f"stamped baseline through migration: {args.baseline_version:04d}")
        else:
            history = migrate(connection, args.directory)
            print(f"applied migrations: {len(history)}")
        return 0
    except MigrationError as exc:
        print(f"migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
