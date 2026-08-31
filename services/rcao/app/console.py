"""Owner Console read models backed by the PostgreSQL control-plane schema.

The console is deliberately a read-model client.  This module keeps the
presentation contract in :mod:`mvp` while making the source of truth explicit:
when the PostgreSQL task backend is enabled, every console read is performed
inside a short-lived repository transaction against the persisted MVP tables.
No browser-facing code is allowed to query PostgreSQL directly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .agent_registry import AGENT_RECORD_COLUMNS
from .mvp import (
    AgentRecord,
    ApprovalRequest,
    AuditLogRecord,
    BoardProposal,
    ExternalActionRequest,
    MvpError,
    RewardRecord,
    SubTaskRecord,
    TaskDetail,
    TaskRecord,
)
from .repository import PostgresRepository, RepositoryTransaction
from .search import OperationRecord, SearchQuery, SearchResponse, SearchScope
from .task_workflow import (
    TASK_RECORD_COLUMNS,
    PersistedTask,
    _as_utc,
    _audit_from_row,
    _evaluation_from_row,
    _review_from_row,
    _subtask_from_row,
)


AGENT_CONSOLE_COLUMNS = AGENT_RECORD_COLUMNS + ("created_at", "updated_at")
REWARD_COLUMNS = (
    "id",
    "task_id",
    "agent_id",
    "reward_budget_lamports",
    "proposed_reward_lamports",
    "approved_reward_lamports",
    "paid_reward_lamports",
    "reserved_reward_lamports",
    "cancelled_reward_lamports",
    "status",
    "approved_by",
    "approved_at",
    "comment",
)
PROPOSAL_COLUMNS = (
    "id",
    "title",
    "proposer",
    "background",
    "objective",
    "required_budget_lamports",
    "expected_return",
    "expected_period",
    "risks",
    "alternatives",
    "recommended_option",
    "exit_criteria",
    "strategy_review",
    "treasury_review",
    "audit_review",
    "owner_decision",
    "status",
    "created_at",
    "updated_at",
)
EXTERNAL_ACTION_COLUMNS = (
    "id",
    "task_id",
    "requested_by",
    "recipient",
    "channel",
    "purpose",
    "content",
    "allowed_action_count",
    "expires_at",
    "owner_decision",
    "status",
    "execution_count",
    "execution_result",
    "created_at",
)
AUDIT_LOG_COLUMNS = (
    "id",
    "actor",
    "actor_type",
    "action",
    "target_type",
    "target_id",
    "before_state",
    "after_state",
    "policy_result",
    "reason",
    "created_at",
    "correlation_id",
)


def _values(row: Any, columns: tuple[str, ...]) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip(columns, row, strict=True))


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _agent_from_row(row: Any) -> AgentRecord:
    values = _values(row, AGENT_CONSOLE_COLUMNS)
    return AgentRecord(
        id=_as_text(values["id"]),
        name=_as_text(values["name"]),
        role=values["role"],
        mission=_as_text(values["mission"]),
        responsibilities=_json_value(values.get("responsibilities"), []),
        authority=_json_value(values.get("authority"), []),
        prohibited_actions=_json_value(values.get("prohibited_actions"), []),
        reports_to=_as_text(values["reports_to"]),
        agent_type=values["agent_type"],
        status=values["status"],
        version=int(values["version"]),
        model=_as_text(values["model"]),
        capability_hash=_as_text(values["capability_hash"]),
        budget_limit_lamports=int(values.get("budget_limit_lamports") or 0),
        created_at=_as_utc(values["created_at"]),
        updated_at=_as_utc(values["updated_at"]),
    )


def _reward_from_row(row: Any) -> RewardRecord:
    values = _values(row, REWARD_COLUMNS)
    approved_at = values.get("approved_at")
    return RewardRecord(
        id=_as_text(values["id"]),
        task_id=_as_text(values["task_id"]),
        agent_id=_as_text(values["agent_id"]),
        reward_budget_lamports=int(values["reward_budget_lamports"]),
        proposed_reward_lamports=int(values.get("proposed_reward_lamports") or 0),
        approved_reward_lamports=(
            int(values["approved_reward_lamports"])
            if values.get("approved_reward_lamports") is not None
            else None
        ),
        paid_reward_lamports=int(values.get("paid_reward_lamports") or 0),
        reserved_reward_lamports=int(values.get("reserved_reward_lamports") or 0),
        cancelled_reward_lamports=int(values.get("cancelled_reward_lamports") or 0),
        status=values["status"],
        approved_by=(
            _as_text(values["approved_by"])
            if values.get("approved_by") is not None
            else None
        ),
        approved_at=_as_utc(approved_at) if approved_at is not None else None,
        comment=_as_text(values.get("comment")),
    )


def _approval_from_row(row: Any) -> ApprovalRequest:
    values = _values(
        row,
        (
            "id",
            "approval_type",
            "target_id",
            "requested_by",
            "owner_decision",
            "comment",
            "created_at",
            "decided_at",
        ),
    )
    decided_at = values.get("decided_at")
    return ApprovalRequest(
        id=_as_text(values["id"]),
        approval_type=values["approval_type"],
        target_id=_as_text(values["target_id"]),
        requested_by=_as_text(values["requested_by"]),
        owner_decision=values.get("owner_decision"),
        comment=_as_text(values.get("comment")),
        created_at=_as_utc(values["created_at"]),
        decided_at=_as_utc(decided_at) if decided_at is not None else None,
    )


def _proposal_from_row(row: Any) -> BoardProposal:
    values = _values(row, PROPOSAL_COLUMNS)
    return BoardProposal(
        id=_as_text(values["id"]),
        title=_as_text(values["title"]),
        proposer=_as_text(values["proposer"]),
        background=_as_text(values["background"]),
        objective=_as_text(values["objective"]),
        required_budget_lamports=int(values["required_budget_lamports"]),
        expected_return=_as_text(values["expected_return"]),
        expected_period=_as_text(values["expected_period"]),
        risks=_json_value(values.get("risks"), []),
        alternatives=_json_value(values.get("alternatives"), []),
        recommended_option=_as_text(values["recommended_option"]),
        exit_criteria=_json_value(values.get("exit_criteria"), []),
        strategy_review=values.get("strategy_review"),
        treasury_review=values.get("treasury_review"),
        audit_review=values.get("audit_review"),
        owner_decision=values.get("owner_decision"),
        status=_as_text(values.get("status")),
        created_at=_as_utc(values["created_at"]),
        updated_at=_as_utc(values["updated_at"]),
    )


def _external_action_from_row(row: Any) -> ExternalActionRequest:
    values = _values(row, EXTERNAL_ACTION_COLUMNS)
    return ExternalActionRequest(
        id=_as_text(values["id"]),
        task_id=(
            _as_text(values["task_id"]) if values.get("task_id") is not None else None
        ),
        requested_by=_as_text(values["requested_by"]),
        recipient=_as_text(values["recipient"]),
        channel=values["channel"],
        purpose=_as_text(values["purpose"]),
        content=_as_text(values["content"]),
        allowed_action_count=int(values["allowed_action_count"]),
        expires_at=_as_utc(values["expires_at"]),
        owner_decision=values.get("owner_decision"),
        status=values["status"],
        execution_count=int(values.get("execution_count") or 0),
        execution_result=values.get("execution_result"),
        created_at=_as_utc(values["created_at"]),
    )


def _audit_log_from_row(row: Any) -> AuditLogRecord:
    values = _values(row, AUDIT_LOG_COLUMNS)
    return AuditLogRecord(
        id=_as_text(values["id"]),
        actor=_as_text(values["actor"]),
        actor_type=_as_text(values["actor_type"]),
        action=_as_text(values["action"]),
        target_type=_as_text(values["target_type"]),
        target_id=_as_text(values["target_id"]),
        before=_json_value(values.get("before_state"), {}),
        after=_json_value(values.get("after_state"), {}),
        policy_result=values["policy_result"],
        reason=_as_text(values["reason"]),
        timestamp=_as_utc(values["created_at"]),
        correlation_id=_as_text(values["correlation_id"]),
    )


def _operation_from_row(row: Any) -> OperationRecord:
    values = _values(
        row,
        (
            "record_id",
            "scope",
            "title",
            "body",
            "task_id",
            "run_id",
            "agent_id",
            "status",
            "created_at",
            "refs",
        ),
    )
    return OperationRecord(
        record_id=_as_text(values["record_id"]),
        scope=values["scope"],
        title=_as_text(values["title"]),
        body=_as_text(values.get("body")),
        task_id=_as_text(values["task_id"]) if values.get("task_id") is not None else None,
        run_id=_as_text(values["run_id"]) if values.get("run_id") is not None else None,
        agent_id=_as_text(values["agent_id"]) if values.get("agent_id") is not None else None,
        status=_as_text(values["status"]) if values.get("status") is not None else None,
        created_at=_as_text(values["created_at"]),
        refs=_json_value(values.get("refs"), []),
    )


class PersistentConsoleReadModel:
    """PostgreSQL-backed read model for Owner Console screens."""

    def __init__(self, repository: PostgresRepository, *, annual_budget_lamports: int) -> None:
        self.repository = repository
        self.annual_budget_lamports = annual_budget_lamports

    def _run(self, callback):
        return self.repository.run(callback)

    @staticmethod
    def _agent_rows(transaction: RepositoryTransaction, include_sub_agents: bool) -> list[Any]:
        where = ""
        params: tuple[Any, ...] = ()
        if not include_sub_agents:
            where = "WHERE agent_type IN ('EXECUTIVE'::mvp_agent_type, 'AUDIT'::mvp_agent_type)"
        return transaction.fetch_all(
            f"SELECT {', '.join(AGENT_CONSOLE_COLUMNS)} FROM mvp_agents {where} ORDER BY name ASC",
            params,
        )

    def list_agents(self, *, include_sub_agents: bool = True) -> list[AgentRecord]:
        return self._run(
            lambda tx: [_agent_from_row(row) for row in self._agent_rows(tx, include_sub_agents)]
        )

    def get_agent(self, agent_id: str) -> AgentRecord:
        rows = self._run(
            lambda tx: tx.fetch_all(
                f"SELECT {', '.join(AGENT_CONSOLE_COLUMNS)} FROM mvp_agents WHERE id = %s",
                (agent_id,),
            )
        )
        if not rows:
            raise MvpError(f"Agent is not registered: {agent_id}")
        return _agent_from_row(rows[0])

    def list_tasks(self, status: str | None = None) -> list[TaskRecord]:
        def read(transaction: RepositoryTransaction) -> list[TaskRecord]:
            if status is None:
                rows = transaction.fetch_all(
                    f"SELECT {', '.join(TASK_RECORD_COLUMNS)} FROM mvp_tasks ORDER BY created_at DESC, id ASC"
                )
            else:
                rows = transaction.fetch_all(
                    f"SELECT {', '.join(TASK_RECORD_COLUMNS)} FROM mvp_tasks WHERE status = %s::mvp_task_status ORDER BY created_at DESC, id ASC",
                    (status,),
                )
            return [PersistedTask.from_record(row).to_model() for row in rows]

        return self._run(read)

    def get_task_detail(self, task_id: str) -> TaskDetail:
        def read(transaction: RepositoryTransaction) -> TaskDetail:
            task_row = transaction.fetch_one(
                f"SELECT {', '.join(TASK_RECORD_COLUMNS)} FROM mvp_tasks WHERE id = %s",
                (task_id,),
            )
            if task_row is None:
                raise MvpError(f"Task is not registered: {task_id}")
            task = PersistedTask.from_record(task_row).to_model()
            subtasks = transaction.fetch_all(
                """
                SELECT id, parent_task_id, title, description, assigned_agent_id,
                       status, progress, dependencies, artifact, review_result,
                       audit_result, created_at, updated_at
                FROM mvp_sub_tasks
                WHERE parent_task_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (task_id,),
            )
            reviews = transaction.fetch_all(
                """
                SELECT task_id, reviewer, quality, completeness, correctness,
                       required_changes, comment, reviewed_at
                FROM mvp_reviews WHERE task_id = %s ORDER BY reviewed_at ASC
                """,
                (task_id,),
            )
            audits = transaction.fetch_all(
                """
                SELECT task_id, auditor, policy_compliance, security_risk,
                       external_action_check, reward_manipulation_check,
                       authority_violation_check, result, comment, audited_at
                FROM mvp_audits WHERE task_id = %s ORDER BY audited_at ASC
                """,
                (task_id,),
            )
            evaluation_row = transaction.fetch_one(
                """
                SELECT task_id, quality, difficulty, contribution, timeliness,
                       rework, strategic_value, owner_comment, evaluated_by,
                       evaluated_at
                FROM owner_evaluations WHERE task_id = %s
                ORDER BY evaluated_at DESC LIMIT 1
                """,
                (task_id,),
            )
            rewards = transaction.fetch_all(
                f"SELECT {', '.join(REWARD_COLUMNS)} FROM reward_allocations WHERE task_id = %s ORDER BY id ASC",
                (task_id,),
            )
            activity = transaction.fetch_all(
                f"""
                SELECT {', '.join(AUDIT_LOG_COLUMNS)}
                FROM mvp_audit_logs
                WHERE task_id = %s OR target_id = %s
                ORDER BY created_at ASC, id ASC
                """,
                (task_id, task_id),
            )
            return TaskDetail(
                task=task,
                subtasks=[_subtask_from_row(row) for row in subtasks],
                reviews=[_review_from_row(row) for row in reviews],
                audits=[_audit_from_row(row) for row in audits],
                owner_evaluation=(
                    _evaluation_from_row(evaluation_row)
                    if evaluation_row is not None
                    else None
                ),
                rewards=[_reward_from_row(row) for row in rewards],
                activity=[_audit_log_from_row(row) for row in activity],
            )

        return self._run(read)

    def list_rewards(self) -> list[RewardRecord]:
        return self._run(
            lambda tx: [
                _reward_from_row(row)
                for row in tx.fetch_all(
                    f"SELECT {', '.join(REWARD_COLUMNS)} FROM reward_allocations ORDER BY id ASC"
                )
            ]
        )

    def list_approvals(self) -> list[ApprovalRequest]:
        return self._run(
            lambda tx: [
                _approval_from_row(row)
                for row in tx.fetch_all(
                    """
                    SELECT id, approval_type, target_id, requested_by,
                           owner_decision, comment, created_at, decided_at
                    FROM approval_requests
                    WHERE owner_decision IS NULL
                    ORDER BY created_at ASC, id ASC
                    """
                )
            ]
        )

    def list_proposals(self) -> list[BoardProposal]:
        return self._run(
            lambda tx: [
                _proposal_from_row(row)
                for row in tx.fetch_all(
                    f"SELECT {', '.join(PROPOSAL_COLUMNS)} FROM board_proposals ORDER BY created_at DESC, id ASC"
                )
            ]
        )

    def list_external_actions(self) -> list[ExternalActionRequest]:
        return self._run(
            lambda tx: [
                _external_action_from_row(row)
                for row in tx.fetch_all(
                    f"SELECT {', '.join(EXTERNAL_ACTION_COLUMNS)} FROM external_action_requests ORDER BY created_at DESC, id ASC"
                )
            ]
        )

    def list_audit_logs(self, *, limit: int = 200) -> list[AuditLogRecord]:
        return self._run(
            lambda tx: [
                _audit_log_from_row(row)
                for row in tx.fetch_all(
                    f"SELECT {', '.join(AUDIT_LOG_COLUMNS)} FROM mvp_audit_logs ORDER BY created_at DESC, id DESC LIMIT %s",
                    (limit,),
                )
            ]
        )

    def dashboard(self) -> dict[str, Any]:
        def read(transaction: RepositoryTransaction) -> dict[str, Any]:
            active_tasks = int(
                transaction.fetch_one(
                    """
                    SELECT count(*) FROM mvp_tasks
                    WHERE status NOT IN (
                      'COMPLETED'::mvp_task_status,
                      'REJECTED'::mvp_task_status,
                      'CANCELLED'::mvp_task_status
                    )
                    """
                )[0]
            )
            reserved = int(
                transaction.fetch_one(
                    """
                    SELECT coalesce(sum(reward_budget_lamports), 0)
                    FROM mvp_tasks
                    WHERE status NOT IN (
                      'COMPLETED'::mvp_task_status,
                      'REJECTED'::mvp_task_status,
                      'CANCELLED'::mvp_task_status
                    )
                    """
                )[0]
            )
            approvals = int(
                transaction.fetch_one(
                    "SELECT count(*) FROM approval_requests WHERE owner_decision IS NULL"
                )[0]
            )
            proposals = int(
                transaction.fetch_one(
                    "SELECT count(*) FROM board_proposals WHERE owner_decision IS NULL"
                )[0]
            )
            pending_rewards = int(
                transaction.fetch_one(
                    """
                    SELECT count(*) FROM reward_allocations
                    WHERE status IN (
                      'Pending'::mvp_reward_status,
                      'Proposed'::mvp_reward_status,
                      'Reserved'::mvp_reward_status
                    )
                    """
                )[0]
            )
            pending_external = int(
                transaction.fetch_one(
                    "SELECT count(*) FROM external_action_requests WHERE owner_decision IS NULL"
                )[0]
            )
            audit_alerts = int(
                transaction.fetch_one(
                    "SELECT count(*) FROM mvp_audit_logs WHERE policy_result = 'DENY'::mvp_policy_result"
                )[0]
            )
            agents = transaction.fetch_all(
                """
                SELECT id, name, role, status
                FROM mvp_agents
                WHERE agent_type IN ('EXECUTIVE'::mvp_agent_type, 'AUDIT'::mvp_agent_type)
                ORDER BY name ASC
                """
            )
            return {
                "fy_plan": {
                    "name": "FY2026 Owner-directed compounding",
                    "phase": "PHASE_1_OFFCHAIN",
                    "status": "ACTIVE",
                },
                "active_tasks": active_tasks,
                "owner_approval_pending": approvals,
                "board_proposals": proposals,
                "reward_approval_pending": pending_rewards,
                "external_action_approval_pending": pending_external,
                "budget_status": {
                    "annual_budget_lamports": self.annual_budget_lamports,
                    "reserved_reward_budget_lamports": reserved,
                    "available_lamports": max(self.annual_budget_lamports - reserved, 0),
                    "mode": "VIRTUAL_LEDGER",
                },
                "audit_alerts": audit_alerts,
                "executive_agent_status": [
                    {
                        "id": _as_text(row[0]),
                        "name": _as_text(row[1]),
                        "role": _as_text(row[2]),
                        "status": _as_text(row[3]),
                    }
                    for row in agents
                ],
            }

        return self._run(read)

    def search_operations(self, query: SearchQuery) -> SearchResponse:
        def read(transaction: RepositoryTransaction) -> SearchResponse:
            rows = transaction.fetch_all(
                """
                SELECT id::text, 'TASKS'::text, title, objective, id, NULL::text,
                       assigned_executive_agent_id, status::text, created_at::text,
                       '[]'::jsonb
                FROM mvp_tasks
                UNION ALL
                SELECT id::text, 'RUNS'::text, provider || ':' || model,
                       coalesce(error_message, output), task_id, id::text,
                       agent_id, status, created_at::text, '[]'::jsonb
                FROM mvp_agent_runs
                UNION ALL
                SELECT id::text, 'MESSAGES'::text, message_type,
                       payload::text, task_id, run_id, sender_agent_id, status,
                       created_at::text, evidence_refs
                FROM mvp_agent_messages
                UNION ALL
                SELECT id::text, 'EVIDENCE'::text, title, content, task_id,
                       run_id, created_by, status, created_at::text, '[]'::jsonb
                FROM mvp_evidence
                UNION ALL
                SELECT id::text, 'MEMORY'::text, title, content, task_id,
                       run_id, created_by, status, created_at::text, '[]'::jsonb
                FROM mvp_memory_items
                UNION ALL
                SELECT id::text, 'AUDIT'::text, action, reason, task_id,
                       run_id, actor, policy_result::text, created_at::text,
                       jsonb_build_array(correlation_id)
                FROM mvp_audit_logs
                ORDER BY 9 DESC, 1 ASC
                """
            )
            records = [_operation_from_row(row) for row in rows]
            needle = query.q.casefold()
            matches = [
                record
                for record in records
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

        return self._run(read)
