"""Persistent, policy-bound Agent-to-Agent message gateway.

Messages are proposals and coordination records.  They are not an alternate
command path: the gateway never changes a Task, Ledger, Treasury, Authority,
wallet, or external system.  It only validates a Task-bound envelope and
persists the message, Audit event, and Outbox notification in one unit of
work.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field

from .agent_registry import (
    AgentRegistryError,
    AgentRegistryRepository,
)
from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter
from .auth import ActorContext, ActorType
from .messages import validate_agent_message
from .models import AgentMessage, AgentRole, MessageStatus, MessageType
from .policy import PolicyViolation
from .repository import PostgresRepository, RepositoryTransaction


MESSAGE_RECORD_COLUMNS = (
    "id",
    "schema_version",
    "idempotency_key",
    "nonce",
    "task_id",
    "run_id",
    "trace_id",
    "conversation_id",
    "parent_message_id",
    "sender_agent_id",
    "recipient_agent_id",
    "message_type",
    "authority_context",
    "payload",
    "evidence_refs",
    "status",
    "expires_at",
    "correlation_id",
    "message_fingerprint",
    "created_at",
    "updated_at",
)


class MessageGatewayError(ValueError):
    """Base error for rejected or unavailable A2A gateway operations."""


class MessageAuthorizationError(MessageGatewayError):
    """The sender, recipient, or status actor is outside the message scope."""


class MessageValidationError(MessageGatewayError):
    """The message envelope violates the versioned protocol contract."""


class MessageConflictError(MessageGatewayError):
    """A message key, nonce, or parent relationship conflicts with history."""


class MessageNotFoundError(MessageGatewayError):
    """The requested message does not exist."""


class MessageExpiredError(MessageGatewayError):
    """The message or its authority context is no longer valid."""


class MessageStatusConflictError(MessageGatewayError):
    """The requested message status transition is not allowed."""


MESSAGE_STATUS_TRANSITIONS: dict[MessageStatus, frozenset[MessageStatus]] = {
    MessageStatus.SENT: frozenset(
        {MessageStatus.DELIVERED, MessageStatus.REJECTED, MessageStatus.EXPIRED}
    ),
    MessageStatus.DELIVERED: frozenset(
        {MessageStatus.ACKNOWLEDGED, MessageStatus.REJECTED, MessageStatus.EXPIRED}
    ),
    MessageStatus.ACKNOWLEDGED: frozenset(
        {MessageStatus.CONSUMED, MessageStatus.EXPIRED}
    ),
    MessageStatus.CONSUMED: frozenset(),
    MessageStatus.REJECTED: frozenset(),
    MessageStatus.EXPIRED: frozenset(),
}


@dataclass(frozen=True)
class MessageSendResult:
    message: AgentMessage
    replayed: bool = False


@dataclass(frozen=True)
class MessageStatusResult:
    message: AgentMessage
    replayed: bool = False


class MessageStatusCommand(BaseModel):
    status: MessageStatus
    reason: str = Field(min_length=1, max_length=500)


class MessageSendResponse(BaseModel):
    message: AgentMessage
    replayed: bool = False


class MessageStatusResponse(BaseModel):
    message: AgentMessage
    replayed: bool = False


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise MessageGatewayError("stored message JSON is invalid") from exc
    return value


def _as_utc(value: datetime | None = None) -> datetime:
    parsed = value or datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_timestamp(value: str | datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise MessageValidationError(f"{field_name} must be an ISO-8601 timestamp") from exc


def _timestamp_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return str(value)


def _message_from_row(row: Any) -> AgentMessage:
    values = (
        dict(row)
        if isinstance(row, Mapping)
        else dict(zip(MESSAGE_RECORD_COLUMNS, row, strict=True))
    )
    authority_context = _json_value(values.get("authority_context"), {})
    payload = _json_value(values.get("payload"), {})
    evidence_refs = _json_value(values.get("evidence_refs"), [])
    return AgentMessage(
        schema_version=str(values["schema_version"]),
        message_id=str(values["id"]),
        idempotency_key=str(values["idempotency_key"]),
        nonce=str(values["nonce"]),
        trace_id=str(values["trace_id"]),
        task_id=str(values["task_id"]),
        run_id=str(values["run_id"]) if values.get("run_id") is not None else None,
        conversation_id=(
            str(values["conversation_id"])
            if values.get("conversation_id") is not None
            else None
        ),
        parent_message_id=(
            str(values["parent_message_id"])
            if values.get("parent_message_id") is not None
            else None
        ),
        sender_agent_id=str(values["sender_agent_id"]),
        recipient_agent_id=str(values["recipient_agent_id"]),
        message_type=MessageType(str(values["message_type"])),
        authority_context=authority_context,
        payload=payload,
        evidence_refs=evidence_refs,
        expires_at=_timestamp_string(values.get("expires_at")),
        correlation_id=(
            str(values["correlation_id"])
            if values.get("correlation_id") is not None
            else None
        ),
        status=MessageStatus(str(values["status"])),
    )


def _message_state(message: AgentMessage) -> dict[str, Any]:
    return message.model_dump(mode="json")


def _message_fingerprint(message: AgentMessage) -> str:
    # Correlation IDs are tracing metadata.  If the gateway generated one,
    # retries with the same idempotency key must still identify the same
    # request.  Status is server-owned and is not part of the request either.
    request = message.model_dump(
        mode="json",
        exclude={"status", "correlation_id"},
    )
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _message_action(message: AgentMessage) -> str:
    action = message.payload.get("action")
    if isinstance(action, str) and action.strip():
        return action.strip().upper().replace("-", "_").replace(" ", "_")
    return {
        MessageType.REVIEW_REQUEST: "REVIEW",
        MessageType.REVIEW_RESULT: "REVIEW",
        MessageType.DECISION_REQUEST: "DECISION",
        MessageType.OWNER_DECISION: "DECISION",
    }.get(message.message_type, message.message_type.value)


class MessageGatewayRepository:
    """A repository-bound implementation used inside one database transaction."""

    def __init__(self, transaction: RepositoryTransaction) -> None:
        self.transaction = transaction

    def _fetch_message(self, message_id: str, *, for_update: bool = False) -> AgentMessage | None:
        lock = " FOR UPDATE" if for_update else ""
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(MESSAGE_RECORD_COLUMNS)}
            FROM mvp_agent_messages
            WHERE id = %s{lock}
            """,
            (message_id,),
        )
        return _message_from_row(row) if row is not None else None

    def _fetch_by_idempotency(self, idempotency_key: str) -> tuple[AgentMessage, str] | None:
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(MESSAGE_RECORD_COLUMNS)}
            FROM mvp_agent_messages
            WHERE idempotency_key = %s
            FOR UPDATE
            """,
            (idempotency_key,),
        )
        if row is None:
            return None
        return _message_from_row(row), str(_row_value(row, "message_fingerprint", 18))

    def _fetch_by_nonce(self, sender_agent_id: str, nonce: str) -> tuple[AgentMessage, str] | None:
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(MESSAGE_RECORD_COLUMNS)}
            FROM mvp_agent_messages
            WHERE sender_agent_id = %s AND nonce = %s
            FOR UPDATE
            """,
            (sender_agent_id, nonce),
        )
        if row is None:
            return None
        return _message_from_row(row), str(_row_value(row, "message_fingerprint", 18))

    def _fetch_by_id(self, message_id: str) -> tuple[AgentMessage, str] | None:
        row = self.transaction.fetch_one(
            f"""
            SELECT {', '.join(MESSAGE_RECORD_COLUMNS)}
            FROM mvp_agent_messages
            WHERE id = %s
            FOR UPDATE
            """,
            (message_id,),
        )
        if row is None:
            return None
        return _message_from_row(row), str(_row_value(row, "message_fingerprint", 18))

    def _validate_parent(self, message: AgentMessage) -> None:
        if message.parent_message_id is None:
            return
        if message.parent_message_id == message.message_id:
            raise MessageConflictError("message cannot be its own parent")
        row = self.transaction.fetch_one(
            "SELECT task_id FROM mvp_agent_messages WHERE id = %s",
            (message.parent_message_id,),
        )
        if row is None:
            raise MessageConflictError("parent message is not registered")
        parent_task_id = str(_row_value(row, "task_id", 0))
        if parent_task_id != message.task_id:
            raise MessageConflictError("parent message belongs to another Task")

    def _validate_registry_scope(
        self,
        message: AgentMessage,
        *,
        now: datetime,
    ) -> None:
        registry = AgentRegistryRepository(self.transaction)
        action = _message_action(message)
        context = message.authority_context

        try:
            sender = registry.ensure_can_participate(
                message.sender_agent_id,
                task_id=message.task_id,
                action=action,
                risk_level=context.risk_class,
            )
            recipient = registry.ensure_can_participate(
                message.recipient_agent_id,
                task_id=message.task_id,
                action=action,
                delegation_id=context.delegation_id,
                risk_level=context.risk_class,
            )
        except AgentRegistryError as exc:
            raise MessageAuthorizationError(str(exc)) from exc

        try:
            sender_role = AgentRole(sender.role)
        except ValueError as exc:
            raise MessageAuthorizationError("sender Agent role is not supported") from exc
        try:
            validate_agent_message(message, sender_role, now=now)
        except PolicyViolation as exc:
            raise MessageValidationError(str(exc)) from exc

        if context.delegation_id is None:
            if context.allowed_scope or context.expires_at is not None:
                raise MessageAuthorizationError(
                    "authority scope and expiry require a persisted delegation_id"
                )
            return

        delegation = registry.get_delegation(context.delegation_id)
        if delegation is None:
            raise MessageAuthorizationError("delegation is not registered")
        if delegation.parent_agent_id != message.sender_agent_id:
            raise MessageAuthorizationError("delegation parent does not match sender")
        requested_scope = {str(item) for item in context.allowed_scope}
        persisted_scope = set(delegation.allowed_scope)
        if requested_scope and not requested_scope.issubset(persisted_scope):
            raise MessageAuthorizationError(
                "message scope exceeds the persisted delegation scope"
            )
        if context.budget_lamports > delegation.budget_limit_lamports:
            raise MessageAuthorizationError(
                "message budget exceeds the persisted delegation budget"
            )
        authority_expiry = _parse_timestamp(context.expires_at, "authority_context.expires_at")
        if authority_expiry is not None:
            if authority_expiry <= now:
                raise MessageExpiredError("authority context has expired")
            delegation_expiry = _parse_timestamp(delegation.expires_at, "delegation.expires_at")
            if delegation_expiry is not None and authority_expiry > delegation_expiry:
                raise MessageAuthorizationError(
                    "authority context outlives the persisted delegation"
                )

    def _append_audit_and_outbox(
        self,
        *,
        message: AgentMessage,
        action: str,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        reason: str,
        outbox_idempotency_key: str,
    ) -> None:
        correlation_id = message.correlation_id or message.trace_id
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                event_version=1,
                event_type="A2A_MESSAGE",
                actor_id=message.sender_agent_id,
                actor_type="AGENT",
                action=action,
                target_type="AGENT_MESSAGE",
                target_id=message.message_id,
                before_state=before,
                after_state=after,
                policy_result="ALLOW",
                reason=reason,
                correlation_id=correlation_id,
                transaction_id=correlation_id,
                task_id=message.task_id,
                run_id=message.run_id,
                message_id=message.message_id,
            ),
        )
        OutboxWriter.enqueue(
            self.transaction,
            OutboxEvent(
                event_id=f"outbox-{uuid4().hex}",
                aggregate_type="AGENT_MESSAGE",
                aggregate_id=message.message_id,
                event_type=action,
                idempotency_key=outbox_idempotency_key,
                payload={
                    "action": action,
                    "message": _message_state(message),
                    "correlation_id": correlation_id,
                },
                event_version=1,
                transaction_id=correlation_id,
            ),
        )

    def send(self, message: AgentMessage, *, now: datetime) -> MessageSendResult:
        if message.status is not MessageStatus.SENT:
            raise MessageValidationError("new messages must start in SENT status")
        if not message.task_id:
            raise MessageValidationError("A2A messages require task_id")
        expiry = _parse_timestamp(message.expires_at, "expires_at")
        if expiry is None:
            raise MessageValidationError("A2A messages require expires_at")
        if expiry <= now:
            raise MessageExpiredError("message has expired")
        if any(not isinstance(ref, str) or not ref.strip() for ref in message.evidence_refs):
            raise MessageValidationError("evidence_refs must contain non-empty strings")

        self._validate_registry_scope(message, now=now)
        self._validate_parent(message)

        fingerprint = _message_fingerprint(message)
        existing = self._fetch_by_idempotency(message.idempotency_key)
        if existing is None:
            existing = self._fetch_by_nonce(message.sender_agent_id, message.nonce)
        if existing is None:
            existing = self._fetch_by_id(message.message_id)
        if existing is not None:
            existing_message, existing_fingerprint = existing
            if (
                existing_message.idempotency_key == message.idempotency_key
                and existing_fingerprint == fingerprint
            ):
                return MessageSendResult(existing_message, replayed=True)
            raise MessageConflictError(
                "message idempotency key, nonce, or message_id is already bound to another request"
            )

        self.transaction.execute(
            """
            INSERT INTO mvp_agent_messages
              (id, schema_version, idempotency_key, nonce, task_id, run_id,
               trace_id, conversation_id, parent_message_id, sender_agent_id,
               recipient_agent_id, message_type, authority_context, payload,
               evidence_refs, status, expires_at, correlation_id,
               message_fingerprint)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
            """,
            (
                message.message_id,
                message.schema_version,
                message.idempotency_key,
                message.nonce,
                message.task_id,
                message.run_id,
                message.trace_id,
                message.conversation_id,
                message.parent_message_id,
                message.sender_agent_id,
                message.recipient_agent_id,
                message.message_type.value,
                json.dumps(message.authority_context.model_dump(mode="json"), sort_keys=True),
                json.dumps(message.payload, ensure_ascii=False, sort_keys=True),
                json.dumps(message.evidence_refs, ensure_ascii=False),
                message.status.value,
                expiry,
                message.correlation_id,
                fingerprint,
            ),
        )
        self._append_audit_and_outbox(
            message=message,
            action="SEND_AGENT_MESSAGE",
            before={},
            after=_message_state(message),
            reason="Gateway accepted a Task-bound A2A message",
            outbox_idempotency_key=message.idempotency_key,
        )
        return MessageSendResult(message)

    def transition_status(
        self,
        message_id: str,
        *,
        actor_id: str,
        actor_type: ActorType,
        status: MessageStatus,
        reason: str,
        now: datetime,
    ) -> MessageStatusResult:
        message = self._fetch_message(message_id, for_update=True)
        if message is None:
            raise MessageNotFoundError(f"message is not registered: {message_id}")
        if actor_type is not ActorType.AGENT or actor_id != message.recipient_agent_id:
            raise MessageAuthorizationError(
                "only the registered recipient Agent may advance message status"
            )
        if message.status is status:
            return MessageStatusResult(message, replayed=True)
        if status is MessageStatus.EXPIRED:
            expiry = _parse_timestamp(message.expires_at, "expires_at")
            if expiry is None or expiry > now:
                raise MessageStatusConflictError("a non-expired message cannot be marked EXPIRED")
        elif _parse_timestamp(message.expires_at, "expires_at") <= now:
            raise MessageExpiredError("message has expired")
        if status not in MESSAGE_STATUS_TRANSITIONS[message.status]:
            raise MessageStatusConflictError(
                f"cannot transition message from {message.status.value} to {status.value}"
            )

        try:
            AgentRegistryRepository(self.transaction).ensure_can_participate(
                actor_id,
                task_id=message.task_id,
                action=f"MESSAGE_{status.value}",
            )
        except AgentRegistryError as exc:
            raise MessageAuthorizationError(str(exc)) from exc

        before = _message_state(message)
        row = self.transaction.fetch_one(
            f"""
            UPDATE mvp_agent_messages
            SET status = %s, updated_at = now()
            WHERE id = %s AND status = %s
            RETURNING {', '.join(MESSAGE_RECORD_COLUMNS)}
            """,
            (status.value, message_id, message.status.value),
        )
        if row is None:
            raise MessageStatusConflictError("message status changed concurrently")
        updated = _message_from_row(row)
        self._append_audit_and_outbox(
            message=updated,
            action="UPDATE_AGENT_MESSAGE_STATUS",
            before=before,
            after=_message_state(updated),
            reason=reason,
            outbox_idempotency_key=f"message-status:{message_id}:{status.value}",
        )
        return MessageStatusResult(updated)

    def list_messages(
        self,
        *,
        actor: ActorContext | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        status: MessageStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AgentMessage, ...]:
        if limit < 1 or limit > 1000:
            raise MessageValidationError("limit must be between 1 and 1000")
        if offset < 0:
            raise MessageValidationError("offset cannot be negative")
        predicates: list[str] = []
        params: list[Any] = []
        if task_id is not None:
            predicates.append("task_id = %s")
            params.append(task_id)
        if trace_id is not None:
            predicates.append("trace_id = %s")
            params.append(trace_id)
        if conversation_id is not None:
            predicates.append("conversation_id = %s")
            params.append(conversation_id)
        if status is not None:
            predicates.append("status = %s")
            params.append(status.value)
        if actor is not None and actor.actor_type is ActorType.AGENT:
            if task_id is None:
                raise MessageAuthorizationError("Agent message search requires task_id")
            predicates.append("(sender_agent_id = %s OR recipient_agent_id = %s)")
            params.extend([actor.actor_id, actor.actor_id])
        elif actor is not None and actor.actor_type is not ActorType.OWNER:
            raise MessageAuthorizationError("only Owner or Agent identities may search messages")
        where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        rows = self.transaction.fetch_all(
            f"""
            SELECT {', '.join(MESSAGE_RECORD_COLUMNS)}
            FROM mvp_agent_messages
            {where}
            ORDER BY created_at ASC, id ASC
            LIMIT %s OFFSET %s
            """,
            (*params, limit, offset),
        )
        return tuple(_message_from_row(row) for row in rows)


