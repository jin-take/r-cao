from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.mpp_client import (
    MPP_CHALLENGE_SCHEMA_VERSION,
    MppApprovalError,
    MppChallenge,
    MppChallengeError,
    MppClient,
    MppClientStatus,
    MppHttpResponse,
    MppIdempotencyError,
    MppPaymentMethod,
    MppProviderError,
    MppProviderRegistry,
    MockMppProviderAdapter,
    MockMppService,
)
from app.mpp_policy import MppPolicyEngine
from app.payment_boundary import PaymentNetwork
from app.payment_profile import (
    AgentPaymentProfile,
    PaymentApprovalMode,
    PaymentProfileNetwork,
)
from app.policy import Phase, PolicyDecision
from app.signer import (
    DeterministicDevnetTransport,
    EncryptedKeyStore,
    InMemorySignerAuditLog,
    LocalDeterministicTransport,
    PolicyBoundSignerGateway,
    Signer,
    SignerNetwork,
    SOLANA_TOKEN_PROGRAM_ID,
)


NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def profile(**overrides: object) -> AgentPaymentProfile:
    values: dict[str, object] = {
        "profile_id": "mpp-client-profile",
        "agent_id": "agent-builder",
        "network": PaymentProfileNetwork.LOCAL,
        "service_id": "service.example.compute",
        "recipient": "service-account-001",
        "token_allowlist": ("LOCAL_TEST_TOKEN",),
        "service_allowlist": ("service.example.compute",),
        "recipient_allowlist": ("service-account-001",),
        "per_payment_limit_units": 100,
        "per_task_limit_units": 200,
        "daily_limit_units": 500,
        "auto_approval_limit_units": 100,
        "max_expiry_seconds": 3_600,
        "expires_at": NOW + timedelta(days=1),
        "approval_mode": PaymentApprovalMode.AUTO_ALLOW,
    }
    values.update(overrides)
    return AgentPaymentProfile(**values)


def challenge(**overrides: object) -> MppChallenge:
    values: dict[str, object] = {
        "schema_version": MPP_CHALLENGE_SCHEMA_VERSION,
        "payment_id": "payment-001",
        "challenge_id": "challenge-001",
        "service_id": "service.example.compute",
        "task_id": "task-001",
        "run_id": "run-001",
        "trace_id": "trace-001",
        "correlation_id": "correlation-001",
        "idempotency_key": "payment-idem-001",
        "nonce": "nonce-001",
        "payment_method": MppPaymentMethod.LOCAL_TEST,
        "network": PaymentNetwork.LOCAL,
        "token": "LOCAL_TEST_TOKEN",
        "recipient": "service-account-001",
        "amount_units": "25",
        "purpose": "SERVICE_PAYMENT",
        "expires_at": NOW + timedelta(minutes=5),
    }
    values.update(overrides)
    return MppChallenge(**values)


def make_client(
    *,
    current_profile: AgentPaymentProfile | None = None,
    current_challenge: MppChallenge | None = None,
    transport: LocalDeterministicTransport | None = None,
    phase: Phase = Phase.DEVNET,
    service_registered: bool = True,
    task_allowlist: tuple[str, ...] = (),
    run_allowlist: tuple[str, ...] = (),
    stop_controller: object | None = None,
) -> tuple[MppClient, MockMppService, LocalDeterministicTransport, EncryptedKeyStore]:
    selected_profile = current_profile or profile()
    selected_challenge = current_challenge or challenge()
    store = EncryptedKeyStore(master_key=b"m" * 32)
    wallet = store.generate_wallet(
        wallet_id="wallet-builder",
        agent_id="agent-builder",
        network=(
            SignerNetwork.LOCAL
            if selected_profile.network is PaymentProfileNetwork.LOCAL
            else SignerNetwork.SOLANA_DEVNET
        ),
    )
    selected_transport = transport or LocalDeterministicTransport(
        network=(
            SignerNetwork.LOCAL
            if selected_profile.network is PaymentProfileNetwork.LOCAL
            else SignerNetwork.SOLANA_DEVNET
        )
    )
    signer = Signer(
        key_store=store,
        transport=selected_transport,
        audit_log=InMemorySignerAuditLog(),
        stop_controller=stop_controller,
        clock=lambda: NOW,
    )
    service = MockMppService(selected_challenge)
    registry = MppProviderRegistry()
    registry.register(MockMppProviderAdapter(service))
    client = MppClient(
        profile=selected_profile,
        wallet=wallet,
        policy_engine=MppPolicyEngine(clock=lambda: NOW),
        signer_gateway=PolicyBoundSignerGateway(signer),
        provider_registry=registry,
        provider_id="mock-mpp",
        phase=phase,
        service_registered=service_registered,
        task_allowlist=task_allowlist,
        run_allowlist=run_allowlist,
        clock=lambda: NOW,
    )
    return client, service, selected_transport, store


