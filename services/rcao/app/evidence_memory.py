"""Policy-bound Evidence and Memory registration and search.

Evidence and Memory are reusable read models, not authority stores.  Content
is masked before it is hashed or persisted, and every lookup applies the
caller's access scope and retention boundary.  Search results never become a
Task command, Reward decision, Payment, or external action by themselves.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .audit import AuditEvent, AuditWriter, OutboxEvent, OutboxWriter, sanitize
from .repository import PostgresRepository, RepositoryTransaction
from .search import OperationRecord, SearchScope


class EvidenceMemoryError(ValueError):
    """Base error for Evidence and Memory contract violations."""


class EvidenceConflictError(EvidenceMemoryError):
    """An identifier or idempotency key is bound to another record."""


class EvidenceNotFoundError(EvidenceMemoryError):
    """The requested Evidence or Memory record does not exist."""


class EvidenceAccessError(EvidenceMemoryError):
    """A caller is outside the record's access scope."""


class EvidenceIntegrityError(EvidenceMemoryError):
    """Stored content no longer matches its content hash."""


class EvidenceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class MemoryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class MemoryType(str, Enum):
    FACT = "FACT"
    DECISION = "DECISION"
    POLICY = "POLICY"
    EVIDENCE = "EVIDENCE"
    SUMMARY = "SUMMARY"


class AccessScope(str, Enum):
    OWNER_ONLY = "OWNER_ONLY"
    TASK = "TASK"
    AGENT = "AGENT"


class EvidenceAccessContext(BaseModel):
    """Canonical access claims supplied by the authenticated caller."""

    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1)
    actor_type: Literal["OWNER", "AGENT"]
    task_ids: set[str] = Field(default_factory=set)

    @property
    def is_owner(self) -> bool:
        return self.actor_type == "OWNER"


def _utc(value: datetime | None = None) -> datetime:
    parsed = value or datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


_EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(
    r"(?<!\w)(?:\+?\d[\d ()\-]{7,}\d)(?!\w)",
)


def mask_sensitive_content(content: str) -> str:
    """Mask secrets and common PII before content leaves the request boundary."""

    masked = sanitize(content)
    if not isinstance(masked, str):
        raise EvidenceMemoryError("Evidence content must be text")
    masked = _EMAIL_PATTERN.sub("[PII:EMAIL]", masked)
    return _PHONE_PATTERN.sub("[PII:PHONE]", masked)


def _validate_embedding(value: list[float] | None) -> list[float] | None:
    if value is None:
        return None
    if not value:
        raise ValueError("embedding must not be empty")
    if any(not math.isfinite(item) for item in value):
        raise ValueError("embedding must contain finite numbers")
    return value


class EvidenceRegistration(BaseModel):
    """Raw registration command; content is masked by ``from_registration``."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    actor_type: Literal["OWNER", "AGENT"] = "AGENT"
    source_uri: str | None = Field(default=None, max_length=2_000)
    task_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    review_id: str | None = None
    access_scope: AccessScope = AccessScope.TASK
    allowed_agent_ids: list[str] = Field(default_factory=list)
    retention_until: datetime | None = None
    embedding_model: str | None = Field(default=None, max_length=200)
    embedding: list[float] | None = None

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, value: list[float] | None) -> list[float] | None:
        return _validate_embedding(value)

    @model_validator(mode="after")
    def validate_scope_and_idempotency(self) -> "EvidenceRegistration":
        if self.idempotency_key is None:
            self.idempotency_key = self.evidence_id
        if self.access_scope is AccessScope.TASK and not self.task_id:
            raise ValueError("TASK Evidence requires task_id")
        if self.access_scope is AccessScope.AGENT and not self.allowed_agent_ids:
            raise ValueError("AGENT Evidence requires allowed_agent_ids")
        if self.access_scope is not AccessScope.AGENT and self.allowed_agent_ids:
            raise ValueError("allowed_agent_ids require AGENT access scope")
        if self.embedding is not None and not self.embedding_model:
            raise ValueError("embedding_model is required with embedding")
        return self


class MemoryRegistration(BaseModel):
    """Raw Memory registration with explicit source and access boundaries."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)
    memory_type: MemoryType
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    actor_type: Literal["OWNER", "AGENT"] = "AGENT"
    source_evidence_id: str | None = None
    source_uri: str | None = Field(default=None, max_length=2_000)
    task_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    review_id: str | None = None
    access_scope: AccessScope = AccessScope.TASK
    allowed_agent_ids: list[str] = Field(default_factory=list)
    retention_until: datetime | None = None
    embedding_model: str | None = Field(default=None, max_length=200)
    embedding: list[float] | None = None

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, value: list[float] | None) -> list[float] | None:
        return _validate_embedding(value)

    @model_validator(mode="after")
    def validate_scope_and_idempotency(self) -> "MemoryRegistration":
        if self.idempotency_key is None:
            self.idempotency_key = self.memory_id
        if self.access_scope is AccessScope.TASK and not self.task_id:
            raise ValueError("TASK Memory requires task_id")
        if self.access_scope is AccessScope.AGENT and not self.allowed_agent_ids:
            raise ValueError("AGENT Memory requires allowed_agent_ids")
        if self.access_scope is not AccessScope.AGENT and self.allowed_agent_ids:
            raise ValueError("allowed_agent_ids require AGENT access scope")
        if self.embedding is not None and not self.embedding_model:
            raise ValueError("embedding_model is required with embedding")
        return self


