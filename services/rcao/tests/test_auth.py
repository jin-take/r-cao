from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.auth import (
    ActorAuthorizationError,
    ActorIdentity,
    ActorType,
    Authenticator,
    AuthenticationError,
    AuthOutcome,
    build_runtime_authenticator,
    IdentityError,
    IdentityRegistry,
    InMemoryAuthAuditLog,
    authorize_actor_action,
    configure_runtime_authenticator,
)
from app.main import app
from app.models import AgentRole
from app.policy import Phase, PolicyAction, PolicyDecision


def make_authenticator() -> tuple[Authenticator, InMemoryAuthAuditLog, list[int]]:
    now = [1_700_000_000]
    audit = InMemoryAuthAuditLog()
    registry = IdentityRegistry(
        [
            ActorIdentity(
                actor_id="owner-1",
                subject="owner-subject",
                name="Owner",
                role=AgentRole.OWNER,
                actor_type=ActorType.OWNER,
                phase=Phase.PHASE_1_OFFCHAIN,
            ),
            ActorIdentity(
                actor_id="builder-1",
                subject="builder-subject",
                name="Builder",
                role=AgentRole.BUILDER,
                actor_type=ActorType.AGENT,
                phase=Phase.PHASE_1_OFFCHAIN,
                task_ids={"task-1"},
                capabilities={"PROPOSE"},
            ),
        ]
    )
    authenticator = Authenticator(
        secret=b"s" * 32,
        phase=Phase.PHASE_1_OFFCHAIN,
        registry=registry,
        audit_log=audit,
        clock=lambda: now[0],
    )
    return authenticator, audit, now


def test_identity_registry_rejects_duplicates_and_invalid_owner_roles() -> None:
    registry = IdentityRegistry()
    registry.register(
        ActorIdentity(
            actor_id="owner-1",
            subject="owner-subject",
            name="Owner",
            role=AgentRole.OWNER,
            actor_type=ActorType.OWNER,
            phase=Phase.PHASE_1_OFFCHAIN,
        )
    )

    with pytest.raises(IdentityError):
        registry.register(
            ActorIdentity(
                actor_id="other-id",
                subject="other-subject",
                name="owner",
                role=AgentRole.BUILDER,
                actor_type=ActorType.AGENT,
                phase=Phase.PHASE_1_OFFCHAIN,
            )
        )

    with pytest.raises(IdentityError, match="one canonical Owner"):
        registry.register(
            ActorIdentity(
                actor_id="owner-2",
                subject="owner-subject-2",
                name="Second Owner",
                role=AgentRole.OWNER,
                actor_type=ActorType.OWNER,
                phase=Phase.PHASE_1_OFFCHAIN,
            )
        )

    with pytest.raises(IdentityError):
        registry.register(
            ActorIdentity(
                actor_id="agent-owner",
                subject="agent-owner-subject",
                name="Agent Owner",
                role=AgentRole.OWNER,
                actor_type=ActorType.AGENT,
                phase=Phase.PHASE_1_OFFCHAIN,
            )
        )


def test_token_context_resolves_role_from_registry() -> None:
    authenticator, audit, _ = make_authenticator()

    token = authenticator.issue_token("builder-subject")
    context = authenticator.authenticate(token)

    assert context.actor_id == "builder-1"
    assert context.role is AgentRole.BUILDER
    assert context.actor_type is ActorType.AGENT
    assert context.task_ids == {"task-1"}
    assert "role" not in token
    assert audit.events[-1].outcome is AuthOutcome.SUCCESS


def test_expiry_and_revocation_are_rejected_and_audited() -> None:
    authenticator, audit, now = make_authenticator()
    token = authenticator.issue_token("builder-subject", ttl_seconds=10)

    now[0] += 10
    with pytest.raises(AuthenticationError):
        authenticator.authenticate(token)
    assert audit.events[-1].outcome is AuthOutcome.DENIED

    now[0] = 1_700_000_000
    token = authenticator.issue_token("builder-subject")
    context = authenticator.authenticate(token)
    authenticator.registry.revoke_token(context.token_id)

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(token)
    assert audit.events[-1].reason == "token has been revoked"
    assert token not in audit.events[-1].model_dump_json()