def test_local_402_challenge_reaches_signer_and_retries_once_with_public_proof() -> None:
    client, service, transport, _ = make_client()

    result = client.pay(service.challenge_response())

    assert result.status is MppClientStatus.SUCCEEDED
    assert result.policy_decision is PolicyDecision.ALLOW
    assert result.attempts == 2
    assert result.proof is not None
    assert result.payment_receipt is not None
    assert result.signer_result is not None
    assert result.signer_result.status.value == "CONFIRMED"
    assert len(transport.submissions) == 1
    assert len(service.requests) == 1
    proof_payload = service.requests[0]["proof"]
    assert "signed_payload" not in proof_payload
    assert "private_key" not in str(proof_payload).lower()
    assert result.payment is not None
    assert result.payment.task_id == "task-001"
    assert result.payment.run_id == "run-001"
    assert result.payment.trace_id == "trace-001"
    assert result.payment.correlation_id == "correlation-001"


def test_challenge_wire_contract_is_strict_and_versioned() -> None:
    with pytest.raises(MppChallengeError, match="HTTP 402"):
        MppChallenge.from_http_response(
            MppHttpResponse(status_code=200, headers={"content-type": "application/json"}, body={})
        )
    with pytest.raises(MppChallengeError, match="JSON content type"):
        MppChallenge.from_http_response(
            MppHttpResponse(status_code=402, headers={"content-type": "text/plain"}, body={})
        )

    for amount in (25, "0", "01", "1.0", "1e2"):
        with pytest.raises((ValidationError, MppChallengeError)):
            MppChallenge.from_http_response(
                MppHttpResponse(
                    status_code=402,
                    headers={"content-type": "application/json"},
                    body=challenge().model_dump(mode="json") | {"amount_units": amount},
                )
            )

    with pytest.raises(MppChallengeError, match="extra"):
        MppChallenge.from_http_response(
            MppHttpResponse(
                status_code=402,
                headers={"content-type": "application/json"},
                body=challenge().model_dump(mode="json") | {"model_private_key": "nope"},
            )
        )


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"service_id": "unregistered.service"}, "Service"),
        ({"recipient": "another-service-account"}, "recipient"),
        ({"token": "LOCAL_TEST_OTHER"}, "token"),
    ],
)
def test_profile_allowlist_mismatch_is_denied_before_signer_or_provider(
    overrides: dict[str, object], expected: str
) -> None:
    changed = challenge(**overrides)
    client, service, transport, _ = make_client(current_challenge=changed)

    result = client.pay(service.challenge_response())

    assert result.status is MppClientStatus.DENIED
    assert expected.casefold() in result.reason.casefold()
    assert service.requests == []
    assert transport.submissions == []


def test_task_and_run_scope_mismatch_is_denied() -> None:
    client, service, transport, _ = make_client(
        task_allowlist=("different-task",),
        run_allowlist=("run-001",),
    )

    result = client.pay(service.challenge_response())

    assert result.status is MppClientStatus.DENIED
    assert "Task" in result.reason
    assert transport.submissions == []
    assert service.requests == []


def test_owner_approval_stays_pending_and_requires_verified_resume() -> None:
    client, service, transport, _ = make_client(
        current_profile=profile(approval_mode=PaymentApprovalMode.OWNER_APPROVAL)
    )

    pending = client.pay(service.challenge_response())
    assert pending.status is MppClientStatus.PENDING_APPROVAL
    assert pending.policy_decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
    assert service.requests == []
    assert transport.submissions == []

    with pytest.raises(MppApprovalError, match="approval_id"):
        client.resume("payment-idem-001", approved=True)

    denied = client.resume("payment-idem-001", approved=False)
    assert denied.status is MppClientStatus.DENIED
    assert service.requests == []
    assert transport.submissions == []

    # A denied approval is terminal and cannot later be turned into a payment.
    replay = client.resume("payment-idem-001", approved=True, approval_id="approval-001")
    assert replay.replayed is True
    assert replay.status is MppClientStatus.DENIED


def test_owner_approval_resume_executes_once_after_approval_id() -> None:
    client, service, transport, _ = make_client(
        current_profile=profile(approval_mode=PaymentApprovalMode.OWNER_APPROVAL)
    )

    pending = client.pay(service.challenge_response())
    assert pending.status is MppClientStatus.PENDING_APPROVAL
    completed = client.resume("payment-idem-001", approved=True, approval_id="approval-001")

    assert completed.status is MppClientStatus.SUCCEEDED
    assert completed.attempts == 2
    assert len(transport.submissions) == 1
    assert len(service.requests) == 1


@pytest.mark.parametrize("phase", [Phase.PHASE_1_OFFCHAIN, Phase.TESTNET, Phase.MAINNET])
def test_client_is_devnet_phase_only(phase: Phase) -> None:
    client, service, transport, _ = make_client(phase=phase)

    result = client.pay(service.challenge_response())

    assert result.status is MppClientStatus.DENIED
    assert "DEVNET" in result.reason
    assert service.requests == []
    assert transport.submissions == []