class EvidenceRecord(BaseModel):
    """Normalized, masked Evidence record."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    idempotency_key: str
    title: str
    content: str
    content_hash: str = Field(min_length=64, max_length=64)
    created_by: str
    actor_type: Literal["OWNER", "AGENT"]
    source_uri: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    review_id: str | None = None
    access_scope: AccessScope
    allowed_agent_ids: list[str] = Field(default_factory=list)
    retention_until: datetime | None = None
    embedding_model: str | None = None
    embedding: list[float] | None = None
    status: EvidenceStatus = EvidenceStatus.ACTIVE
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_registration(
        cls,
        registration: EvidenceRegistration,
        *,
        now: datetime | None = None,
    ) -> "EvidenceRecord":
        masked = mask_sensitive_content(registration.content)
        timestamp = _utc(now)
        return cls(
            evidence_id=registration.evidence_id,
            idempotency_key=registration.idempotency_key or registration.evidence_id,
            title=mask_sensitive_content(registration.title),
            content=masked,
            content_hash=_hash_content(masked),
            created_by=registration.created_by,
            actor_type=registration.actor_type,
            source_uri=(
                mask_sensitive_content(registration.source_uri)
                if registration.source_uri
                else None
            ),
            task_id=registration.task_id,
            run_id=registration.run_id,
            message_id=registration.message_id,
            review_id=registration.review_id,
            access_scope=registration.access_scope,
            allowed_agent_ids=list(registration.allowed_agent_ids),
            retention_until=registration.retention_until,
            embedding_model=registration.embedding_model,
            embedding=registration.embedding,
            created_at=timestamp,
            updated_at=timestamp,
        )


class MemoryRecord(BaseModel):
    """Normalized, masked reusable Memory record."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    idempotency_key: str
    memory_type: MemoryType
    title: str
    content: str
    content_hash: str = Field(min_length=64, max_length=64)
    created_by: str
    actor_type: Literal["OWNER", "AGENT"]
    source_evidence_id: str | None = None
    source_uri: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    review_id: str | None = None
    access_scope: AccessScope
    allowed_agent_ids: list[str] = Field(default_factory=list)
    retention_until: datetime | None = None
    embedding_model: str | None = None
    embedding: list[float] | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_registration(
        cls,
        registration: MemoryRegistration,
        *,
        now: datetime | None = None,
    ) -> "MemoryRecord":
        masked = mask_sensitive_content(registration.content)
        timestamp = _utc(now)
        return cls(
            memory_id=registration.memory_id,
            idempotency_key=registration.idempotency_key or registration.memory_id,
            memory_type=registration.memory_type,
            title=mask_sensitive_content(registration.title),
            content=masked,
            content_hash=_hash_content(masked),
            created_by=registration.created_by,
            actor_type=registration.actor_type,
            source_evidence_id=registration.source_evidence_id,
            source_uri=(
                mask_sensitive_content(registration.source_uri)
                if registration.source_uri
                else None
            ),
            task_id=registration.task_id,
            run_id=registration.run_id,
            message_id=registration.message_id,
            review_id=registration.review_id,
            access_scope=registration.access_scope,
            allowed_agent_ids=list(registration.allowed_agent_ids),
            retention_until=registration.retention_until,
            embedding_model=registration.embedding_model,
            embedding=registration.embedding,
            created_at=timestamp,
            updated_at=timestamp,
        )


class EvidenceSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = ""
    task_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    review_id: str | None = None
    status: EvidenceStatus | None = EvidenceStatus.ACTIVE
    embedding_model: str | None = None
    embedding: list[float] | None = None
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, value: list[float] | None) -> list[float] | None:
        return _validate_embedding(value)


class MemorySearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = ""
    memory_type: MemoryType | None = None
    task_id: str | None = None
    run_id: str | None = None
    message_id: str | None = None
    review_id: str | None = None
    status: MemoryStatus | None = MemoryStatus.ACTIVE
    embedding_model: str | None = None
    embedding: list[float] | None = None
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("embedding")
    @classmethod
    def validate_embedding(cls, value: list[float] | None) -> list[float] | None:
        return _validate_embedding(value)


def verify_content_integrity(record: EvidenceRecord | MemoryRecord) -> None:
    if _hash_content(record.content) != record.content_hash:
        raise EvidenceIntegrityError(f"content hash mismatch: {record_id(record)}")


