from datetime import datetime, timedelta, timezone

import pytest

from app.evidence_memory import (
    AccessScope,
    EvidenceAccessContext,
    EvidenceIntegrityError,
    EvidenceMemoryService,
    EvidenceRegistration,
    EvidenceSearchQuery,
    InMemoryEvidenceMemoryStore,
    MemoryRegistration,
    MemorySearchQuery,
    MemoryType,
)


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def viewer(
    actor_id: str,
    *,
    owner: bool = False,
    task_ids: tuple[str, ...] = (),
) -> EvidenceAccessContext:
    return EvidenceAccessContext(
        actor_id=actor_id,
        actor_type="OWNER" if owner else "AGENT",
        task_ids=set(task_ids),
    )


def test_registration_masks_pii_and_secrets_before_hashing() -> None:
    service = EvidenceMemoryService(
        InMemoryEvidenceMemoryStore(),
        clock=lambda: NOW,
    )
    record = service.register_evidence(
        EvidenceRegistration(
            evidence_id="e-001",
            title="Research result for owner@example.com",
            content="Call 090-1234-5678; api_key=secret-value; owner@example.com",
            created_by="agent-researcher",
            task_id="T-001",
        )
    )

    assert "owner@example.com" not in record.content
    assert "090-1234-5678" not in record.content
    assert "secret-value" not in record.content
    assert len(record.content_hash) == 64
    assert service.get_evidence("e-001").content_hash == record.content_hash


def test_task_agent_scope_and_retention_are_enforced() -> None:
    service = EvidenceMemoryService(InMemoryEvidenceMemoryStore(), clock=lambda: NOW)
    service.register_evidence(
        EvidenceRegistration(
            evidence_id="e-task",
            title="Task finding",
            content="shared finding",
            created_by="agent-a",
            task_id="T-001",
            access_scope=AccessScope.TASK,
        )
    )
    service.register_evidence(
        EvidenceRegistration(
            evidence_id="e-agent",
            title="Private finding",
            content="private finding",
            created_by="agent-a",
            access_scope=AccessScope.AGENT,
            allowed_agent_ids=["agent-b"],
        )
    )
    service.register_evidence(
        EvidenceRegistration(
            evidence_id="e-expired",
            title="Expired finding",
            content="old finding",
            created_by="agent-a",
            task_id="T-001",
            retention_until=NOW - timedelta(seconds=1),
        )
    )

    agent_a = service.search_evidence(EvidenceSearchQuery(), viewer("agent-a", task_ids=("T-001",)))
    agent_b = service.search_evidence(EvidenceSearchQuery(), viewer("agent-b", task_ids=("T-001",)))
    owner = service.search_evidence(EvidenceSearchQuery(), viewer("owner", owner=True))

    assert {item.evidence_id for item in agent_a} == {"e-task"}
    assert {item.evidence_id for item in agent_b} == {"e-task", "e-agent"}
    assert {item.evidence_id for item in owner} == {"e-task", "e-agent"}


def test_memory_keyword_vector_search_and_model_version() -> None:
    service = EvidenceMemoryService(InMemoryEvidenceMemoryStore(), clock=lambda: NOW)
    for memory_id, embedding in (
        ("m-far", [0.0, 1.0]),
        ("m-near", [0.9, 0.1]),
        ("m-best", [1.0, 0.0]),
    ):
        service.register_memory(
            MemoryRegistration(
                memory_id=memory_id,
                memory_type=MemoryType.FACT,
                title=f"Embedding finding {memory_id}",
                content="vector-search finding",
                created_by="agent-a",
                task_id="T-001",
                embedding_model="embed-v1",
                embedding=embedding,
            )
        )

    context = viewer("agent-a", task_ids=("T-001",))
    keyword = service.search_memory(
        MemorySearchQuery(q="vector-search", embedding_model="embed-v1"),
        context,
    )
    vector = service.search_memory(
        MemorySearchQuery(embedding=[1.0, 0.0], embedding_model="embed-v1"),
        context,
    )

    assert len(keyword) == 3
    assert [item.memory_id for item in vector] == ["m-best", "m-near", "m-far"]
    assert service.search_memory(
        MemorySearchQuery(embedding=[1.0, 0.0], embedding_model="embed-v2"),
        context,
    ) == []


def test_integrity_check_detects_tampering_and_revoke_is_audited() -> None:
    store = InMemoryEvidenceMemoryStore()
    service = EvidenceMemoryService(store, clock=lambda: NOW)
    service.register_evidence(
        EvidenceRegistration(
            evidence_id="e-integrity",
            title="Integrity",
            content="immutable content",
            created_by="agent-a",
            task_id="T-001",
        )
    )
    store.evidence["e-integrity"].content = "tampered content"
    with pytest.raises(EvidenceIntegrityError):
        service.get_evidence("e-integrity")

    store.evidence["e-integrity"].content = "immutable content"
    revoked = service.revoke_evidence(
        "e-integrity",
        actor_id="owner",
        reason="Owner revoked stale evidence",
    )
    assert revoked.status.value == "REVOKED"
    assert service.search_evidence(
        EvidenceSearchQuery(), viewer("owner", owner=True)
    ) == []
    assert [item.action for item in store.audit] == ["REGISTER_EVIDENCE", "REVOKED_EVIDENCE"]


def test_operations_read_model_keeps_evidence_and_memory_as_non_authority_records() -> None:
    service = EvidenceMemoryService(InMemoryEvidenceMemoryStore(), clock=lambda: NOW)
    service.register_evidence(
        EvidenceRegistration(
            evidence_id="e-op",
            title="Evidence operation",
            content="masked operation",
            created_by="agent-a",
            task_id="T-001",
        )
    )
    service.register_memory(
        MemoryRegistration(
            memory_id="m-op",
            memory_type=MemoryType.SUMMARY,
            title="Memory operation",
            content="reusable summary",
            created_by="agent-a",
            task_id="T-001",
            source_evidence_id="e-op",
        )
    )

    records = service.backend.operation_records(  # type: ignore[attr-defined]
        viewer("agent-a", task_ids=("T-001",)), now=NOW
    )
    assert {item.record_id for item in records} == {"e-op", "m-op"}
    assert {item.scope.value for item in records} == {"EVIDENCE", "MEMORY"}
    assert records[0].refs == ["e-op"] or records[1].refs == ["e-op"]
