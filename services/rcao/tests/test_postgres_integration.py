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
from app.auth import ActorContext, ActorType
from app.models import AgentMessage, AgentRole, MessageType
from app.payment_boundary import (
    PaymentNetwork,
    PaymentPurpose,
    ServicePaymentRequest,
    ServicePaymentRepository,
)
from app.payment_profile import (
    AgentPaymentProfile,
    AgentPaymentProfileRepository,
    PaymentApprovalMode,
    PaymentProfileNetwork,
    PaymentProfileStatus,
)
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


def _service_payment_request(
    ids: dict[str, str],
    suffix: str,
    *,
    profile_id: str | None = None,
    profile_version: int | None = None,
) -> ServicePaymentRequest:
    return ServicePaymentRequest(
        payment_id=f"{ids['task']}-payment-{suffix}",
        idempotency_key=f"{ids['task']}-payment-idem-{suffix}",
        challenge_id=f"{ids['task']}-challenge-{suffix}",
        nonce=f"{ids['task']}-nonce-{suffix}",
        task_id=ids["task"],
        run_id=f"{ids['task']}-run-{suffix}",
        trace_id=f"{ids['task']}-trace-{suffix}",
        correlation_id=f"{ids['task']}-correlation-{suffix}",
        agent_id=ids["builder"],
        service_id="service.example.compute",
        profile_id=profile_id,
        profile_version=profile_version,
        recipient="service-account-001",
        network=PaymentNetwork.LOCAL,
        token="LOCAL_TEST_TOKEN",
        amount_units=1250,
        purpose=PaymentPurpose.SERVICE_PAYMENT,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )


def test_clean_migrations_create_the_complete_core_schema(migrated_database) -> None:
    connection, history = migrated_database

    assert [item.version for item in history] == list(range(1, 18))
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
        "service_payment_boundary",
        "agent_payment_profiles",
        "mpp_policy_engine",
        "signer_boundary",
        "mpp_client_attempts",
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
        "mvp_service_payments",
        "mvp_service_payment_events",
        "mvp_agent_payment_profiles",
        "mvp_agent_payment_profile_versions",
        "mvp_mpp_policy_decisions",
        "mvp_mpp_budget_counters",
        "mvp_mpp_budget_reservations",
        "mvp_mpp_signer_authorizations",
        "mvp_signer_wallets",
        "mvp_signer_requests",
        "mvp_signer_results",
        "mvp_signer_receipts",
        "mvp_mpp_client_attempts",
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