def record_id(record: EvidenceRecord | MemoryRecord) -> str:
    return record.evidence_id if isinstance(record, EvidenceRecord) else record.memory_id


def _is_visible(
    record: EvidenceRecord | MemoryRecord,
    viewer: EvidenceAccessContext,
    *,
    now: datetime,
) -> bool:
    if record.status is not (EvidenceStatus.ACTIVE if isinstance(record, EvidenceRecord) else MemoryStatus.ACTIVE):
        return False
    if record.retention_until is not None and _utc(record.retention_until) <= now:
        return False
    if viewer.is_owner:
        return True
    if record.access_scope is AccessScope.OWNER_ONLY:
        return False
    if record.access_scope is AccessScope.TASK:
        return record.task_id is not None and record.task_id in viewer.task_ids
    return viewer.actor_id in record.allowed_agent_ids


def _text_matches(record: EvidenceRecord | MemoryRecord, query: str) -> bool:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return True
    haystack = " ".join(
        item.casefold()
        for item in (record.title, record.content, record.source_uri or "")
    )
    return all(term in haystack for term in terms)


def _cosine_distance(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right):
        return None
    denominator = math.sqrt(sum(item * item for item in left)) * math.sqrt(
        sum(item * item for item in right)
    )
    if denominator == 0:
        return None
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / denominator
    return 1.0 - similarity


def _state(record: EvidenceRecord | MemoryRecord) -> dict[str, Any]:
    return record.model_dump(
        mode="json",
        exclude={"content", "embedding"},
    )


@dataclass(frozen=True)
class EvidenceMemoryAuditRecord:
    record_type: Literal["EVIDENCE", "MEMORY"]
    record_id: str
    action: str
    actor_id: str
    reason: str
    content_hash: str
    timestamp: datetime
    before: Mapping[str, Any] = field(default_factory=dict)
    after: Mapping[str, Any] = field(default_factory=dict)