def test_idempotency_and_duplicate_challenge_nonce_prevent_double_payment() -> None:
    client, service, transport, _ = make_client()

    first = client.pay(service.challenge_response())
    replay = client.pay(service.challenge_response())

    assert first.status is MppClientStatus.SUCCEEDED
    assert replay.replayed is True
    assert replay.payment_receipt == first.payment_receipt
    assert len(service.requests) == 1
    assert len(transport.submissions) == 1

    changed_amount = challenge(amount_units="26")
    with pytest.raises(MppIdempotencyError, match="different Challenge"):
        client.pay_challenge(changed_amount)

    changed_key = challenge(idempotency_key="payment-idem-002")
    with pytest.raises(MppIdempotencyError, match="nonce"):
        client.pay_challenge(changed_key)


def test_concurrent_replays_submit_only_one_proof() -> None:
    client, service, transport, _ = make_client()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: client.pay(service.challenge_response()),
                range(4),
            )
        )

    assert sum(result.replayed for result in results) == 3
    assert len(service.requests) == 1
    assert len(transport.submissions) == 1


def test_expired_challenge_and_budget_overflow_never_reach_provider() -> None:
    expired_client, expired_service, expired_transport, _ = make_client(
        current_challenge=challenge(expires_at=NOW - timedelta(seconds=1))
    )
    expired = expired_client.pay(expired_service.challenge_response())
    assert expired.status is MppClientStatus.DENIED
    assert expired_transport.submissions == []
    assert expired_service.requests == []

    over_limit_client, over_limit_service, over_limit_transport, _ = make_client(
        current_challenge=challenge(amount_units="101")
    )
    over_limit = over_limit_client.pay(over_limit_service.challenge_response())
    assert over_limit.status is MppClientStatus.PENDING_APPROVAL
    assert over_limit.policy_decision is PolicyDecision.REQUIRE_OWNER_APPROVAL
    assert "limit" in over_limit.reason.casefold()
    assert over_limit_transport.submissions == []
    assert over_limit_service.requests == []


def test_signer_failure_and_stop_are_terminal_without_provider_retry() -> None:
    failed_transport = LocalDeterministicTransport(fail_next="validator unavailable")
    failed_client, failed_service, failed_transport, _ = make_client(transport=failed_transport)
    failed = failed_client.pay(failed_service.challenge_response())
    assert failed.status is MppClientStatus.FAILED
    assert failed.attempts == 1
    assert failed.signer_result is not None
    assert failed.signer_result.failure_code == "TRANSPORT_FAILURE"
    assert failed_service.requests == []

    class Stopped:
        def stop_reason(self, target: object, target_id: str) -> str | None:
            return "emergency stop" if getattr(target, "value", None) == "SIGNER" else None

    stopped_client, stopped_service, stopped_transport, _ = make_client(
        stop_controller=Stopped()
    )
    stopped = stopped_client.pay(stopped_service.challenge_response())
    assert stopped.status is MppClientStatus.STOPPED
    assert stopped_service.requests == []
    assert stopped_transport.submissions == []


def test_unknown_provider_fails_closed() -> None:
    client, service, _, _ = make_client()
    client.provider_id = "not-registered"

    with pytest.raises(MppProviderError, match="not registered"):
        client.pay(service.challenge_response())


def test_devnet_challenge_uses_devnet_signer_and_spl_fields() -> None:
    store = EncryptedKeyStore(master_key=b"d" * 32)
    wallet = store.generate_wallet(
        wallet_id="wallet-builder",
        agent_id="agent-builder",
        network=SignerNetwork.SOLANA_DEVNET,
    )
    recipient_account = store.generate_wallet(
        wallet_id="recipient-account",
        agent_id="agent-builder",
        network=SignerNetwork.SOLANA_DEVNET,
    )
    current_profile = profile(
        network=PaymentProfileNetwork.SOLANA_DEVNET,
        token_allowlist=("SPL_TEST_TOKEN",),
        mint_allowlist=("SPL_TEST_MINT",),
        program_allowlist=(SOLANA_TOKEN_PROGRAM_ID,),
    )
    current_challenge = challenge(
        payment_method=MppPaymentMethod.SPL_TOKEN,
        network=PaymentNetwork.SOLANA_DEVNET,
        cluster="DEVNET",
        token="SPL_TEST_TOKEN",
        token_mint="SPL_TEST_MINT",
        program_id=SOLANA_TOKEN_PROGRAM_ID,
        source_token_account=wallet.public_key,
        recipient_token_account=recipient_account.public_key,
    )
    transport = DeterministicDevnetTransport()
    signer = Signer(
        key_store=store,
        transport=transport,
        audit_log=InMemorySignerAuditLog(),
        clock=lambda: NOW,
    )
    service = MockMppService(current_challenge)
    registry = MppProviderRegistry()
    registry.register(MockMppProviderAdapter(service))
    client = MppClient(
        profile=current_profile,
        wallet=wallet,
        policy_engine=MppPolicyEngine(clock=lambda: NOW),
        signer_gateway=PolicyBoundSignerGateway(signer),
        provider_registry=registry,
        provider_id="mock-mpp",
        phase=Phase.DEVNET,
        clock=lambda: NOW,
    )

    result = client.pay(service.challenge_response())

    assert result.status is MppClientStatus.SUCCEEDED
    assert result.proof is not None
    assert result.proof.network is PaymentNetwork.SOLANA_DEVNET
    assert transport.submissions[0]["request_id"] == "signer-request-payment-001"
