# Observability, Stop Controls, and Incidents

Operational state is enforced at execution boundaries. A dashboard may show a
stop, but the command, Agent Run, Provider, MPP, Payment, and Signer adapters
must call `ExecutionGate.assert_allowed` or pass
`StopController.runtime_checker(...)` before doing work.

## Stop state

`StopController` manages `GLOBAL`, `COMMAND`, `RUN`, `AGENT`, `PROVIDER`,
`MPP`, `SIGNER`, and `PAYMENT` targets. A global stop and a wildcard target
stop block every matching operation. Stop and resume are Owner-only Policy
actions; each transition has a reason, Policy version, actor, version, and
correlation ID. Resume never silently restarts in-flight external work; the
caller must create a new bounded operation after recovery.

The PostgreSQL backend persists current state and append-only history. Audit
and Outbox records are written in the same transaction as the state change.

## Telemetry and alerts

`InMemoryObservability` is the deterministic reference recorder. It emits
structured events with request/run/trace IDs and records latency, token use,
cost, attempts, payment rejection rate, and budget usage. Metadata is sanitized
before storage. Alerts are produced for abnormal retries, payment rejection
rates, and budget overruns.

## Incident recovery

`IncidentManager` records an incident and append-only timeline. Acknowledgement
and resolution require the Owner and a reason. The default recovery procedure
preserves correlation, keeps affected scopes stopped, inspects sanitized Audit
and telemetry, validates the fix offline or on devnet, and resumes only the
required scope.

No stop or observability record grants authority to mutate Tasks, Rewards,
Treasury, wallets, or customer assets.