class PersistentMessageGateway:
    """Application facade that gives each message operation its own UoW."""

    def __init__(self, repository: PostgresRepository) -> None:
        self.repository = repository

    def send(self, message: AgentMessage, *, now: datetime | None = None) -> MessageSendResult:
        current = _as_utc(now)
        if message.status is not MessageStatus.SENT:
            raise MessageValidationError("new messages must start in SENT status")
        correlation_id = message.correlation_id or f"corr-{uuid4().hex}"
        normalized = message.model_copy(
            update={"correlation_id": correlation_id, "status": MessageStatus.SENT}
        )
        return self.repository.run(
            lambda transaction: MessageGatewayRepository(transaction).send(
                normalized,
                now=current,
            )
        )

    def transition_status(
        self,
        message_id: str,
        *,
        actor: ActorContext,
        status: MessageStatus,
        reason: str,
        now: datetime | None = None,
    ) -> MessageStatusResult:
        return self.repository.run(
            lambda transaction: MessageGatewayRepository(transaction).transition_status(
                message_id,
                actor_id=actor.actor_id,
                actor_type=actor.actor_type,
                status=status,
                reason=reason,
                now=_as_utc(now),
            )
        )

    def list_messages(
        self,
        *,
        actor: ActorContext | None = None,
        task_id: str | None = None,
        trace_id: str | None = None,
        conversation_id: str | None = None,
        status: MessageStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[AgentMessage, ...]:
        return self.repository.run(
            lambda transaction: MessageGatewayRepository(transaction).list_messages(
                actor=actor,
                task_id=task_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                status=status,
                limit=limit,
                offset=offset,
            )
        )


def postgres_message_gateway(database_url: str) -> PersistentMessageGateway:
    """Build the PostgreSQL gateway without importing psycopg at module load."""

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - composition-only path
        raise RuntimeError("psycopg is required for the PostgreSQL A2A gateway") from exc
    return PersistentMessageGateway(
        PostgresRepository(lambda: psycopg.connect(database_url))
    )
