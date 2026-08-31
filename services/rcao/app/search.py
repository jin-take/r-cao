from enum import Enum

from pydantic import BaseModel, Field


class SearchScope(str, Enum):
    ALL = "ALL"
    TASKS = "TASKS"
    RUNS = "RUNS"
    MESSAGES = "MESSAGES"
    EVIDENCE = "EVIDENCE"
    MEMORY = "MEMORY"
    AUDIT = "AUDIT"


class OperationRecord(BaseModel):
    record_id: str
    scope: SearchScope
    title: str
    body: str = ""
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    status: str | None = None
    created_at: str
    refs: list[str] = Field(default_factory=list)


class SearchQuery(BaseModel):
    q: str = ""
    scope: SearchScope = SearchScope.ALL
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    status: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class SearchResponse(BaseModel):
    query: SearchQuery
    hits: list[OperationRecord]
    total: int


class InMemoryOperationSearch:
    """Deterministic read model used until the PostgreSQL repository is wired."""

    def __init__(self, records: list[OperationRecord] | None = None) -> None:
        self.records = records or []

    def search(self, query: SearchQuery) -> SearchResponse:
        needle = query.q.casefold()
        matches = [
            record
            for record in self.records
            if (query.scope is SearchScope.ALL or record.scope is query.scope)
            and (not query.task_id or record.task_id == query.task_id)
            and (not query.run_id or record.run_id == query.run_id)
            and (not query.agent_id or record.agent_id == query.agent_id)
            and (not query.status or record.status == query.status)
            and (
                not needle
                or needle in record.title.casefold()
                or needle in record.body.casefold()
            )
        ]
        return SearchResponse(
            query=query,
            hits=matches[: query.limit],
            total=len(matches),
        )
