"""Core integration and security gates against a disposable PostgreSQL instance.

The regular test suite deliberately uses fakes so it remains fast and
offline.  These tests exercise the real migration history and the most
important cross-module contracts against PostgreSQL/pgvector in CI.  They are
opt-in because a developer should never point a test at an arbitrary database
by accident.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from app.a2a import postgres_message_gateway
from app.migrations import DEFAULT_MIGRATION_DIR, migrate
from app.models import AgentMessage, AgentRole, MessageType
from app.policy import Phase
from app.repository import (
    PostgresRepository,
    TaskTransitionCommand,
)


pytestmark = pytest.mark.postgres


def _enabled() -> bool:
    return os.getenv("RCAO_RUN_POSTGRES_INTEGRATION") == "1"


@pytest.fixture(scope="module")
def database_url() -> str:
    if not _enabled():
        pytest.skip(
            "set RCAO_RUN_POSTGRES_INTEGRATION=1 to run the disposable PostgreSQL gate"
        )
    value = os.getenv("DATABASE_URL")
    if not value:
        pytest.fail("DATABASE_URL is required for the PostgreSQL integration gate")
    return value


@pytest.fixture(scope="module")
def migrated_database(database_url: str):
    connection = psycopg.connect(database_url)
    try:
        history = migrate(connection, DEFAULT_MIGRATION_DIR)
        yield connection, history
    finally:
        connection.close()


def _fetch_one(connection, statement: str, params: tuple[object, ...] = ()):
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchone()


def _seed_core_rows(connection, prefix: str) -> dict[str, str]:
    owner_id = f"{prefix}-owner"
    executive_id = f"{prefix}-executive"
    builder_id = f"{prefix}-builder"
    reviewer_id = f"{prefix}-reviewer"
    auditor_id = f"{prefix}-auditor"
    task_id = f"{prefix}-task"

    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, display_name) VALUES (%s, %s)",
            (owner_id, "Integration Owner"),
        )
        cursor.execute(
            "INSERT INTO owners (id, name) VALUES (%s, %s)",
            (owner_id, "Integration Owner"),
        )
        for agent_id, name, role, agent_type, layer in (
            (executive_id, "Integration Executive", "ENGINEERING", "EXECUTIVE", "VALUE_CREATION"),
            (builder_id, "Integration Builder", "ENGINEERING", "SUB_AGENT", "VALUE_CREATION"),
            (reviewer_id, "Integration Reviewer", "REVIEWER", "SUB_AGENT", "VALUE_PROTECTION"),
            (auditor_id, "Integration Auditor", "AUDITOR", "AUDIT", "VALUE_PROTECTION"),
        ):
            cursor.execute(
                """
                INSERT INTO mvp_agents
                  (id, name, role, mission, responsibilities, authority,
                   prohibited_actions, reports_to, agent_type, status,
                   model, capability_hash, identity_id, organization_layer,
                   provider, prompt_version, allowed_tools, network_scope,
                   budget_scope, risk_scope, budget_limit_lamports)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s, %s::mvp_agent_type, 'ACTIVE'::mvp_agent_status,
                        'policy-bound', %s, %s, %s, 'TEST', 'v1',
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, 1000)
                """,
                (
                    agent_id,
                    f"{prefix} {name}",
                    role,
                    "Exercise the integration boundary",
                    json.dumps(["complete assigned work"]),
                    json.dumps(["propose work"]),
                    json.dumps(["change authority", "send assets"]),
                    owner_id,
                    agent_type,
                    f"capability-{agent_id}",
                    agent_id,
                    layer,
                    json.dumps(["repo.read"]),
                    json.dumps(["OFFCHAIN"]),
                    json.dumps({"max_lamports": 1000}),
                    json.dumps({"max": "LOW"}),
                ),
            )
        cursor.execute(
            """
            INSERT INTO mvp_tasks
              (id, title, objective, priority, deadline, acceptance_criteria,
               reward_budget_lamports, assigned_executive_agent_id, risk_level,
               created_by)
            VALUES (%s, %s, %s, 'HIGH', %s, %s::jsonb, 100, %s, 'LOW', %s)
            """,
            (
                task_id,
                "Integration Task",
                "Exercise the durable control plane",
                datetime.now(timezone.utc) + timedelta(hours=1),
                json.dumps(["transition", "audit", "replay"]),
                executive_id,
                owner_id,
            ),
        )
        cursor.execute(
            """
            INSERT INTO mvp_agent_memberships
              (task_id, agent_id, membership_role, assigned_by)
            VALUES (%s, %s, 'BUILDER', %s), (%s, %s, 'REVIEWER', %s)
            """,
            (task_id, builder_id, owner_id, task_id, reviewer_id, owner_id),
        )
    connection.commit()
    return {
        "owner": owner_id,
        "executive": executive_id,
        "builder": builder_id,
        "reviewer": reviewer_id,
        "auditor": auditor_id,
        "task": task_id,
    }


def _agent_message(ids: dict[str, str], suffix: str) -> AgentMessage:
    return AgentMessage(
        message_id=f"{ids['builder']}-message-{suffix}",
        idempotency_key=f"{ids['builder']}-message-idem-{suffix}",
        nonce=f"{ids['builder']}-nonce-{suffix}",
        trace_id=f"{ids['task']}-trace-{suffix}",
        task_id=ids["task"],
        sender_agent_id=ids["builder"],
        recipient_agent_id=ids["reviewer"],
        message_type=MessageType.REQUEST,
        payload={"action": "PROPOSE_PLAN", "content": "Integration proposal"},
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    )


def test_clean_migrations_create_the_complete_core_schema(migrated_database) -> None:
    connection, history = migrated_database

    assert [item.version for item in history] == list(range(1, 13))
    assert [item.name for item in history] == [
        "phase1_foundation",
        "owner_directed_mvp",
        "transaction_boundaries",
        "idempotency_request_fingerprint",
        "audit_outbox_replay",
        "agent_registry_capabilities",
        "task_workflow_acceptance_history",
        "virtual_ledger_treasury",
        "a2a_message_gateway",
        "agent_runs",
        "evidence_memory",
        "observability_stop_incidents",
    ]

    required_tables = {
        "schema_migrations",
        "mvp_tasks",
        "mvp_audit_logs",
        "mvp_outbox_events",
        "mvp_agent_memberships",
        "mvp_agent_delegations",
        "mvp_treasury_accounts",
        "mvp_virtual_ledger_entries",
        "mvp_agent_messages",
        "mvp_agent_runs",
        "mvp_evidence",
        "mvp_memory_items",
        "mvp_stop_controls",
        "mvp_observability_events",
        "mvp_incidents",
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
        tables = {row[0] for row in cursor.fetchall()}
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        vector_extension = cursor.fetchone()

    assert required_tables <= tables
    assert vector_extension is not None

    # A second run must be a no-op and must keep every checksum unchanged.
    rerun = migrate(connection, DEFAULT_MIGRATION_DIR)
    assert rerun == history


def test_persistent_task_audit_outbox_and_replay_share_a_transaction(
    migrated_database, database_url: str
) -> None:
    connection, _ = migrated_database
    ids = _seed_core_rows(connection, f"it-{uuid4().hex[:12]}")
    repository = PostgresRepository(lambda: psycopg.connect(database_url))
    command = TaskTransitionCommand(
        task_id=ids["task"],
        expected_version=1,
        status="APPROVED",
        progress=10,
        actor_id=ids["owner"],
        actor_type="OWNER",
        reason="Integration Owner approved the Task",
        correlation_id=f"corr-{uuid4().hex}",
        idempotency_key=f"idem-{uuid4().hex}",
        audit_id=f"audit-{uuid4().hex}",
        outbox_event_id=f"outbox-{uuid4().hex}",
    )

    first = repository.transition_task(command)
    replay = repository.transition_task(command)
    assert first.task.status == "APPROVED"
    assert first.task.version == replay.task.version == 2
    assert replay.replayed is True

    with psycopg.connect(database_url) as verify:
        task = _fetch_one(
            verify,
            "SELECT status, version FROM mvp_tasks WHERE id = %s",
            (ids["task"],),
        )
        audit = _fetch_one(
            verify,
            """
            SELECT correlation_id, event_hash, before_state, after_state
            FROM mvp_audit_logs
            WHERE id = %s
            """,
            (command.audit_id,),
        )
        outbox = _fetch_one(
            verify,
            """
            SELECT transaction_id, delivery_status, payload
            FROM mvp_outbox_events
            WHERE id = %s
            """,
            (command.outbox_event_id,),
        )

    assert task == ("APPROVED", 2)
    assert audit is not None
    assert audit[0] == command.correlation_id
    assert len(audit[1]) == 64
    assert outbox is not None
    assert outbox[0] == command.correlation_id
    assert outbox[1] == "PENDING"
    assert outbox[2]["task_id"] == ids["task"]

    result = repository.run(lambda tx: tx.replay_task(ids["task"]))
    assert result.events_processed == 1
    assert result.states[f"TASK:{ids['task']}"]["status"] == "APPROVED"


def test_registry_membership_and_a2a_are_task_bound_in_postgres(
    migrated_database, database_url: str
) -> None:
    connection, _ = migrated_database
    ids = _seed_core_rows(connection, f"a2a-{uuid4().hex[:12]}")
    gateway = postgres_message_gateway(database_url)
    message = _agent_message(ids, "valid")

    first = gateway.send(message)
    second = gateway.send(message)
    assert first.replayed is False
    assert second.replayed is True

    with psycopg.connect(database_url) as verify:
        count = _fetch_one(
            verify,
            "SELECT count(*) FROM mvp_agent_messages WHERE id = %s",
            (message.message_id,),
        )
        audit_count = _fetch_one(
            verify,
            "SELECT count(*) FROM mvp_audit_logs WHERE message_id = %s",
            (message.message_id,),
        )
    assert count == (1,)
    assert audit_count == (1,)

    outsider = _agent_message(ids, "outsider")
    outsider = outsider.model_copy(update={"sender_agent_id": ids["auditor"]})
    with pytest.raises(Exception, match="Task|membership|member"):
        gateway.send(outsider)


def test_database_constraints_keep_virtual_ledger_separate_from_external_assets(
    migrated_database,
) -> None:
    connection, _ = migrated_database
    ids = _seed_core_rows(connection, f"ledger-{uuid4().hex[:12]}")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO mvp_treasury_accounts
              (id, asset_type, currency)
            VALUES (%s, 'VIRTUAL_REWARD', 'VIRTUAL')
            """,
            (f"{ids['task']}-treasury",),
        )
        connection.commit()
        with pytest.raises(psycopg.Error):
            cursor.execute(
                """
                INSERT INTO mvp_treasury_accounts
                  (id, asset_type, currency)
                VALUES (%s, 'SOL', 'SOL')
                """,
                (f"{ids['task']}-real-asset",),
            )
        connection.rollback()

    # There is no recipient-agent column or Agent-to-Agent transfer table in
    # the virtual ledger schema; only an optional single beneficiary Agent is
    # linked to an Owner-approved Reward allocation.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'mvp_virtual_ledger_entries'
            """
        )
        columns = {row[0] for row in cursor.fetchall()}
    assert "recipient_agent_id" not in columns
    assert "sender_agent_id" not in columns


def test_core_security_boundaries_are_executable() -> None:
    from app.agent_runtime import (
        AgentRunRequest,
        DeterministicTestRuntime,
        PolicyBoundAgentRuntime,
        ProviderRegistry,
        ModelProvider,
    )
    from app.messages import validate_agent_message
    from app.models import AgentMessage
    from app.policy import PolicyAction, PolicyDecision, evaluate_policy

    assert evaluate_policy(AgentRole.BUILDER, PolicyAction.DIRECT_AGENT_TRANSFER) is PolicyDecision.DENY
    assert evaluate_policy(AgentRole.OWNER, PolicyAction.DIRECT_AGENT_TRANSFER) is PolicyDecision.DENY

    blocked = AgentMessage(
        message_id="security-message",
        idempotency_key="security-idem",
        nonce="security-nonce",
        trace_id="security-trace",
        task_id="security-task",
        sender_agent_id="agent-a",
        recipient_agent_id="agent-b",
        message_type=MessageType.REQUEST,
        payload={"action": "PROPOSE_PLAN", "private_key": "must-not-pass"},
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    )
    with pytest.raises(Exception, match="secret|private|forbidden"):
        validate_agent_message(blocked, AgentRole.BUILDER)

    request = AgentRunRequest(
        run_id="security-run",
        task_id="security-task",
        agent_id="agent-a",
        provider=ModelProvider.TEST,
        model="deterministic-v1",
        input="prepare a proposal",
        trace_id="security-trace",
        prompt_version="v1",
    )
    result = __import__("asyncio").run(
        PolicyBoundAgentRuntime(
            ProviderRegistry({ModelProvider.TEST: DeterministicTestRuntime()})
        ).run(request)
    )
    assert result.proposal_only is True
