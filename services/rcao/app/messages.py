from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping

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

FORBIDDEN_DIRECT_ACTIONS = frozenset(
    {
        "APPROVE_REWARD",
        "CHANGE_AUTHORITY",
        "CHANGE_AGENT_AUTHORITY",
        "CHANGE_CONSTITUTION",
        "CHANGE_POLICY",
        "CHANGE_REWARD",
        "CREATE_AGENT",
        "CREATE_TASK",
        "EXECUTE_EXTERNAL_ACTION",
        "EXECUTE_PAYMENT",
        "FINALIZE_REWARD",
        "FUND_TREASURY",
        "PAY_REWARD",
        "REGISTER_AGENT",
        "SEND_PAYMENT",
        "SIGN_TRANSACTION",
        "SUBMIT_TRANSACTION",
        "TRANSFER_ASSET",
        "TRANSITION_TASK",
        "UPDATE_LEDGER",
    }
)

# Message payloads are intentionally small and explicit.  The payload is a
# proposal envelope; it is not a second command API.  ``metadata`` and
# ``content`` are available on every message so providers can carry a human
# readable explanation without inventing a new message type.
MESSAGE_PAYLOAD_FIELDS: dict[MessageType, frozenset[str]] = {
    MessageType.COMMAND: frozenset({"action", "arguments", "metadata", "content"}),
    MessageType.DELEGATION: frozenset(
        {"action", "delegation_id", "scope", "budget_lamports", "risk_class", "metadata", "content"}
    ),
    MessageType.REQUEST: frozenset(
        {"action", "request", "question", "parameters", "metadata", "content"}
    ),
    MessageType.RESPONSE: frozenset(
        {"action", "status", "result", "error", "metadata", "content"}
    ),
    MessageType.HANDOFF: frozenset(
        {"action", "next_agent_id", "reason", "scope", "metadata", "content"}
    ),
    MessageType.REVIEW_REQUEST: frozenset(
        {"action", "review_scope", "criteria", "metadata", "content"}
    ),
    MessageType.REVIEW_RESULT: frozenset(
        {"action", "result", "score", "findings", "metadata", "content"}
    ),
    MessageType.BLOCK: frozenset(
        {"action", "reason", "blocked_action", "metadata", "content"}
    ),
    MessageType.ESCALATION: frozenset(
        {"action", "reason", "severity", "metadata", "content"}
    ),
    MessageType.DECISION_REQUEST: frozenset(
        {"action", "decision_scope", "options", "reason", "metadata", "content"}
    ),
    MessageType.OWNER_DECISION: frozenset(
        {"action", "decision", "comment", "reason", "metadata", "content"}
    ),
    MessageType.EVIDENCE: frozenset(
        {"action", "uri", "content_hash", "description", "metadata", "content"}
    ),
}

MESSAGE_REQUIRED_PAYLOAD_FIELDS: dict[MessageType, frozenset[str]] = {
    MessageType.COMMAND: frozenset({"action"}),
    MessageType.OWNER_DECISION: frozenset({"decision"}),
    MessageType.BLOCK: frozenset({"reason"}),
    MessageType.ESCALATION: frozenset({"reason"}),
}


def _iter_payload(value: Any, path: str = ""):
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            yield key_path, str(key), child
            yield from _iter_payload(child, key_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _iter_payload(child, f"{path}[{index}]")


def _normalize_action(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def _normalize_key(value: str) -> str:
    with_word_boundaries = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", with_word_boundaries).strip("_").lower()


def _validate_payload(message: AgentMessage) -> None:
    if not isinstance(message.payload, dict):
        raise PolicyViolation("message payload must be a JSON object")

    # Check prohibited content before the shape check so a clear security
    # error is returned even when an attacker uses an otherwise unknown key.
    for path, key, value in _iter_payload(message.payload):
        normalized_key = _normalize_key(key)
        if (
            normalized_key in FORBIDDEN_DIRECT_TRANSFER_KEYS
            or normalized_key.startswith("transfer_")
            or normalized_key.startswith("send_asset")
        ):
            raise PolicyViolation(
                "Agent messages cannot authorize direct asset or Reward transfers: "
                + path
            )
        if normalized_key in {"private_key", "seed_phrase", "mnemonic", "secret"}:
            raise PolicyViolation(f"message payload cannot carry secret material: {path}")
        if normalized_key in {"action", "operation", "command", "blocked_action"}:
            action = _normalize_action(value)
            if action in FORBIDDEN_DIRECT_ACTIONS:
                raise PolicyViolation(
                    f"A2A message cannot directly execute forbidden action: {action}"
                )

    allowed = MESSAGE_PAYLOAD_FIELDS[message.message_type]
    unknown = set(message.payload).difference(allowed)
    if unknown:
        raise PolicyViolation(
            f"payload fields are not allowed for {message.message_type.value}: "
            + ", ".join(sorted(str(item) for item in unknown))
        )

    required = MESSAGE_REQUIRED_PAYLOAD_FIELDS.get(message.message_type, frozenset())
    missing = required.difference(message.payload)
    if missing:
        raise PolicyViolation(
            f"payload is missing required fields: "
            + ", ".join(sorted(missing))
        )


def _as_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PolicyViolation("message expires_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_agent_message(
    message: AgentMessage,
    sender_role: AgentRole,
    *,
    now: datetime | None = None,
) -> AgentMessage:
    if message.schema_version != "1.0":
        raise PolicyViolation(f"unsupported message schema version: {message.schema_version}")
    if not message.message_id or not message.sender_agent_id or not message.recipient_agent_id:
        raise PolicyViolation("message identity fields are required")
    if message.sender_agent_id == message.recipient_agent_id:
        raise PolicyViolation("A2A messages cannot target the sending Agent")
    if message.message_type in TASK_BOUND_TYPES and not message.task_id:
        raise PolicyViolation(
            f"{message.message_type.value} must be linked to a task_id"
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
        raise PolicyViolation(f"{message.message_type.value} requires Owner authority")

    if message.expires_at is not None:
        current = _as_utc(now or datetime.now(timezone.utc))
        if _as_utc(message.expires_at) <= current:
            raise PolicyViolation("message has expired")

    _validate_payload(message)

    return message
