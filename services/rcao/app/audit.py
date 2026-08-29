"""Versioned audit and outbox contracts for the R-CAO control plane.

The control plane records a sanitized, immutable description of each
state-changing decision.  Audit replay is intentionally a pure operation: it
reconstructs recorded state and never invokes a provider, external API,
signer, wallet, or payment adapter.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


REDACTED = "[REDACTED]"
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:authorization|access[_-]?token|api[_-]?key|auth[_-]?token|"
    r"client[_-]?secret|password|private[_-]?key|seed(?:[_-]?phrase)?|"
    r"mnemonic|secret|token)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
        r"client[_-]?secret|password|private[_-]?key|seed(?:[_-]?phrase)?|"
        r"mnemonic|secret|token)\s*[:=]\s*[^\s,;]+"
    ),
)

REPLAY_FORBIDDEN_ACTIONS = frozenset(
    {
        "SEND_EXTERNAL_ACTION",
        "EXECUTE_EXTERNAL_ACTION",
        "SIGN_TRANSACTION",
        "SUBMIT_TRANSACTION",
        "EXECUTE_PAYMENT",
        "SEND_PAYMENT",
        "TRANSFER_ASSET",
    }
)


class AuditContractError(ValueError):
    """Raised when an audit or outbox record violates its contract."""


class ReplayIntegrityError(AuditContractError):
    """Raised when the recorded event sequence cannot be reconciled."""


class ReplaySafetyError(AuditContractError):
    """Raised when replay is asked to execute an external side effect."""


def _sanitize_string(value: str) -> str:
    sanitized = value
    for pattern in SENSITIVE_VALUE_PATTERNS:
        sanitized = pattern.sub(REDACTED, sanitized)
    return sanitized


def sanitize(value: Any, *, key: str | None = None) -> Any:
    """Return a JSON-compatible copy with secret-bearing values redacted."""

    if key is not None and SENSITIVE_KEY_PATTERN.search(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): sanitize(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


def _json(value: Any) -> str:
    return json.dumps(
        sanitize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


@dataclass(frozen=True)
class AuditEvent:
    """A versioned, sanitized record of one control-plane decision."""

    event_id: str
    event_version: int
    event_type: str
    actor_id: str
    actor_type: str
    action: str
    target_type: str
    target_id: str
    before_state: Mapping[str, Any]
    after_state: Mapping[str, Any]
    policy_result: str
    reason: str
    correlation_id: str
    transaction_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    payment_id: str | None = None
    ledger_entry_id: str | None = None
    evidence_hash: str | None = None
    event_hash: str | None = None
    previous_event_hash: str | None = None
    created_at: datetime | str | None = None

    def __post_init__(self) -> None:
        if self.event_version < 1:
            raise AuditContractError("event_version must be positive")
        for field_name in (
            "event_id",
            "event_type",
            "actor_id",
            "actor_type",
            "action",
            "target_type",
            "target_id",
            "policy_result",
            "reason",
            "correlation_id",
        ):
            if not getattr(self, field_name):
                raise AuditContractError(f"{field_name} is required")

    def sanitized(self) -> "AuditEvent":
        return replace(
            self,
            before_state=sanitize(self.before_state),
            after_state=sanitize(self.after_state),
            reason=_sanitize_string(self.reason),
        )

    def canonical_payload(self) -> dict[str, Any]:
        event = self.sanitized()
        return {
            "event_id": event.event_id,
            "event_version": event.event_version,
            "event_type": event.event_type,
            "actor_id": event.actor_id,
            "actor_type": event.actor_type,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "before_state": event.before_state,
            "after_state": event.after_state,
            "policy_result": event.policy_result,
            "reason": event.reason,
            "correlation_id": event.correlation_id,
            "transaction_id": event.transaction_id,
            "task_id": event.task_id,
            "run_id": event.run_id,
            "message_id": event.message_id,
            "payment_id": event.payment_id,
            "ledger_entry_id": event.ledger_entry_id,
            "evidence_hash": event.evidence_hash,
            "previous_event_hash": event.previous_event_hash,
        }

    def with_integrity(self) -> "AuditEvent":
        event = self.sanitized()
        digest = hashlib.sha256(_json(event.canonical_payload()).encode("utf-8")).hexdigest()
        return replace(event, event_hash=digest)

    def to_record(self) -> dict[str, Any]:
        event = self.with_integrity()
        return {
            "id": event.event_id,
            "event_version": event.event_version,
            "event_type": event.event_type,
            "actor": event.actor_id,
            "actor_type": event.actor_type,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "before_state": dict(event.before_state),
            "after_state": dict(event.after_state),
            "policy_result": event.policy_result,
            "reason": event.reason,
            "correlation_id": event.correlation_id,
            "transaction_id": event.transaction_id,
            "task_id": event.task_id,
            "run_id": event.run_id,
            "message_id": event.message_id,
            "payment_id": event.payment_id,
            "ledger_entry_id": event.ledger_entry_id,
            "evidence_hash": event.evidence_hash,
            "event_hash": event.event_hash,
            "previous_event_hash": event.previous_event_hash,
            "created_at": event.created_at,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any] | tuple[Any, ...]) -> "AuditEvent":
        if isinstance(record, Mapping):
            values = record
        else:
            values = dict(zip(AUDIT_RECORD_COLUMNS, record, strict=True))
        return cls(
            event_id=str(values["id"]),
            event_version=int(values.get("event_version", 1)),
            event_type=str(values.get("event_type", "STATE_CHANGE")),
            actor_id=str(values["actor"]),
            actor_type=str(values["actor_type"]),
            action=str(values["action"]),
            target_type=str(values["target_type"]),
            target_id=str(values["target_id"]),
            before_state=values.get("before_state") or {},
            after_state=values.get("after_state") or {},
            policy_result=str(values["policy_result"]),
            reason=str(values["reason"]),
            correlation_id=str(values["correlation_id"]),
            transaction_id=values.get("transaction_id"),
            task_id=values.get("task_id"),
            run_id=values.get("run_id"),
            message_id=values.get("message_id"),
            payment_id=values.get("payment_id"),
            ledger_entry_id=values.get("ledger_entry_id"),
            evidence_hash=values.get("evidence_hash"),
            event_hash=values.get("event_hash"),
            previous_event_hash=values.get("previous_event_hash"),
            created_at=values.get("created_at"),
        )


AUDIT_RECORD_COLUMNS = (
    "id",
    "event_version",
    "event_type",
    "actor",
    "actor_type",
    "action",
    "target_type",
    "target_id",
    "before_state",
    "after_state",
    "policy_result",
    "reason",
    "correlation_id",
    "transaction_id",
    "task_id",
    "run_id",
    "message_id",
    "payment_id",
    "ledger_entry_id",
    "evidence_hash",
    "event_hash",
    "previous_event_hash",
    "created_at",
)


@dataclass(frozen=True)
class OutboxEvent:
    """A durable notification to be delivered outside the DB transaction."""

    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    idempotency_key: str
    payload: Mapping[str, Any]
    event_version: int = 1
    transaction_id: str | None = None
    delivery_status: str = "PENDING"
    delivery_attempts: int = 0
    last_error: str | None = None
    available_at: datetime | str | None = None

    def __post_init__(self) -> None:
        if self.event_version < 1:
            raise AuditContractError("event_version must be positive")
        if self.delivery_status not in {"PENDING", "IN_FLIGHT", "PUBLISHED", "FAILED"}:
            raise AuditContractError("invalid outbox delivery_status")
        if self.delivery_attempts < 0:
            raise AuditContractError("delivery_attempts cannot be negative")
        for field_name in (
            "event_id",
            "aggregate_type",
            "aggregate_id",
            "event_type",
            "idempotency_key",
        ):
            if not getattr(self, field_name):
                raise AuditContractError(f"{field_name} is required")

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "idempotency_key": self.idempotency_key,
            "payload": sanitize(self.payload),
            "transaction_id": self.transaction_id,
            "delivery_status": self.delivery_status,
            "delivery_attempts": self.delivery_attempts,
            "last_error": _sanitize_string(self.last_error) if self.last_error else None,
            "available_at": self.available_at,
        }


class AuditWriter:
    """Persist sanitized Audit events through an existing transaction."""

    @staticmethod
    def append(transaction: Any, event: AuditEvent) -> AuditEvent:
        record = event.with_integrity().to_record()
        transaction.execute(
            """
            INSERT INTO mvp_audit_logs
              (id, event_version, event_type, actor, actor_type, action,
               target_type, target_id, before_state, after_state,
               policy_result, reason, correlation_id, transaction_id, task_id,
               run_id, message_id, payment_id, ledger_entry_id, evidence_hash,
               event_hash, previous_event_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s::mvp_policy_result, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s)
            """,
            (
                record["id"],
                record["event_version"],
                record["event_type"],
                record["actor"],
                record["actor_type"],
                record["action"],
                record["target_type"],
                record["target_id"],
                _json(record["before_state"]),
                _json(record["after_state"]),
                record["policy_result"],
                record["reason"],
                record["correlation_id"],
                record["transaction_id"],
                record["task_id"],
                record["run_id"],
                record["message_id"],
                record["payment_id"],
                record["ledger_entry_id"],
                record["evidence_hash"],
                record["event_hash"],
                record["previous_event_hash"],
            ),
        )
        return AuditEvent.from_record(record)


class OutboxWriter:
    """Persist an outbox event in the same transaction as its state change."""

    @staticmethod
    def enqueue(transaction: Any, event: OutboxEvent) -> OutboxEvent:
        record = event.to_record()
        transaction.execute(
            """
            INSERT INTO mvp_outbox_events
              (id, aggregate_type, aggregate_id, event_type, event_version,
               idempotency_key, payload, transaction_id, delivery_status,
               delivery_attempts, last_error, available_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
            """,
            (
                record["id"],
                record["aggregate_type"],
                record["aggregate_id"],
                record["event_type"],
                record["event_version"],
                record["idempotency_key"],
                _json(record["payload"]),
                record["transaction_id"],
                record["delivery_status"],
                record["delivery_attempts"],
                record["last_error"],
                record["available_at"] or datetime.now(timezone.utc),
            ),
        )
        return event


@dataclass(frozen=True)
class ReplayResult:
    """Reconstructed target state from an ordered Audit event sequence."""

    events_processed: int
    states: Mapping[str, Mapping[str, Any]]
    correlations: tuple[str, ...]


def _event_from_input(event: AuditEvent | Mapping[str, Any]) -> AuditEvent:
    return event if isinstance(event, AuditEvent) else AuditEvent.from_record(event)


def replay_audit_events(
    events: Iterable[AuditEvent | Mapping[str, Any]],
) -> ReplayResult:
    """Reconstruct recorded state without executing any external side effect."""

    states: dict[str, dict[str, Any]] = {}
    correlations: list[str] = []
    processed = 0
    for raw_event in events:
        event = _event_from_input(raw_event)
        if event.action.upper() in REPLAY_FORBIDDEN_ACTIONS:
            raise ReplaySafetyError(
                f"replay cannot execute external action: {event.action}"
            )
        if event.event_hash and event.event_hash != event.with_integrity().event_hash:
            raise ReplayIntegrityError(f"Audit event hash mismatch: {event.event_id}")

        key = f"{event.target_type}:{event.target_id}"
        before = dict(sanitize(event.before_state))
        current = states.get(key)
        if current is not None and before and current != before:
            raise ReplayIntegrityError(
                f"Audit event before_state does not match replay state: {event.event_id}"
            )
        states[key] = dict(sanitize(event.after_state))
        correlations.append(event.correlation_id)
        processed += 1

    return ReplayResult(
        events_processed=processed,
        states=states,
        correlations=tuple(correlations),
    )
