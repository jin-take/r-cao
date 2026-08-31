from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.a2a import (
    MessageAuthorizationError,
    MessageConflictError,
    MessageExpiredError,
    MessageGatewayRepository,
    MessageValidationError,
    MessageStatusConflictError,
)
from app.agent_registry import RegisteredAgent
from app.auth import ActorContext, ActorType
from app.models import AgentMessage, AgentRole, MessageStatus, MessageType
from app.policy import Phase


NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)


def agent(agent_id: str, role: str = "BUILDER") -> RegisteredAgent:
    return RegisteredAgent(
        agent_id=agent_id,
        identity_id=agent_id,
        name=agent_id,
        role=role,
        organization_layer="VALUE_CREATION",
        mission="Complete the assigned Task",
        responsibilities=("Task work",),
        authority=("Propose work",),
        prohibited_actions=("Change authority",),
        reports_to="owner-local",
        agent_type="SUB_AGENT",
        status="ACTIVE",
        version=1,
        model="policy-bound",
        provider="TEST",
        prompt_version="v1",
        capability_hash=f"sha256:{agent_id}",
        allowed_tools=("repo.read",),
        network_scope=("OFFCHAIN",),
        budget_scope={"max_lamports": 100},
        risk_scope={"max": "LOW"},
        budget_limit_lamports=100,
    )


def message(**changes: object) -> AgentMessage:
    values: dict[str, object] = {
        "message_id": "msg-001",
        "idempotency_key": "idem-001",
        "nonce": "nonce-001",
        "trace_id": "trace-001",
        "task_id": "T-001",
        "sender_agent_id": "agent-builder",
        "recipient_agent_id": "agent-reviewer",
        "message_type": MessageType.REQUEST,
        "payload": {"action": "PROPOSE_PLAN", "content": "Review the plan"},
        "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
    }
    values.update(changes)
    return AgentMessage(**values)


def actor(agent_id: str) -> ActorContext:
    return ActorContext(
        actor_id=agent_id,
        subject=f"subject:{agent_id}",
        name=agent_id,
        role=AgentRole.REVIEWER,
        actor_type=ActorType.AGENT,
        phase=Phase.PHASE_1_OFFCHAIN,
        token_id=f"token:{agent_id}",
        issued_at=1,
        expires_at=2,
        identity_version=1,
    )


class FakeRegistry:
    agents = {
        "agent-builder": agent("agent-builder"),
        "agent-reviewer": agent("agent-reviewer", role="REVIEWER"),
    }

    def __init__(self, transaction) -> None:
        self.transaction = transaction

    def ensure_can_participate(self, agent_id: str, **kwargs: object) -> RegisteredAgent:
        registered = self.agents.get(agent_id)
        if registered is None:
            raise ValueError(f"Agent is not registered: {agent_id}")
        return registered

    def get_delegation(self, delegation_id: str):
        return None


class FakeTransaction:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, object]] = {}
        self.audit_rows: list[tuple[object, ...]] = []
        self.outbox_rows: list[tuple[object, ...]] = []

    def fetch_one(self, statement: str, params: tuple[object, ...] = ()):
        normalized = " ".join(statement.split()).lower()
        if normalized.startswith("select task_id from mvp_agent_messages"):
            row = self.messages.get(str(params[0]))
            return {"task_id": row["task_id"]} if row is not None else None
        if normalized.startswith("select") and "from mvp_agent_messages" in normalized:
            row = None
            if "where idempotency_key" in normalized:
                row = next(
                    (item for item in self.messages.values() if item["idempotency_key"] == params[0]),
                    None,
                )
            elif "where sender_agent_id" in normalized:
                row = next(
                    (
                        item
                        for item in self.messages.values()
                        if item["sender_agent_id"] == params[0] and item["nonce"] == params[1]
                    ),
                    None,
                )
            elif "where id =" in normalized:
                row = self.messages.get(str(params[0]))
            return row
        if normalized.startswith("update mvp_agent_messages"):
            status, message_id, current_status = params
            row = self.messages.get(str(message_id))
            if row is None or row["status"] != current_status:
                return None
            row["status"] = status
            return row
        return None

    def fetch_all(self, statement: str, params: tuple[object, ...] = ()):
        rows = list(self.messages.values())
        if params:
            # The final two parameters are LIMIT/OFFSET.  This fake only needs
            # enough filtering to exercise the Gateway's query contract.
            limit, offset = int(params[-2]), int(params[-1])
            filter_params = params[:-2]
            if filter_params:
                task_id = filter_params[0]
                rows = [row for row in rows if row["task_id"] == task_id]
            return rows[offset : offset + limit]
        return rows

    def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
        normalized = " ".join(statement.split()).lower()
        if normalized.startswith("insert into mvp_agent_messages"):
            (
                message_id,
                schema_version,
                idempotency_key,
                nonce,
                task_id,
                run_id,
                trace_id,
                conversation_id,
                parent_message_id,
                sender_agent_id,
                recipient_agent_id,
                message_type,
                authority_context,
                payload,
                evidence_refs,
                status,
                expires_at,
                correlation_id,
                message_fingerprint,
            ) = params
            self.messages[str(message_id)] = {
                "id": message_id,
                "schema_version": schema_version,
                "idempotency_key": idempotency_key,
                "nonce": nonce,
                "task_id": task_id,
                "run_id": run_id,
                "trace_id": trace_id,
                "conversation_id": conversation_id,
                "parent_message_id": parent_message_id,
                "sender_agent_id": sender_agent_id,
                "recipient_agent_id": recipient_agent_id,
                "message_type": message_type,
                "authority_context": authority_context,
                "payload": payload,
                "evidence_refs": evidence_refs,
                "status": status,
                "expires_at": expires_at,
                "correlation_id": correlation_id,
                "message_fingerprint": message_fingerprint,
                "created_at": NOW,
                "updated_at": NOW,
            }
        elif normalized.startswith("insert into mvp_audit_logs"):
            self.audit_rows.append(params)
        elif normalized.startswith("insert into mvp_outbox_events"):
            self.outbox_rows.append(params)


