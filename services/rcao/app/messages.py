from .models import AgentMessage, AgentRole, MessageType
from .policy import PolicyViolation


TASK_BOUND_TYPES = {
    MessageType.COMMAND,
    MessageType.DELEGATION,
    MessageType.REQUEST,
    MessageType.RESPONSE,
    MessageType.HANDOFF,
    MessageType.REVIEW_REQUEST,
    MessageType.REVIEW_RESULT,
    MessageType.DECISION_REQUEST,
    MessageType.OWNER_DECISION,
    MessageType.EVIDENCE,
    MessageType.BLOCK,
    MessageType.ESCALATION,
}

FORBIDDEN_DIRECT_TRANSFER_KEYS = {
    "transfer",
    "transfer_lamports",
    "wallet_address",
    "send_asset",
    "direct_reward",
}


def validate_agent_message(
    message: AgentMessage,
    sender_role: AgentRole,
) -> AgentMessage:
    if message.message_type in TASK_BOUND_TYPES and not message.task_id:
        raise PolicyViolation(
            f"{message.message_type} must be linked to a task_id"
        )

    if (
        message.message_type is MessageType.OWNER_DECISION
        and sender_role is not AgentRole.OWNER
    ):
        raise PolicyViolation("Only Owner can issue OWNER_DECISION")

    if message.message_type is MessageType.DELEGATION:
        if sender_role not in {AgentRole.OWNER, AgentRole.MANAGER}:
            raise PolicyViolation("Only Owner or Manager can issue DELEGATION")
        if not message.authority_context.delegation_id:
            raise PolicyViolation("DELEGATION requires delegation_id")

    if message.message_type in {
        MessageType.COMMAND,
        MessageType.OWNER_DECISION,
    } and sender_role is not AgentRole.OWNER:
        raise PolicyViolation(f"{message.message_type} requires Owner authority")

    forbidden = FORBIDDEN_DIRECT_TRANSFER_KEYS.intersection(message.payload)
    if forbidden:
        raise PolicyViolation(
            "Agent messages cannot authorize direct asset or Reward transfers: "
            + ", ".join(sorted(forbidden))
        )

    return message