def test_service_payment_isolated_from_virtual_reward_ledger(
    migrated_database, database_url: str
) -> None:
    connection, _ = migrated_database
    ids = _seed_core_rows(connection, f"payment-{uuid4().hex[:12]}")
    profile = AgentPaymentProfile(
        profile_id=f"{ids['task']}-payment-profile",
        agent_id=ids["builder"],
        network=PaymentProfileNetwork.LOCAL,
        service_id="service.example.compute",
        recipient="service-account-001",
        token_allowlist=("LOCAL_TEST_TOKEN",),
        service_allowlist=("service.example.compute",),
        recipient_allowlist=("service-account-001",),
        per_payment_limit_units=2_000,
        per_task_limit_units=5_000,
        daily_limit_units=10_000,
        auto_approval_limit_units=2_000,
        max_expiry_seconds=3_600,
        approval_mode=PaymentApprovalMode.AUTO_ALLOW,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    request = _service_payment_request(
        ids,
        "valid",
        profile_id=profile.profile_id,
        profile_version=profile.version,
    )
    actor = ActorContext(
        actor_id=ids["builder"],
        subject=f"subject:{ids['builder']}",
        name="Integration Builder",
        role=AgentRole.BUILDER,
        actor_type=ActorType.AGENT,
        phase=Phase.DEVNET,
        token_id=f"token:{ids['builder']}",
        issued_at=1,
        expires_at=2,
        task_ids={ids["task"]},
        identity_version=1,
    )
    repository = PostgresRepository(lambda: psycopg.connect(database_url))
    repository.run(
        lambda tx: AgentPaymentProfileRepository(tx).create(
            actor_id=ids["owner"],
            actor_type="OWNER",
            profile=profile,
            audit_id=f"audit-{uuid4().hex}",
            correlation_id=f"corr-{uuid4().hex}",
        )
    )

    first = repository.run(
        lambda tx: ServicePaymentRepository(tx).propose(request, actor=actor)
    )
    replay = repository.run(
        lambda tx: ServicePaymentRepository(tx).propose(request, actor=actor)
    )

    assert first.payment.request.purpose is PaymentPurpose.SERVICE_PAYMENT
    assert first.payment.status.value == "PROPOSED"
    assert replay.replayed is True

    with psycopg.connect(database_url) as verify:
        payment = _fetch_one(
            verify,
            """
            SELECT purpose, network, token, amount_units, policy_decision, status,
                   policy_decision_id, budget_reservation_id, owner_approval_id
            FROM mvp_service_payments WHERE id = %s
            """,
            (request.payment_id,),
        )
        policy_decision = _fetch_one(
            verify,
            """
            SELECT decision, policy_version, payment_id, task_id, run_id,
                   trace_id, correlation_id, reservation_id
            FROM mvp_mpp_policy_decisions
            WHERE payment_id = %s
            """,
            (request.payment_id,),
        )
        reservation = _fetch_one(
            verify,
            """
            SELECT payment_id, amount_units, status, profile_version
            FROM mvp_mpp_budget_reservations
            WHERE payment_id = %s
            """,
            (request.payment_id,),
        )
        payment_events = _fetch_one(
            verify,
            """
            SELECT count(*)
            FROM mvp_service_payment_events
            WHERE payment_id = %s
            """,
            (request.payment_id,),
        )
        audit = _fetch_one(
            verify,
            """
            SELECT event_type, target_type, payment_id, task_id, run_id,
                   correlation_id, policy_result
            FROM mvp_audit_logs
            WHERE payment_id = %s AND event_type = 'SERVICE_PAYMENT_PROPOSED'
            ORDER BY created_at ASC, id ASC
            """,
            (request.payment_id,),
        )
        outbox = _fetch_one(
            verify,
            """
            SELECT aggregate_type, aggregate_id, event_type, transaction_id
            FROM mvp_outbox_events
            WHERE aggregate_id = %s AND event_type = 'SERVICE_PAYMENT_PROPOSED'
            ORDER BY created_at ASC, id ASC
            """,
            (request.payment_id,),
        )
        ledger_count = _fetch_one(
            verify,
            "SELECT count(*) FROM mvp_virtual_ledger_entries WHERE task_id = %s",
            (request.task_id,),
        )
        columns = _fetch_one(
            verify,
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'mvp_service_payments'
              AND column_name IN ('ledger_entry_id', 'recipient_agent_id', 'sender_agent_id')
            """,
        )

    assert payment == (
        "SERVICE_PAYMENT",
        "LOCAL",
        "LOCAL_TEST_TOKEN",
        1250,
        "allow",
        "PROPOSED",
        first.payment.policy_decision_id,
        first.payment.budget_reservation_id,
        None,
    )
    assert policy_decision == (
        "allow",
        "mpp-policy-engine-v1",
        request.payment_id,
        request.task_id,
        request.run_id,
        request.trace_id,
        request.correlation_id,
        first.payment.budget_reservation_id,
    )
    assert reservation == (
        request.payment_id,
        1250,
        "RESERVED",
        1,
    )
    assert payment_events == (1,)
    assert audit == (
        "SERVICE_PAYMENT_PROPOSED",
        "SERVICE_PAYMENT",
        request.payment_id,
        request.task_id,
        request.run_id,
        request.correlation_id,
        "ALLOW",
    )
    assert outbox == (
        "SERVICE_PAYMENT",
        request.payment_id,
        "SERVICE_PAYMENT_PROPOSED",
        request.correlation_id,
    )
    assert ledger_count == (0,)
    assert columns == (0,)

    with psycopg.connect(database_url) as invalid:
        with invalid.cursor() as cursor:
            with pytest.raises(psycopg.Error):
                cursor.execute(
                    """
                    INSERT INTO mvp_service_payments
                      (id, idempotency_key, challenge_id, nonce, task_id, run_id,
                       trace_id, correlation_id, agent_id, service_id, recipient,
                       network, token, amount_units, purpose, expires_at,
                       challenge_hash, policy_version, policy_decision, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            'LOCAL', 'LOCAL_TEST_TOKEN', 1250, 'REWARD', %s,
                            %s, %s, 'allow', %s)
                    """,
                    (
                        f"{request.payment_id}-invalid",
                        f"{request.idempotency_key}-invalid",
                        f"{request.challenge_id}-invalid",
                        f"{request.nonce}-invalid",
                        request.task_id,
                        request.run_id,
                        request.trace_id,
                        f"{request.correlation_id}-invalid",
                        request.agent_id,
                        request.service_id,
                        "service-account-002",
                        request.expires_at,
                        request.challenge_hash(),
                        "mpp-service-payment-v1",
                        request.agent_id,
                    ),
                )
        invalid.rollback()


def test_payment_profile_is_owner_versioned_and_audited(
    migrated_database, database_url: str
) -> None:
    connection, _ = migrated_database
    ids = _seed_core_rows(connection, f"profile-{uuid4().hex[:12]}")
    profile = AgentPaymentProfile(
        profile_id=f"{ids['task']}-profile",
        agent_id=ids["builder"],
        network=PaymentProfileNetwork.LOCAL,
        service_id="service.example.compute",
        recipient="service-account-001",
        token_allowlist=("LOCAL_TEST_TOKEN",),
        service_allowlist=("service.example.compute",),
        recipient_allowlist=("service-account-001",),
        per_payment_limit_units=2_000,
        per_task_limit_units=5_000,
        daily_limit_units=10_000,
        auto_approval_limit_units=2_000,
        max_expiry_seconds=3_600,
        approval_mode=PaymentApprovalMode.AUTO_ALLOW,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    repository = PostgresRepository(lambda: psycopg.connect(database_url))

    created = repository.run(
        lambda tx: AgentPaymentProfileRepository(tx).create(
            actor_id=ids["owner"],
            actor_type="OWNER",
            profile=profile,
            audit_id=f"audit-{uuid4().hex}",
            correlation_id=f"corr-{uuid4().hex}",
        )
    )
    request = _service_payment_request(
        ids,
        "profiled",
        profile_id=created.profile_id,
        profile_version=created.version,
    )
    payment = repository.run(
        lambda tx: ServicePaymentRepository(tx).propose(
            request,
            actor=ActorContext(
                actor_id=ids["builder"],
                subject=f"subject:{ids['builder']}",
                name="Integration Builder",
                role=AgentRole.BUILDER,
                actor_type=ActorType.AGENT,
                phase=Phase.DEVNET,
                token_id=f"token:{ids['builder']}",
                issued_at=1,
                expires_at=2,
                task_ids={ids["task"]},
                identity_version=1,
            ),
        )
    )
    assert payment.payment.request.profile_id == created.profile_id
    assert payment.payment.request.profile_version == 1

    reduced = created.model_copy(
        update={
            "version": 2,
            "per_payment_limit_units": 1_000,
            "per_task_limit_units": 3_000,
            "daily_limit_units": 5_000,
            "auto_approval_limit_units": 1_000,
        }
    )
    updated = repository.run(
        lambda tx: AgentPaymentProfileRepository(tx).update(
            actor_id=ids["owner"],
            actor_type="OWNER",
            profile=reduced,
            expected_version=1,
            audit_id=f"audit-{uuid4().hex}",
            correlation_id=f"corr-{uuid4().hex}",
        )
    )
    assert updated.version == 2

    stopped = repository.run(
        lambda tx: AgentPaymentProfileRepository(tx).set_status(
            actor_id=ids["owner"],
            actor_type="OWNER",
            profile_id=updated.profile_id,
            status=PaymentProfileStatus.SUSPENDED,
            expected_version=2,
            audit_id=f"audit-{uuid4().hex}",
            correlation_id=f"corr-{uuid4().hex}",
            reason="Owner paused the profile for a safety check",
        )
    )
    assert stopped.version == 3
    with pytest.raises(Exception, match="Owner"):
        repository.run(
            lambda tx: AgentPaymentProfileRepository(tx).set_status(
                actor_id=ids["builder"],
                actor_type="AGENT",
                profile_id=stopped.profile_id,
                status=PaymentProfileStatus.ACTIVE,
                expected_version=3,
                audit_id=f"audit-{uuid4().hex}",
                correlation_id=f"corr-{uuid4().hex}",
                reason="Agent must not resume a payment profile",
            )
        )

    with psycopg.connect(database_url) as verify:
        versions = _fetch_one(
            verify,
            "SELECT count(*) FROM mvp_agent_payment_profile_versions WHERE profile_id = %s",
            (profile.profile_id,),
        )
        audit = _fetch_one(
            verify,
            """
            SELECT count(*)
            FROM mvp_audit_logs
            WHERE target_type = 'PAYMENT_PROFILE' AND target_id = %s
            """,
            (profile.profile_id,),
        )
        payment_snapshot = _fetch_one(
            verify,
            "SELECT profile_id, profile_version FROM mvp_service_payments WHERE id = %s",
            (request.payment_id,),
        )
    assert versions == (3,)
    assert audit == (3,)
    assert payment_snapshot == (profile.profile_id, 1)


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
