from fastapi import FastAPI, Query
from pydantic import BaseModel

from .agent_runtime import runtime_integration_notes
from .search import InMemoryOperationSearch, SearchQuery, SearchResponse, SearchScope


class HealthResponse(BaseModel):
    service: str
    phase: int
    ledger: str
    status: str


app = FastAPI(
    title="R-CAO Control Plane",
    version="0.2.0",
    description="Owner-directed control plane and Agent runtime boundary.",
)

operation_search = InMemoryOperationSearch()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        service="rcao-control-plane",
        phase=1,
        ledger="virtual",
        status="ok",
    )


@app.get("/api/v1/capabilities")
def capabilities() -> dict[str, dict[str, str]]:
    return {"runtime": runtime_integration_notes()}


@app.get("/api/v1/operations/search", response_model=SearchResponse)
def search_operations(
    q: str = Query(default=""),
    scope: SearchScope = Query(default=SearchScope.ALL),
    task_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> SearchResponse:
    """Read-only operations search; PostgreSQL is the next repository adapter."""

    return operation_search.search(
        SearchQuery(
            q=q,
            scope=scope,
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )
    )
