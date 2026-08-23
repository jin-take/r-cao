import pytest

from app.messages import validate_agent_message
from app.models import AgentMessage, AgentRole, MessageType
from app.policy import PolicyViolation


def test_delegation_requires_task_and_delegation_context() -> None:
    message = AgentMessage(
        message_id="msg-1",
        idempotency_key="msg-1",
        trace_id="trace-1",
        sender_agent_id="orion",
        recipient_agent_id="lyra",
        message_type=MessageType.DELEGATION,
    )

    with pytest.raises(PolicyViolation):
        validate_agent_message(message, AgentRole.MANAGER)


def test_owner_decision_cannot_be_issued_by_agent() -> None:
    message = AgentMessage(
        message_id="msg-2",
        idempotency_key="msg-2",
        trace_id="trace-2",
        task_id="task-1",
        sender_agent_id="orion",
        recipient_agent_id="owner",
        message_type=MessageType.OWNER_DECISION,
    )

    with pytest.raises(PolicyViolation):
        validate_agent_message(message, AgentRole.MANAGER)


def test_agent_message_cannot_smuggle_direct_asset_transfer() -> None:
    message = AgentMessage(
        message_id="msg-3",
        idempotency_key="msg-3",
        trace_id="trace-3",
        task_id="task-1",
        sender_agent_id="owner",
        recipient_agent_id="builder",
        message_type=MessageType.REQUEST,
        payload={"transfer_lamports": 1},
    )

    with pytest.raises(PolicyViolation, match="direct asset"):
        validate_agent_message(message, AgentRole.OWNER)