class EvidenceMemoryBackend(Protocol):
    def register_evidence(self, record: EvidenceRecord, *, reason: str) -> EvidenceRecord: ...

    def register_memory(self, record: MemoryRecord, *, reason: str) -> MemoryRecord: ...

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None: ...

    def get_memory(self, memory_id: str) -> MemoryRecord | None: ...

    def search_evidence(
        self,
        query: EvidenceSearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[EvidenceRecord]: ...

    def search_memory(
        self,
        query: MemorySearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[MemoryRecord]: ...

    def set_evidence_status(
        self,
        evidence_id: str,
        status: EvidenceStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> EvidenceRecord: ...

    def set_memory_status(
        self,
        memory_id: str,
        status: MemoryStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> MemoryRecord: ...


class InMemoryEvidenceMemoryStore:
    """Offline reference backend with the same access and integrity rules."""

    def __init__(self) -> None:
        self.evidence: dict[str, EvidenceRecord] = {}
        self.memories: dict[str, MemoryRecord] = {}
        self.audit: list[EvidenceMemoryAuditRecord] = []

    def _register(
        self,
        record: EvidenceRecord | MemoryRecord,
        *,
        reason: str,
    ) -> EvidenceRecord | MemoryRecord:
        verify_content_integrity(record)
        collection = self.evidence if isinstance(record, EvidenceRecord) else self.memories
        key = record_id(record)
        existing = collection.get(key)
        if existing is not None:
            if existing.content_hash != record.content_hash or existing.idempotency_key != record.idempotency_key:
                raise EvidenceConflictError(f"record is already bound: {key}")
            return existing.model_copy(deep=True)
        by_key = next(
            (item for item in collection.values() if item.idempotency_key == record.idempotency_key),
            None,
        )
        if by_key is not None:
            if record_id(by_key) != key or by_key.content_hash != record.content_hash:
                raise EvidenceConflictError(
                    f"idempotency key is already bound: {record.idempotency_key}"
                )
            return by_key.model_copy(deep=True)
        collection[key] = record.model_copy(deep=True)
        self._audit(record, "REGISTER_EVIDENCE" if isinstance(record, EvidenceRecord) else "REGISTER_MEMORY", reason, {}, _state(record))
        return record.model_copy(deep=True)

    def register_evidence(self, record: EvidenceRecord, *, reason: str) -> EvidenceRecord:
        return self._register(record, reason=reason)  # type: ignore[return-value]

    def register_memory(self, record: MemoryRecord, *, reason: str) -> MemoryRecord:
        return self._register(record, reason=reason)  # type: ignore[return-value]

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        record = self.evidence.get(evidence_id)
        if record is None:
            return None
        verify_content_integrity(record)
        return record.model_copy(deep=True)

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        record = self.memories.get(memory_id)
        if record is None:
            return None
        verify_content_integrity(record)
        return record.model_copy(deep=True)

    def _search(
        self,
        records: list[EvidenceRecord] | list[MemoryRecord],
        query: EvidenceSearchQuery | MemorySearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[EvidenceRecord] | list[MemoryRecord]:
        visible: list[EvidenceRecord | MemoryRecord] = []
        for record in records:
            verify_content_integrity(record)
            if not _is_visible(record, viewer, now=_utc(now)):
                continue
            if query.status is not None and record.status is not query.status:
                continue
            if isinstance(query, MemorySearchQuery) and query.memory_type is not None and record.memory_type is not query.memory_type:
                continue
            if query.task_id is not None and record.task_id != query.task_id:
                continue
            if query.run_id is not None and record.run_id != query.run_id:
                continue
            if query.message_id is not None and record.message_id != query.message_id:
                continue
            if query.review_id is not None and record.review_id != query.review_id:
                continue
            if query.embedding_model is not None and record.embedding_model != query.embedding_model:
                continue
            if not _text_matches(record, query.q):
                continue
            if query.embedding is not None:
                if record.embedding is None or _cosine_distance(record.embedding, query.embedding) is None:
                    continue
            visible.append(record)
        if query.embedding is not None:
            visible.sort(
                key=lambda item: (
                    _cosine_distance(item.embedding or [], query.embedding or [])
                    if _cosine_distance(item.embedding or [], query.embedding or []) is not None
                    else float("inf"),
                    -_utc(item.created_at).timestamp(),
                    record_id(item),
                )
            )
        else:
            visible.sort(key=lambda item: (-_utc(item.created_at).timestamp(), record_id(item)))
        return [item.model_copy(deep=True) for item in visible[: query.limit]]

    def search_evidence(
        self,
        query: EvidenceSearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[EvidenceRecord]:
        return self._search(list(self.evidence.values()), query, viewer, now=now)  # type: ignore[return-value]

    def search_memory(
        self,
        query: MemorySearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[MemoryRecord]:
        return self._search(list(self.memories.values()), query, viewer, now=now)  # type: ignore[return-value]

    def set_evidence_status(
        self,
        evidence_id: str,
        status: EvidenceStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> EvidenceRecord:
        record = self.get_evidence(evidence_id)
        if record is None:
            raise EvidenceNotFoundError(evidence_id)
        if record.status is not EvidenceStatus.ACTIVE:
            raise EvidenceConflictError("only ACTIVE Evidence can change status")
        updated = record.model_copy(update={"status": status, "updated_at": datetime.now(timezone.utc)})
        self.evidence[evidence_id] = updated
        self._audit(record, f"{status.value}_EVIDENCE", reason, _state(record), _state(updated), actor_id=actor_id)
        return updated.model_copy(deep=True)

    def set_memory_status(
        self,
        memory_id: str,
        status: MemoryStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> MemoryRecord:
        record = self.get_memory(memory_id)
        if record is None:
            raise EvidenceNotFoundError(memory_id)
        if record.status is not MemoryStatus.ACTIVE:
            raise EvidenceConflictError("only ACTIVE Memory can change status")
        updated = record.model_copy(update={"status": status, "updated_at": datetime.now(timezone.utc)})
        self.memories[memory_id] = updated
        self._audit(record, f"{status.value}_MEMORY", reason, _state(record), _state(updated), actor_id=actor_id)
        return updated.model_copy(deep=True)

    def _audit(
        self,
        record: EvidenceRecord | MemoryRecord,
        action: str,
        reason: str,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        *,
        actor_id: str | None = None,
    ) -> None:
        self.audit.append(
            EvidenceMemoryAuditRecord(
                record_type="EVIDENCE" if isinstance(record, EvidenceRecord) else "MEMORY",
                record_id=record_id(record),
                action=action,
                actor_id=actor_id or record.created_by,
                reason=reason,
                content_hash=record.content_hash,
                timestamp=datetime.now(timezone.utc),
                before=before,
                after=after,
            )
        )

    def operation_records(
        self,
        viewer: EvidenceAccessContext,
        *,
        now: datetime | None = None,
    ) -> list[OperationRecord]:
        timestamp = _utc(now)
        records: list[OperationRecord] = []
        for record in self.search_evidence(EvidenceSearchQuery(), viewer, now=timestamp):
            records.append(
                OperationRecord(
                    record_id=record.evidence_id,
                    scope=SearchScope.EVIDENCE,
                    title=record.title,
                    body=record.content,
                    task_id=record.task_id,
                    run_id=record.run_id,
                    agent_id=record.created_by,
                    status=record.status.value,
                    created_at=_utc(record.created_at).isoformat(),
                    refs=[item for item in (record.message_id, record.review_id) if item],
                )
            )
        for record in self.search_memory(MemorySearchQuery(), viewer, now=timestamp):
            records.append(
                OperationRecord(
                    record_id=record.memory_id,
                    scope=SearchScope.MEMORY,
                    title=record.title,
                    body=record.content,
                    task_id=record.task_id,
                    run_id=record.run_id,
                    agent_id=record.created_by,
                    status=record.status.value,
                    created_at=_utc(record.created_at).isoformat(),
                    refs=[item for item in (record.source_evidence_id, record.message_id, record.review_id) if item],
                )
            )
        return sorted(records, key=lambda item: item.created_at, reverse=True)


class EvidenceMemoryService:
    """Application facade that normalizes content before backend persistence."""

    def __init__(
        self,
        backend: EvidenceMemoryBackend | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.backend = backend or InMemoryEvidenceMemoryStore()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def register_evidence(self, registration: EvidenceRegistration) -> EvidenceRecord:
        return self.backend.register_evidence(
            EvidenceRecord.from_registration(registration, now=_utc(self._clock())),
            reason="Evidence registered after content masking",
        )

    def register_memory(self, registration: MemoryRegistration) -> MemoryRecord:
        return self.backend.register_memory(
            MemoryRecord.from_registration(registration, now=_utc(self._clock())),
            reason="Memory registered after content masking",
        )

    def get_evidence(self, evidence_id: str) -> EvidenceRecord:
        record = self.backend.get_evidence(evidence_id)
        if record is None:
            raise EvidenceNotFoundError(evidence_id)
        verify_content_integrity(record)
        return record

    def get_memory(self, memory_id: str) -> MemoryRecord:
        record = self.backend.get_memory(memory_id)
        if record is None:
            raise EvidenceNotFoundError(memory_id)
        verify_content_integrity(record)
        return record

    def search_evidence(
        self,
        query: EvidenceSearchQuery,
        viewer: EvidenceAccessContext,
    ) -> list[EvidenceRecord]:
        return self.backend.search_evidence(query, viewer, now=_utc(self._clock()))

    def search_memory(
        self,
        query: MemorySearchQuery,
        viewer: EvidenceAccessContext,
    ) -> list[MemoryRecord]:
        return self.backend.search_memory(query, viewer, now=_utc(self._clock()))

    def revoke_evidence(self, evidence_id: str, *, actor_id: str, reason: str) -> EvidenceRecord:
        return self.backend.set_evidence_status(
            evidence_id,
            EvidenceStatus.REVOKED,
            actor_id=actor_id,
            reason=reason,
        )

    def expire_evidence(self, evidence_id: str, *, actor_id: str, reason: str) -> EvidenceRecord:
        return self.backend.set_evidence_status(
            evidence_id,
            EvidenceStatus.EXPIRED,
            actor_id=actor_id,
            reason=reason,
        )

    def revoke_memory(self, memory_id: str, *, actor_id: str, reason: str) -> MemoryRecord:
        return self.backend.set_memory_status(
            memory_id,
            MemoryStatus.REVOKED,
            actor_id=actor_id,
            reason=reason,
        )


EVIDENCE_RECORD_COLUMNS = (
    "id",
    "idempotency_key",
    "title",
    "content",
    "content_hash",
    "created_by",
    "actor_type",
    "source_uri",
    "task_id",
    "run_id",
    "message_id",
    "review_id",
    "access_scope",
    "allowed_agent_ids",
    "retention_until",
    "embedding_model",
    "embedding",
    "status",
    "created_at",
    "updated_at",
)
MEMORY_RECORD_COLUMNS = (
    "id",
    "idempotency_key",
    "memory_type",
    "title",
    "content",
    "content_hash",
    "created_by",
    "actor_type",
    "source_evidence_id",
    "source_uri",
    "task_id",
    "run_id",
    "message_id",
    "review_id",
    "access_scope",
    "allowed_agent_ids",
    "retention_until",
    "embedding_model",
    "embedding",
    "status",
    "created_at",
    "updated_at",
)


def _values(row: Any, columns: tuple[str, ...]) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip(columns, row, strict=True))


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise EvidenceMemoryError("stored Evidence/Memory JSON is invalid") from exc
    return value


def _vector_value(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [float(item) for item in value.strip("[]").split(",") if item.strip()]
    if not isinstance(value, (list, tuple)):
        raise EvidenceMemoryError("stored embedding is invalid")
    return [float(item) for item in value]


def _vector_literal(value: list[float] | None) -> str | None:
    if value is None:
        return None
    return "[" + ",".join(format(item, ".12g") for item in value) + "]"


def _evidence_from_row(row: Any) -> EvidenceRecord:
    values = _values(row, EVIDENCE_RECORD_COLUMNS)
    return EvidenceRecord(
        evidence_id=str(values["id"]),
        idempotency_key=str(values["idempotency_key"]),
        title=str(values["title"]),
        content=str(values["content"]),
        content_hash=str(values["content_hash"]),
        created_by=str(values["created_by"]),
        actor_type=str(values["actor_type"]),
        source_uri=values.get("source_uri"),
        task_id=values.get("task_id"),
        run_id=values.get("run_id"),
        message_id=values.get("message_id"),
        review_id=values.get("review_id"),
        access_scope=AccessScope(str(values["access_scope"])),
        allowed_agent_ids=_json_value(values.get("allowed_agent_ids"), []),
        retention_until=values.get("retention_until"),
        embedding_model=values.get("embedding_model"),
        embedding=_vector_value(values.get("embedding")),
        status=EvidenceStatus(str(values["status"])),
        created_at=values["created_at"],
        updated_at=values["updated_at"],
    )


def _memory_from_row(row: Any) -> MemoryRecord:
    values = _values(row, MEMORY_RECORD_COLUMNS)
    return MemoryRecord(
        memory_id=str(values["id"]),
        idempotency_key=str(values["idempotency_key"]),
        memory_type=MemoryType(str(values["memory_type"])),
        title=str(values["title"]),
        content=str(values["content"]),
        content_hash=str(values["content_hash"]),
        created_by=str(values["created_by"]),
        actor_type=str(values["actor_type"]),
        source_evidence_id=values.get("source_evidence_id"),
        source_uri=values.get("source_uri"),
        task_id=values.get("task_id"),
        run_id=values.get("run_id"),
        message_id=values.get("message_id"),
        review_id=values.get("review_id"),
        access_scope=AccessScope(str(values["access_scope"])),
        allowed_agent_ids=_json_value(values.get("allowed_agent_ids"), []),
        retention_until=values.get("retention_until"),
        embedding_model=values.get("embedding_model"),
        embedding=_vector_value(values.get("embedding")),
        status=MemoryStatus(str(values["status"])),
        created_at=values["created_at"],
        updated_at=values["updated_at"],
    )


class EvidenceMemoryRepository:
    """PostgreSQL adapter; each state change emits Audit and Outbox together."""

    def __init__(self, transaction: RepositoryTransaction) -> None:
        self.transaction = transaction

    def _find_by_key(self, table: str, columns: tuple[str, ...], key: str) -> Any:
        row = self.transaction.fetch_one(
            f"SELECT {', '.join(columns)} FROM {table} WHERE idempotency_key = %s FOR UPDATE",
            (key,),
        )
        return row

    def register_evidence(self, record: EvidenceRecord, *, reason: str) -> EvidenceRecord:
        existing = self._find_by_key("mvp_evidence", EVIDENCE_RECORD_COLUMNS, record.idempotency_key)
        if existing is not None:
            current = _evidence_from_row(existing)
            if current.evidence_id != record.evidence_id or current.content_hash != record.content_hash:
                raise EvidenceConflictError("Evidence idempotency key is already bound")
            return current
        self.transaction.execute(
            """
            INSERT INTO mvp_evidence
              (id, idempotency_key, title, content, content_hash, created_by,
               actor_type, source_uri, task_id, run_id, message_id, review_id,
               access_scope, allowed_agent_ids, retention_until, embedding_model,
               embedding, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s, %s::vector, %s, %s, %s)
            """,
            self._evidence_params(record),
        )
        self._emit(record, "REGISTER_EVIDENCE", reason, {}, _state(record))
        return record

    def register_memory(self, record: MemoryRecord, *, reason: str) -> MemoryRecord:
        existing = self._find_by_key("mvp_memory_items", MEMORY_RECORD_COLUMNS, record.idempotency_key)
        if existing is not None:
            current = _memory_from_row(existing)
            if current.memory_id != record.memory_id or current.content_hash != record.content_hash:
                raise EvidenceConflictError("Memory idempotency key is already bound")
            return current
        self.transaction.execute(
            """
            INSERT INTO mvp_memory_items
              (id, idempotency_key, memory_type, title, content, content_hash,
               created_by, actor_type, source_evidence_id, source_uri, task_id,
               run_id, message_id, review_id, access_scope, allowed_agent_ids,
               retention_until, embedding_model, embedding, status, created_at,
               updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s::jsonb, %s, %s, %s::vector, %s, %s, %s)
            """,
            self._memory_params(record),
        )
        self._emit(record, "REGISTER_MEMORY", reason, {}, _state(record))
        return record

    @staticmethod
    def _evidence_params(record: EvidenceRecord) -> tuple[Any, ...]:
        return (
            record.evidence_id,
            record.idempotency_key,
            record.title,
            record.content,
            record.content_hash,
            record.created_by,
            record.actor_type,
            record.source_uri,
            record.task_id,
            record.run_id,
            record.message_id,
            record.review_id,
            record.access_scope.value,
            json.dumps(record.allowed_agent_ids, ensure_ascii=False),
            record.retention_until,
            record.embedding_model,
            _vector_literal(record.embedding),
            record.status.value,
            record.created_at,
            record.updated_at,
        )

    @staticmethod
    def _memory_params(record: MemoryRecord) -> tuple[Any, ...]:
        return (
            record.memory_id,
            record.idempotency_key,
            record.memory_type.value,
            record.title,
            record.content,
            record.content_hash,
            record.created_by,
            record.actor_type,
            record.source_evidence_id,
            record.source_uri,
            record.task_id,
            record.run_id,
            record.message_id,
            record.review_id,
            record.access_scope.value,
            json.dumps(record.allowed_agent_ids, ensure_ascii=False),
            record.retention_until,
            record.embedding_model,
            _vector_literal(record.embedding),
            record.status.value,
            record.created_at,
            record.updated_at,
        )

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        row = self.transaction.fetch_one(
            f"SELECT {', '.join(EVIDENCE_RECORD_COLUMNS)} FROM mvp_evidence WHERE id = %s",
            (evidence_id,),
        )
        if row is None:
            return None
        record = _evidence_from_row(row)
        verify_content_integrity(record)
        return record

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        row = self.transaction.fetch_one(
            f"SELECT {', '.join(MEMORY_RECORD_COLUMNS)} FROM mvp_memory_items WHERE id = %s",
            (memory_id,),
        )
        if row is None:
            return None
        record = _memory_from_row(row)
        verify_content_integrity(record)
        return record

    def set_evidence_status(
        self,
        evidence_id: str,
        status: EvidenceStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> EvidenceRecord:
        current = self.get_evidence(evidence_id)
        if current is None:
            raise EvidenceNotFoundError(evidence_id)
        if current.status is not EvidenceStatus.ACTIVE:
            raise EvidenceConflictError("only ACTIVE Evidence can change status")
        row = self.transaction.fetch_one(
            f"""
            UPDATE mvp_evidence
            SET status = %s, updated_at = now()
            WHERE id = %s AND status = 'ACTIVE'
            RETURNING {', '.join(EVIDENCE_RECORD_COLUMNS)}
            """,
            (status.value, evidence_id),
        )
        if row is None:
            raise EvidenceConflictError("Evidence status changed concurrently")
        updated = _evidence_from_row(row)
        self._emit(updated, f"{status.value}_EVIDENCE", reason, _state(current), _state(updated), actor_id=actor_id)
        return updated

    def set_memory_status(
        self,
        memory_id: str,
        status: MemoryStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> MemoryRecord:
        current = self.get_memory(memory_id)
        if current is None:
            raise EvidenceNotFoundError(memory_id)
        if current.status is not MemoryStatus.ACTIVE:
            raise EvidenceConflictError("only ACTIVE Memory can change status")
        row = self.transaction.fetch_one(
            f"""
            UPDATE mvp_memory_items
            SET status = %s, updated_at = now()
            WHERE id = %s AND status = 'ACTIVE'
            RETURNING {', '.join(MEMORY_RECORD_COLUMNS)}
            """,
            (status.value, memory_id),
        )
        if row is None:
            raise EvidenceConflictError("Memory status changed concurrently")
        updated = _memory_from_row(row)
        self._emit(updated, f"{status.value}_MEMORY", reason, _state(current), _state(updated), actor_id=actor_id)
        return updated

    def search_evidence(
        self,
        query: EvidenceSearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[EvidenceRecord]:
        clauses, params = _search_access(viewer, now)
        _append_common_filters(clauses, params, query)
        if query.q.strip():
            clauses.append("to_tsvector('simple', title || ' ' || content) @@ plainto_tsquery('simple', %s)")
            params.append(query.q)
        order = "created_at DESC, id ASC"
        if query.embedding is not None:
            clauses.append("embedding IS NOT NULL")
            params.append(_vector_literal(query.embedding))
            order = "embedding <=> %s::vector ASC, created_at DESC, id ASC"
        rows = self.transaction.fetch_all(
            f"""
            SELECT {', '.join(EVIDENCE_RECORD_COLUMNS)}
            FROM mvp_evidence
            WHERE {' AND '.join(clauses)}
            ORDER BY {order}
            LIMIT %s
            """,
            (*params, query.limit),
        )
        return [_evidence_from_row(row) for row in rows]

    def search_memory(
        self,
        query: MemorySearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[MemoryRecord]:
        clauses, params = _search_access(viewer, now)
        _append_common_filters(clauses, params, query)
        if query.memory_type is not None:
            clauses.append("memory_type = %s")
            params.append(query.memory_type.value)
        if query.q.strip():
            clauses.append("to_tsvector('simple', title || ' ' || content) @@ plainto_tsquery('simple', %s)")
            params.append(query.q)
        order = "created_at DESC, id ASC"
        if query.embedding is not None:
            clauses.append("embedding IS NOT NULL")
            params.append(_vector_literal(query.embedding))
            order = "embedding <=> %s::vector ASC, created_at DESC, id ASC"
        rows = self.transaction.fetch_all(
            f"""
            SELECT {', '.join(MEMORY_RECORD_COLUMNS)}
            FROM mvp_memory_items
            WHERE {' AND '.join(clauses)}
            ORDER BY {order}
            LIMIT %s
            """,
            (*params, query.limit),
        )
        return [_memory_from_row(row) for row in rows]

    def _emit(
        self,
        record: EvidenceRecord | MemoryRecord,
        action: str,
        reason: str,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        *,
        actor_id: str | None = None,
    ) -> None:
        target_id = record_id(record)
        record_type = "EVIDENCE" if isinstance(record, EvidenceRecord) else "MEMORY"
        correlation_id = record.run_id or record.task_id or f"{record_type.lower()}:{target_id}"
        AuditWriter.append(
            self.transaction,
            AuditEvent(
                event_id=f"audit-{uuid4().hex}",
                event_version=1,
                event_type=record_type,
                actor_id=actor_id or record.created_by,
                actor_type=record.actor_type,
                action=action,
                target_type=record_type,
                target_id=target_id,
                before_state=before,
                after_state=after,
                policy_result="ALLOW",
                reason=reason,
                correlation_id=correlation_id,
                transaction_id=correlation_id,
                task_id=record.task_id,
                run_id=record.run_id,
                message_id=record.message_id,
                evidence_hash=record.content_hash,
            ),
        )
        OutboxWriter.enqueue(
            self.transaction,
            OutboxEvent(
                event_id=f"outbox-{uuid4().hex}",
                aggregate_type=record_type,
                aggregate_id=target_id,
                event_type=action,
                idempotency_key=f"{record_type.lower()}:{target_id}:{action}",
                payload={"action": action, "record": after, "correlation_id": correlation_id},
                event_version=1,
                transaction_id=correlation_id,
            ),
        )


def _search_access(
    viewer: EvidenceAccessContext,
    now: datetime,
) -> tuple[list[str], list[Any]]:
    clauses = ["(retention_until IS NULL OR retention_until > %s)"]
    params: list[Any] = [_utc(now)]
    if viewer.is_owner:
        return clauses, params
    clauses.append(
        "((access_scope = 'TASK' AND task_id = ANY(%s::text[])) "
        "OR (access_scope = 'AGENT' AND allowed_agent_ids ? %s))"
    )
    params.extend([list(viewer.task_ids), viewer.actor_id])
    return clauses, params


def _append_common_filters(
    clauses: list[str],
    params: list[Any],
    query: EvidenceSearchQuery | MemorySearchQuery,
) -> None:
    if query.status is not None:
        clauses.append("status = %s")
        params.append(query.status.value)
    for column in ("task_id", "run_id", "message_id", "review_id"):
        value = getattr(query, column)
        if value is not None:
            clauses.append(f"{column} = %s")
            params.append(value)
    if query.embedding_model is not None:
        clauses.append("embedding_model = %s")
        params.append(query.embedding_model)


class PersistentEvidenceMemoryStore:
    """Facade that keeps Evidence/Memory, Audit, and Outbox in one UoW."""

    def __init__(self, repository: PostgresRepository) -> None:
        self.repository = repository

    def register_evidence(self, record: EvidenceRecord, *, reason: str) -> EvidenceRecord:
        return self.repository.run(lambda tx: EvidenceMemoryRepository(tx).register_evidence(record, reason=reason))

    def register_memory(self, record: MemoryRecord, *, reason: str) -> MemoryRecord:
        return self.repository.run(lambda tx: EvidenceMemoryRepository(tx).register_memory(record, reason=reason))

    def get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        return self.repository.run(lambda tx: EvidenceMemoryRepository(tx).get_evidence(evidence_id))

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        return self.repository.run(lambda tx: EvidenceMemoryRepository(tx).get_memory(memory_id))

    def search_evidence(
        self,
        query: EvidenceSearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[EvidenceRecord]:
        return self.repository.run(lambda tx: EvidenceMemoryRepository(tx).search_evidence(query, viewer, now=now))

    def search_memory(
        self,
        query: MemorySearchQuery,
        viewer: EvidenceAccessContext,
        *,
        now: datetime,
    ) -> list[MemoryRecord]:
        return self.repository.run(lambda tx: EvidenceMemoryRepository(tx).search_memory(query, viewer, now=now))

    def set_evidence_status(
        self,
        evidence_id: str,
        status: EvidenceStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> EvidenceRecord:
        return self.repository.run(lambda tx: EvidenceMemoryRepository(tx).set_evidence_status(evidence_id, status, actor_id=actor_id, reason=reason))

    def set_memory_status(
        self,
        memory_id: str,
        status: MemoryStatus,
        *,
        actor_id: str,
        reason: str,
    ) -> MemoryRecord:
        return self.repository.run(lambda tx: EvidenceMemoryRepository(tx).set_memory_status(memory_id, status, actor_id=actor_id, reason=reason))
