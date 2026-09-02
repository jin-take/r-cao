from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.payment_profile import (
    AgentPaymentProfile,
    AgentPaymentProfilePolicy,
    PaymentApprovalMode,
    PaymentProfileError,
    PaymentProfileNetwork,
    PaymentProfileRotationState,
    PaymentProfileStatus,
)
from app.policy import PolicyDecision


NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def profile(**overrides: object) -> AgentPaymentProfile:
    values: dict[str, object] = {
        "profile_id": "profile-001",
        "agent_id": "agent-builder",
        "network": PaymentProfileNetwork.LOCAL,
        "service_id": "service.example.compute",
        "recipient": "service-account-001",
        "token_allowlist": ("LOCAL_TEST_TOKEN",),
        "service_allowlist": ("service.example.compute",),
        "recipient_allowlist": ("service-account-001",),
        "program_allowlist": ("program-compute-v1",),
        "per_payment_limit_units": 1_000,
        "per_task_limit_units": 2_000,
        "daily_limit_units": 5_000,
        "auto_approval_limit_units": 500,
        "max_expiry_seconds": 3_600,
        "expires_at": NOW + timedelta(days=1),
        "approval_mode": PaymentApprovalMode.AUTO_ALLOW,
    }
    values.update(overrides)
    return AgentPaymentProfile(**values)


def evaluate(**overrides: object):
    values: dict[str, object] = {
        "profile": profile(),
        "agent_id": "agent-builder",
        "service_id": "service.example.compute",
        "recipient": "service-account-001",
        "network": PaymentProfileNetwork.LOCAL,
        "token": "LOCAL_TEST_TOKEN",
        "amount_units": 100,
        "purpose": "SERVICE_PAYMENT",
        "program_id": "program-compute-v1",
        "expires_at": NOW + timedelta(minutes=5),
        "now": NOW,
    }
    values.update(overrides)
    return AgentPaymentProfilePolicy.evaluate(**values)


def test_profile_contract_is_secret_free_and_strict() -> None:
    current = profile()
    assert current.cluster == "LOCAL"
    assert "private_key" not in current.model_dump()
    assert "seed_phrase" not in current.model_dump()

    with pytest.raises(ValidationError, match="extra|private"):
        profile(private_key="must-not-be-accepted")


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_allowlist": ("VIRTUAL_REWARD",)},
        {"token_allowlist": ("SPL_TEST_USDC",)},
        {"recipient": "agent-other", "recipient_allowlist": ("agent-other",)},
        {"purpose_allowlist": ("REWARD",)},
        {"network": PaymentProfileNetwork.SOLANA_DEVNET, "cluster": "LOCAL"},
        {"per_payment_limit_units": 2_001},
    ],
)
def test_profile_rejects_unsafe_or_inconsistent_contracts(
    overrides: dict[str, object],
) -> None:
    with pytest.raises((ValidationError, ValueError)):
        profile(**overrides)


def test_profile_policy_allows_only_bounded_payment() -> None:
    decision = evaluate()
    assert decision.decision is PolicyDecision.ALLOW
    assert decision.profile_id == "profile-001"
    assert decision.profile_version == 1

    assert evaluate(amount_units=501).decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
    assert evaluate(task_spent_units=1_950).decision is PolicyDecision.DENY
    assert evaluate(daily_spent_units=4_950).decision is PolicyDecision.DENY
    assert evaluate(program_id="program-other").decision is PolicyDecision.DENY


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile": profile(status=PaymentProfileStatus.SUSPENDED)},
        {"profile": profile(rotation_state=PaymentProfileRotationState.PENDING)},
        {"profile": profile(expires_at=NOW)},
        {"agent_id": "agent-other"},
        {"service_id": "service-other"},
        {"recipient": "service-other"},
        {"network": PaymentProfileNetwork.SOLANA_DEVNET},
        {"token": "LOCAL_TEST_OTHER"},
        {"purpose": "REWARD"},
        {"expires_at": NOW + timedelta(hours=2)},
    ],
)
def test_profile_policy_fails_closed_for_out_of_scope_requests(
    overrides: dict[str, object],
) -> None:
    assert evaluate(**overrides).decision is PolicyDecision.DENY


def test_owner_approval_mode_never_returns_allow() -> None:
    decision = evaluate(
        profile=profile(approval_mode=PaymentApprovalMode.OWNER_APPROVAL),
    )
    assert decision.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
    assert decision.decision is not PolicyDecision.ALLOW


def test_deny_mode_is_not_recoverable_by_amount() -> None:
    decision = evaluate(profile=profile(approval_mode=PaymentApprovalMode.DENY))
    assert decision.decision is PolicyDecision.DENY


def test_ensure_usable_raises_only_for_deny() -> None:
    assert AgentPaymentProfilePolicy.ensure_usable(profile(), **{
        "agent_id": "agent-builder",
        "service_id": "service.example.compute",
        "recipient": "service-account-001",
        "network": PaymentProfileNetwork.LOCAL,
        "token": "LOCAL_TEST_TOKEN",
        "amount_units": 100,
        "program_id": "program-compute-v1",
        "expires_at": NOW + timedelta(minutes=5),
        "now": NOW,
    }).decision is PolicyDecision.ALLOW
    with pytest.raises(PaymentProfileError):
        AgentPaymentProfilePolicy.ensure_usable(
            profile(),
            agent_id="agent-builder",
            service_id="service.example.compute",
            recipient="service-account-001",
            network=PaymentProfileNetwork.LOCAL,
            token="LOCAL_TEST_TOKEN",
            amount_units=100,
            program_id="program-other",
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )
