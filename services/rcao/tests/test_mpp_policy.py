from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.mpp_policy import (
    DirectSignerCallError,
    InMemoryMppBudgetRepository,
    MppBudgetExceededError,
    MppPolicyEngine,
    MppPolicyInput,
    MppReservationStatus,
    MppSignerAuthorizationError,
)
from app.payment_boundary import (
    PaymentNetwork,
    PaymentPurpose,
    ServicePaymentRequest,
)
from app.payment_profile import (
    AgentPaymentProfile,
    PaymentApprovalMode,
    PaymentProfileNetwork,
    PaymentProfileStatus,
)
from app.policy import Phase, PolicyDecision


NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def profile(**overrides: object) -> AgentPaymentProfile:
    values: dict[str, object] = {
        "profile_id": "mpp-profile",
        "agent_id": "agent-builder",
        "network": PaymentProfileNetwork.LOCAL,
        "service_id": "service.example.compute",
        "recipient": "service-account-001",
        "token_allowlist": ("LOCAL_TEST_TOKEN",),
        "service_allowlist": ("service.example.compute",),
        "recipient_allowlist": ("service-account-001",),
        "per_payment_limit_units": 100,
        "per_task_limit_units": 100,
        "daily_limit_units": 100,
        "auto_approval_limit_units": 100,
        "max_expiry_seconds": 3_600,
        "expires_at": NOW + timedelta(days=1),
        "approval_mode": PaymentApprovalMode.AUTO_ALLOW,
    }
    values.update(overrides)
    return AgentPaymentProfile(**values)


def request(**overrides: object) -> ServicePaymentRequest:
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
        "amount_units": 25,
        "purpose": PaymentPurpose.SERVICE_PAYMENT,
        "expires_at": NOW + timedelta(minutes=5),
    }
    values.update(overrides)
    return ServicePaymentRequest(**values)


def evaluate(**overrides: object):
    current = request()
    values: dict[str, object] = {
        "profile": profile(),
        "phase": Phase.DEVNET,
        "now": NOW,
    }
    values.update(overrides)
    return MppPolicyEngine(clock=lambda: NOW).evaluate(
        payment=current,
        **{key: value for key, value in values.items() if key != "now"},
    )


def test_allow_can_issue_only_a_short_lived_non_secret_capability() -> None:
    decision = evaluate()

    assert decision.decision is PolicyDecision.ALLOW
    assert decision.policy_version == "mpp-policy-engine-v1"
    authorization = MppPolicyEngine(clock=lambda: NOW).issue_signer_authorization(
        decision,
        now=NOW,
    )
    assert authorization.payment_id == decision.payment_id
    assert authorization.policy_decision_id == decision.decision_id
    assert "private" not in authorization.model_dump_json().lower()
    assert authorization.expires_at <= NOW + timedelta(seconds=60)


def test_owner_approval_never_reaches_signer_before_verification() -> None:
    decision = evaluate(
        profile=profile(approval_mode=PaymentApprovalMode.OWNER_APPROVAL),
    )

    assert decision.decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
    with pytest.raises(MppSignerAuthorizationError):
        MppPolicyEngine(clock=lambda: NOW).issue_signer_authorization(
            decision,
            now=NOW,
        )

    approved = evaluate(
        profile=profile(approval_mode=PaymentApprovalMode.OWNER_APPROVAL),
        owner_approval_id="approval-001",
        approval_verified=True,
    )
    assert approved.decision is PolicyDecision.ALLOW


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile": None},
        {"profile": profile(status=PaymentProfileStatus.SUSPENDED)},
        {"phase": Phase.PHASE_1_OFFCHAIN},
        {"agent_status": "STOPPED"},
        {"mpp_status": "STOPPED"},
        {"provider_status": "STOPPED"},
        {"signer_status": "STOPPED"},
        {"expires_at": NOW - timedelta(seconds=1)},
    ],
)
def test_policy_fails_closed_for_stops_and_invalid_context(
    overrides: dict[str, object],
) -> None:
    if "expires_at" in overrides:
        current_request = request(expires_at=overrides.pop("expires_at"))
        decision = MppPolicyEngine(clock=lambda: NOW).evaluate(
            payment=current_request,
            profile=profile(),
            phase=Phase.DEVNET,
        )
    else:
        decision = evaluate(**overrides)
    assert decision.decision is PolicyDecision.DENY


def test_operational_stop_checker_denies_new_payment() -> None:
    context = MppPolicyInput.from_request(
        request(),
        profile=profile(),
        phase=Phase.DEVNET,
    )
    decision = MppPolicyEngine(
        clock=lambda: NOW,
        stop_checker=lambda target, target_id: (
            "emergency hold" if target.value == "MPP" else None
        ),
    ).evaluate(context)
    assert decision.decision is PolicyDecision.DENY
    assert "emergency hold" in decision.reason


def test_direct_signer_call_is_an_explicit_policy_error() -> None:
    with pytest.raises(DirectSignerCallError):
        MppPolicyEngine.reject_direct_signer_call(operation="send")


def test_budget_reservation_is_idempotent_and_concurrency_safe() -> None:
    budget = InMemoryMppBudgetRepository()
    current_profile = profile()

    first = budget.reserve(
        profile=current_profile,
        payment_id="payment-001",
        idempotency_key="payment-idem-001",
        task_id="task-001",
        agent_id="agent-builder",
        amount_units=25,
        correlation_id="correlation-001",
        now=NOW,
    )
    replay = budget.reserve(
        profile=current_profile,
        payment_id="payment-001",
        idempotency_key="payment-idem-001",
        task_id="task-001",
        agent_id="agent-builder",
        amount_units=25,
        correlation_id="correlation-001",
        now=NOW,
    )
    assert first.reservation.reservation_id == replay.reservation.reservation_id
    assert replay.replayed is True

    def reserve(index: int) -> bool:
        try:
            budget.reserve(
                profile=current_profile,
                payment_id=f"payment-{index + 2}",
                idempotency_key=f"payment-idem-{index + 2}",
                task_id="task-001",
                agent_id="agent-builder",
                amount_units=10,
                correlation_id=f"correlation-{index + 2}",
                now=NOW,
            )
            return True
        except MppBudgetExceededError:
            return False

    with ThreadPoolExecutor(max_workers=20) as executor:
        outcomes = list(executor.map(reserve, range(20)))
    # 25 is already reserved, leaving capacity for exactly seven 10-unit
    # reservations under the 100-unit Task and daily limits.
    assert sum(outcomes) == 7

    consumed = budget.consume(first.reservation.reservation_id, now=NOW)
    assert consumed.status is MppReservationStatus.CONSUMED
    assert budget.current_spend(
        profile_id=current_profile.profile_id,
        task_id="task-001",
        agent_id="agent-builder",
        now=NOW,
    )[0] == 95
