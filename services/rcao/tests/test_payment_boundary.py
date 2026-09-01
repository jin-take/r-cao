from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.payment_boundary import (
    DirectAgentTransferError,
    PaymentNetwork,
    PaymentPolicyError,
    PaymentPurpose,
    ServicePaymentRequest,
    ServicePaymentStatus,
    evaluate_service_payment,
)
from app.models import AgentRole
from app.policy import Phase, PolicyAction, PolicyDecision, evaluate_policy


def payment_request(**overrides: object) -> ServicePaymentRequest:
    values: dict[str, object] = {
        "payment_id": "payment-001",
        "idempotency_key": "payment-idem-001",
        "challenge_id": "challenge-001",
        "nonce": "nonce-001",
        "task_id": "task-001",
        "run_id": "run-001",
        "trace_id": "trace-001",
        "correlation_id": "correlation-001",
        "agent_id": "agent-builder",
        "service_id": "service.example.compute",
        "recipient": "service-account-001",
        "network": PaymentNetwork.LOCAL,
        "token": "LOCAL_TEST_TOKEN",
        "amount_units": 1250,
        "purpose": PaymentPurpose.SERVICE_PAYMENT,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    values.update(overrides)
    return ServicePaymentRequest(**values)


def test_service_payment_request_is_strict_and_hashable() -> None:
    request = payment_request()

    assert request.amount_units == 1250
    assert len(request.challenge_hash()) == 64
    assert request.canonical_payload()["amount_units"] == "1250"


def test_payment_purpose_is_required_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="purpose"):
        values = payment_request().model_dump()
        del values["purpose"]
        ServicePaymentRequest(**values)

    with pytest.raises(ValidationError, match="private_key|extra"):
        payment_request(private_key="must-not-cross-the-boundary")
    with pytest.raises(ValidationError, match="reward|extra"):
        payment_request(reward_budget_lamports=1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_units": 0},
        {"amount_units": -1},
        {"amount_units": "1250"},
        {"amount_units": (1 << 63)},
        {"purpose": "REWARD"},
    ],
)
def test_malformed_payment_values_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        payment_request(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"token": "VIRTUAL_REWARD"},
        {"token": "TREASURY"},
        {"token": "SOL"},
    ],
)
def test_reward_treasury_and_fee_assets_cannot_be_service_payment_tokens(
    overrides: dict[str, object],
) -> None:
    request = payment_request(**overrides)
    with pytest.raises(PaymentPolicyError):
        evaluate_service_payment(request, phase=Phase.DEVNET)


def test_agent_recipient_is_not_a_service_payment() -> None:
    request = payment_request(recipient="agent-reviewer")

    with pytest.raises(DirectAgentTransferError):
        evaluate_service_payment(request, phase=Phase.DEVNET)


def test_service_payment_is_devnet_or_local_only_and_expiry_is_fail_closed() -> None:
    request = payment_request()
    assert evaluate_service_payment(request, phase=Phase.PHASE_1_OFFCHAIN).decision is PolicyDecision.DENY
    assert evaluate_service_payment(
        payment_request(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)),
        phase=Phase.DEVNET,
    ).decision is PolicyDecision.DENY


def test_profile_can_require_owner_approval_without_authorizing_execution() -> None:
    evaluation = evaluate_service_payment(
        payment_request(),
        phase=Phase.DEVNET,
        owner_approval_required=True,
    )

    assert evaluation.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
    assert ServicePaymentStatus.APPROVAL_REQUIRED.value == "APPROVAL_REQUIRED"


def test_policy_exposes_a_separate_service_payment_action() -> None:
    assert (
        evaluate_policy(
            AgentRole.BUILDER,
            PolicyAction.REQUEST_SERVICE_PAYMENT,
            phase=Phase.DEVNET,
        )
        is PolicyDecision.ALLOW
    )
    assert (
        evaluate_policy(
            AgentRole.BUILDER,
            PolicyAction.REQUEST_SERVICE_PAYMENT,
            phase=Phase.PHASE_1_OFFCHAIN,
        )
        is PolicyDecision.DENY
    )
    assert (
        evaluate_policy(
            AgentRole.BUILDER,
            PolicyAction.EXECUTE_SERVICE_PAYMENT,
            phase=Phase.DEVNET,
        )
        is PolicyDecision.REQUIRE_OWNER_APPROVAL
    )
    assert (
        evaluate_policy(
            AgentRole.BUILDER,
            PolicyAction.DIRECT_AGENT_TRANSFER,
            phase=Phase.DEVNET,
        )
        is PolicyDecision.DENY
    )