def repository() -> tuple[MessageGatewayRepository, FakeTransaction]:
    transaction = FakeTransaction()
    return MessageGatewayRepository(transaction), transaction


def test_send_is_task_bound_and_records_audit_and_outbox_once(monkeypatch) -> None:
    monkeypatch.setattr("app.a2a.AgentRegistryRepository", FakeRegistry)
    gateway, transaction = repository()
    first = gateway.send(message(), now=NOW)
    second = gateway.send(message(), now=NOW)

    assert first.message.message_id == "msg-001"
    assert first.replayed is False
    assert second.replayed is True
    assert len(transaction.messages) == 1
    assert len(transaction.audit_rows) == 1
    assert len(transaction.outbox_rows) == 1
    assert transaction.messages["msg-001"]["status"] == "SENT"


def test_same_key_or_nonce_cannot_bind_a_different_request(monkeypatch) -> None:
    monkeypatch.setattr("app.a2a.AgentRegistryRepository", FakeRegistry)
    gateway, _ = repository()
    gateway.send(message(), now=NOW)

    with pytest.raises(MessageConflictError):
        gateway.send(message(payload={"action": "REQUEST_INFORMATION"}), now=NOW)

    with pytest.raises(MessageConflictError):
        gateway.send(
            message(idempotency_key="idem-002", message_id="msg-002"),
            now=NOW,
        )


def test_expiry_parent_and_forbidden_actions_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr("app.a2a.AgentRegistryRepository", FakeRegistry)
    gateway, _ = repository()

    with pytest.raises(MessageExpiredError):
        gateway.send(
            message(expires_at=(NOW - timedelta(seconds=1)).isoformat()),
            now=NOW,
        )

    with pytest.raises(MessageConflictError, match="parent"):
        gateway.send(message(parent_message_id="missing"), now=NOW)

    with pytest.raises(MessageValidationError, match="forbidden action"):
        gateway.send(
            message(
                message_id="msg-003",
                idempotency_key="idem-003",
                nonce="nonce-003",
                payload={"action": "TRANSFER_ASSET"},
            ),
            now=NOW,
        )


def test_recipient_only_can_advance_the_message_lifecycle(monkeypatch) -> None:
    monkeypatch.setattr("app.a2a.AgentRegistryRepository", FakeRegistry)
    gateway, transaction = repository()
    gateway.send(message(), now=NOW)

    with pytest.raises(MessageAuthorizationError):
        gateway.transition_status(
            "msg-001",
            actor_id="agent-builder",
            actor_type=ActorType.AGENT,
            status=MessageStatus.DELIVERED,
            reason="wrong actor",
            now=NOW,
        )

    delivered = gateway.transition_status(
        "msg-001",
        actor_id="agent-reviewer",
        actor_type=ActorType.AGENT,
        status=MessageStatus.DELIVERED,
        reason="recipient received message",
        now=NOW,
    )
    acknowledged = gateway.transition_status(
        "msg-001",
        actor_id="agent-reviewer",
        actor_type=ActorType.AGENT,
        status=MessageStatus.ACKNOWLEDGED,
        reason="recipient acknowledged message",
        now=NOW,
    )
    consumed = gateway.transition_status(
        "msg-001",
        actor_id="agent-reviewer",
        actor_type=ActorType.AGENT,
        status=MessageStatus.CONSUMED,
        reason="recipient consumed message",
        now=NOW,
    )

    assert delivered.message.status is MessageStatus.DELIVERED
    assert acknowledged.message.status is MessageStatus.ACKNOWLEDGED
    assert consumed.message.status is MessageStatus.CONSUMED
    assert len(transaction.audit_rows) == 4
    assert len(transaction.outbox_rows) == 4

    with pytest.raises(MessageStatusConflictError):
        gateway.transition_status(
            "msg-001",
            actor_id="agent-reviewer",
            actor_type=ActorType.AGENT,
            status=MessageStatus.DELIVERED,
            reason="reopen",
            now=NOW,
        )
