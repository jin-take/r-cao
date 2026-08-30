# R-CAO PostgreSQL migrations

The SQL files in this directory are the executable schema history for the
Control Plane. They are ordered by the four-digit prefix and are applied by
`app.migrations`. Once a migration has been applied, do not edit it; add a new
forward migration instead.

## Local usage

```bash
python -m pip install -e 'services/rcao[postgres]'
DATABASE_URL=postgresql://rcao:rcao@localhost:5432/rcao \
  rcao-migrate --directory db/migrations
```

## Existing database adoption

The repository's `docker-compose.yml` initializes a database from the
consolidated `db/schema.sql`. For such an existing database, first verify that
the schema contains every object through the selected migration, then stamp
the history without executing the migration SQL:

```bash
DATABASE_URL=postgresql://rcao:rcao@localhost:5432/rcao \
  rcao-migrate --directory db/migrations --baseline-version 3
```

Baseline stamping is explicit and never performs automatic schema detection.
Do not use it for a partially initialized or unverified database. It creates
only missing `schema_migrations` history rows and does not delete or rewrite
application data.

The runner creates `schema_migrations` and records each migration's SHA-256
checksum. It applies a migration and its history row in one transaction. A
checksum mismatch, unknown version, or out-of-order migration stops execution.

`0001_phase1_foundation.sql` is the original Phase 1 relational schema,
`0002_owner_directed_mvp.sql` is the persistence contract introduced by the
Owner-Directed MVP, `0003_transaction_boundaries.sql` adds transactional
command primitives, `0004_idempotency_request_fingerprint.sql` binds replay to
the complete request, and `0005_audit_outbox_replay.sql` adds versioned Audit /
Outbox metadata. `db/schema.sql` remains a review-friendly consolidated schema;
future changes must be represented by a new migration and reflected in that
consolidated file.

The migrations do not create wallets, hold private keys, connect to mainnet, or
move real assets.