def test_suspended_identity_and_wrong_phase_are_rejected() -> None:
    authenticator, _, _ = make_authenticator()
    token = authenticator.issue_token("builder-subject")
    authenticator.registry.suspend("builder-1")

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(token)

    phase_one_authenticator, _, _ = make_authenticator()
    phase_one_token = phase_one_authenticator.issue_token("builder-subject")
    devnet_verifier = Authenticator(
        secret=b"s" * 32,
        phase=Phase.DEVNET,
        registry=phase_one_authenticator.registry,
    )
    with pytest.raises(AuthenticationError):
        devnet_verifier.authenticate(phase_one_token)


def test_owner_and_task_membership_boundaries_are_enforced() -> None:
    authenticator, _, _ = make_authenticator()
    owner = authenticator.authenticate(authenticator.issue_token("owner-subject"))
    builder = authenticator.authenticate(
        authenticator.issue_token("builder-subject")
    )

    assert (
        authorize_actor_action(
            owner,
            PolicyAction.ISSUE_TASK,
        )
        is PolicyDecision.ALLOW
    )
    with pytest.raises(ActorAuthorizationError):
        authorize_actor_action(
            builder,
            PolicyAction.FINAL_ACCEPT_TASK,
            task_id="task-2",
        )
    with pytest.raises(ActorAuthorizationError):
        authorize_actor_action(
            builder,
            PolicyAction.FINAL_ACCEPT_TASK,
            task_id="task-1",
        )


def test_api_returns_authenticated_actor_context_and_policy_decision() -> None:
    authenticator, _, _ = make_authenticator()
    configure_runtime_authenticator(authenticator)
    client = TestClient(app)

    owner_token = authenticator.issue_token("owner-subject")
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 200
    assert response.json()["actor_id"] == "owner-1"
    assert response.json()["role"] == AgentRole.OWNER.value

    builder_token = authenticator.issue_token("builder-subject")
    response = client.post(
        "/api/v1/auth/policy-check",
        headers={"Authorization": f"Bearer {builder_token}"},
        json={"action": PolicyAction.FINAL_ACCEPT_TASK.value, "task_id": "task-1"},
    )
    assert response.status_code == 200
    assert response.json()["actor_id"] == "builder-1"
    assert response.json()["decision"] == PolicyDecision.REQUIRE_OWNER_APPROVAL.value

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_api_rejects_tampered_token() -> None:
    authenticator, _, _ = make_authenticator()
    configure_runtime_authenticator(authenticator)
    client = TestClient(app)

    token = authenticator.issue_token("owner-subject")
    header_segment, payload_segment, signature_segment = token.split(".")
    signature = bytearray(base64.urlsafe_b64decode(signature_segment + "=="))
    signature[0] ^= 0x01
    tampered_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    tampered = f"{header_segment}.{payload_segment}.{tampered_signature}"
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tampered}"},
    )

    assert response.status_code == 401


def test_owner_console_reads_require_owner_authentication() -> None:
    authenticator, _, _ = make_authenticator()
    configure_runtime_authenticator(authenticator)
    client = TestClient(app)

    assert client.get("/api/v1/dashboard").status_code == 401

    agent_token = authenticator.issue_token("builder-subject")
    response = client.get(
        "/api/v1/dashboard",
        headers={"Authorization": f"Bearer {agent_token}"},
    )
    assert response.status_code == 403

    owner_token = authenticator.issue_token("owner-subject")
    response = client.get(
        "/api/v1/dashboard",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 200
    assert response.json()["budget_status"]["mode"] == "VIRTUAL_LEDGER"


def test_runtime_rejects_checked_in_placeholder_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCAO_AUTH_SECRET", "__SET_IN_LOCAL_ENV__")
    with pytest.raises(AuthenticationError, match="replaced"):
        build_runtime_authenticator()
