from dataclasses import replace

import pytest

from app.audit import (
    AuditEvent,
    OutboxEvent,
    ReplayIntegrityError,
    ReplaySafetyError,
    replay_audit_events,
)


def event(*, action: str = "TRANSITION_TASK", **changes: object) -> AuditEvent:
    return AuditEvent(
        event_id=str(changes.get("event_id", "audit-001")),
        event_version=1,
        event_type="TASK_TRANSITION",
        actor_id="owner-local",
        actor_type="OWNER",
        action=action,
        target_type="TASK",
        target_id="T-001",
        before_state=changes.get("before_state", {}),
        after_state=changes.get("after_state", {"status": "DRAFT"}),
        policy_result="ALLOW",
        reason=str(changes.get("reason", "Owner transition")),
        correlation_id="corr-001",
        task_id="T-001",
    )


def test_audit_event_redacts_secrets_before_hashing_and_persistence() -> None:
    audit = event(
        before_state={"status": "DRAFT", "api_token": "super-secret"},
        after_state={"status": "APPROVED", "nested": {"private_key": "key-value"}},
        reason="Bearer live-token",
    )

    record = audit.to_record()

    assert record["before_state"]["api_token"] == "[REDACTED]"
    assert record["after_state"]["nested"]["private_key"] == "[REDACTED]"
    assert record["reason"] == "[REDACTED]"
    assert len(record["event_hash"]) == 64
    assert "super-secret" not in str(record)
    assert "live-token" not in str(record)


def test_replay_reconstructs_state_without_external_execution() -> None:
    first = event(after_state={"status": "DRAFT"}).with_integrity()
    second = event(
        event_id="audit-002",
        before_state={"status": "DRAFT"},
        after_state={"status": "APPROVED"},
    ).with_integrity()

    result = replay_audit_events([first, second])

    assert result.events_processed == 2
    assert result.states["TASK:T-001"] == {"status": "APPROVED"}
    assert result.correlations == ("corr-001", "corr-001")


def test_replay_rejects_tampered_event() -> None:
    audit = event().with_integrity()
    tampered = replace(audit, after_state={"status": "CANCELLED"})

    with pytest.raises(ReplayIntegrityError, match="hash mismatch"):
        replay_audit_events([tampered])


def test_replay_rejects_external_side_effect_actions() -> None:
    with pytest.raises(ReplaySafetyError, match="external action"):
        replay_audit_events([event(action="SIGN_TRANSACTION")])


def test_outbox_record_redacts_payload_and_tracks_delivery_state() -> None:
    outbox = OutboxEvent(
        event_id="event-001",
        aggregate_type="TASK",
        aggregate_id="T-001",
        event_type="TASK_TRANSITION",
        idempotency_key="cmd-001",
        payload={"authorization": "Bearer secret", "status": "APPROVED"},
        transaction_id="corr-001",
    )

    record = outbox.to_record()

    assert record["payload"]["authorization"] == "[REDACTED]"
    assert record["delivery_status"] == "PENDING"
    assert record["delivery_attempts"] == 0
