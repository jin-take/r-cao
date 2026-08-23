from app.search import (
    InMemoryOperationSearch,
    OperationRecord,
    SearchQuery,
    SearchScope,
)


def test_operations_search_filters_by_task_and_text() -> None:
    search = InMemoryOperationSearch(
        [
            OperationRecord(
                record_id="msg-1",
                scope=SearchScope.MESSAGES,
                title="Review request",
                body="Independent review for the foundation task",
                task_id="task-1",
                created_at="2026-08-23T00:00:00Z",
            ),
            OperationRecord(
                record_id="run-2",
                scope=SearchScope.RUNS,
                title="Unrelated run",
                body="another task",
                task_id="task-2",
                created_at="2026-08-23T00:01:00Z",
            ),
        ]
    )

    result = search.search(
        SearchQuery(q="independent", task_id="task-1", scope=SearchScope.ALL)
    )

    assert result.total == 1
    assert result.hits[0].record_id == "msg-1"
