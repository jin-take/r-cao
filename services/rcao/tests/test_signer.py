from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.mpp_policy import DirectSignerCallError, MppPolicyEngine
from app.payment_boundary import PaymentNetwork, PaymentPurpose, ServicePaymentRequest
from app.payment_profile import AgentPaymentProfile, PaymentApprovalMode, PaymentProfileNetwork
from app.policy import Phase, PolicyDecision
from app.signer import (
    DeterministicDevnetTransport,
    EncryptedKeyStore,
    InMemorySignerAuditLog,
    LocalDeterministicTransport,
    PolicyBoundSignerGateway,
    Signer,
    SignerIdempotencyError,
    SignerNetwork,
    SignerRequest,
    SignerRequestStatus,
    SignerWalletStatus,
    SOLANA_TOKEN_PROGRAM_ID,
    SolanaDevnetRpcTransport,
)


NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def profile(**overrides: object) -> AgentPaymentProfile:
    values: dict[str, object] = {
        "profile_id": "signer-profile",
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


def payment(**overrides: object) -> ServicePaymentRequest:
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


def prepared_local(
    *,
    store: EncryptedKeyStore | None = None,
    transport: LocalDeterministicTransport | None = None,
    **payment_overrides: object,
) -> tuple[Signer, PolicyBoundSignerGateway, SignerRequest, object, LocalDeterministicTransport]:
    current_store = store or EncryptedKeyStore(master_key=b"k" * 32)
    wallet = current_store.generate_wallet(
        wallet_id="wallet-builder",
        agent_id="agent-builder",
        network=SignerNetwork.LOCAL,
    )
    current_payment = payment(**payment_overrides)
    current_profile = profile()
    evaluation = MppPolicyEngine(clock=lambda: NOW).evaluate(
        payment=current_payment,
        profile=current_profile,
        phase=Phase.DEVNET,
        now=NOW,
    )
    assert evaluation.decision is PolicyDecision.ALLOW
    authorization = MppPolicyEngine(clock=lambda: NOW).issue_signer_authorization(
        evaluation,
        now=NOW,
    )
    request = SignerRequest.from_payment(
        current_payment,
        profile=current_profile,
        authorization=authorization,
        wallet=wallet,
    )
    current_transport = transport or LocalDeterministicTransport()
    signer = Signer(
        key_store=current_store,
        transport=current_transport,
        audit_log=InMemorySignerAuditLog(),
        clock=lambda: NOW,
    )
    return signer, PolicyBoundSignerGateway(signer), request, authorization, current_transport


def test_local_signer_keeps_keys_private_and_correlates_receipt() -> None:
    signer, gateway, request, authorization, transport = prepared_local()

    result, receipt = gateway.execute(request, authorization)

    assert result.status is SignerRequestStatus.CONFIRMED
    assert receipt is not None
    assert receipt.payment_id == request.payment_id
    assert receipt.challenge_id == request.challenge_id
    assert receipt.task_id == request.task_id
    assert receipt.run_id == request.run_id
    assert receipt.trace_id == request.trace_id
    assert receipt.correlation_id == request.correlation_id
    assert receipt.request_hash == result.request_hash
    assert len(transport.submissions) == 1
    assert "private" not in request.model_dump_json().lower()
    assert "private" not in receipt.model_dump_json().lower()
    assert all("signed_payload" not in event.canonical_payload() for event in signer.audit_log.events)
    assert [event.event_type for event in signer.audit_log.events] == [
        "SIGNER_REQUEST",
        "SIGNER_RESULT",
    ]

    replay, replay_receipt = gateway.execute(request, authorization)
    assert replay.result_id == result.result_id
    assert replay_receipt is not None
    assert replay_receipt.receipt_id == receipt.receipt_id
    assert len(transport.submissions) == 1


def test_encrypted_key_store_persists_only_ciphertext_and_public_identity(tmp_path) -> None:
    store = EncryptedKeyStore(master_key=b"p" * 32, directory=tmp_path)
    wallet = store.generate_wallet(
        wallet_id="wallet-persisted",
        agent_id="agent-builder",
        network=SignerNetwork.LOCAL,
    )
    record_path = tmp_path / "wallet-persisted.json"
    record = record_path.read_text(encoding="utf-8")

    assert wallet.public_key in record
    assert "ciphertext" in record
    assert "private_key" not in record
    assert "seed_phrase" not in record
    assert record_path.stat().st_mode & 0o777 == 0o600


def test_direct_signer_calls_are_rejected() -> None:
    signer, _, _, _, _ = prepared_local()

    with pytest.raises(DirectSignerCallError, match="direct Signer"):
        signer.execute()
    with pytest.raises(DirectSignerCallError, match="direct Signer"):
        signer.sign_and_submit()


@pytest.mark.parametrize(
    "field,value",
    [
        ("cluster", "MAINNET"),
        ("token", "VIRTUAL_REWARD"),
        ("recipient", "agent-reviewer"),
        ("program_id", "mainnet-program"),
        ("instruction", "TRANSFER_ANYTHING"),
    ],
)
def test_wrong_cluster_token_recipient_program_or_instruction_is_rejected(
    field: str, value: str
) -> None:
    _, _, request, _, _ = prepared_local()
    values = request.model_dump()
    values[field] = value

    with pytest.raises(ValidationError):
        SignerRequest(**values)


def test_amount_overrun_and_unknown_recipient_are_rejected() -> None:
    _, _, request, _, _ = prepared_local()

    with pytest.raises(ValidationError, match="per-payment"):
        values = request.model_dump()
        values["amount_units"] = 101
        SignerRequest(**values)

    with pytest.raises(ValidationError, match="allowlist"):
        values = request.model_dump()
        values["recipient"] = "unknown-service"
        SignerRequest(**values)


def test_reusing_idempotency_for_a_different_request_is_rejected() -> None:
    _, gateway, request, authorization, _ = prepared_local()
    gateway.execute(request, authorization)
    changed = request.model_copy(update={"amount_units": 26})

    with pytest.raises(SignerIdempotencyError):
        gateway.execute(changed, authorization)


def test_reusing_a_nonce_for_a_new_idempotency_key_is_rejected() -> None:
    _, gateway, request, authorization, transport = prepared_local()
    gateway.execute(request, authorization)
    changed = request.model_copy(
        update={"idempotency_key": "another-idempotency-key", "request_id": "another-request"}
    )

    with pytest.raises(SignerIdempotencyError, match="nonce"):
        gateway.execute(changed, authorization)
    assert len(transport.submissions) == 1


def test_failure_releases_budget_and_stop_is_audited_without_submission() -> None:
    transport = LocalDeterministicTransport(fail_next="devnet node unavailable")
    signer, gateway, request, authorization, _ = prepared_local(transport=transport)
    failed, receipt = gateway.execute(request, authorization)

    assert failed.status is SignerRequestStatus.FAILED
    assert failed.failure_code == "TRANSPORT_FAILURE"
    assert receipt is not None
    assert request.idempotency_key not in signer.budget_ledger.reservations
    assert signer.audit_log.events[-1].event_type == "SIGNER_FAILURE"

    class Stopped:
        def stop_reason(self, target: object, target_id: str) -> str | None:
            return "emergency stop" if getattr(target, "value", None) == "SIGNER" else None

    stopped_store = EncryptedKeyStore(master_key=b"s" * 32)
    wallet = stopped_store.generate_wallet(
        wallet_id="wallet-stopped",
        agent_id="agent-builder",
        network=SignerNetwork.LOCAL,
    )
    current_payment = payment(payment_id="payment-stopped", idempotency_key="idem-stopped")
    current_profile = profile()
    evaluation = MppPolicyEngine(clock=lambda: NOW).evaluate(
        payment=current_payment, profile=current_profile, phase=Phase.DEVNET, now=NOW
    )
    stopped_auth = MppPolicyEngine(clock=lambda: NOW).issue_signer_authorization(evaluation, now=NOW)
    stopped_request = SignerRequest.from_payment(
        current_payment,
        profile=current_profile,
        authorization=stopped_auth,
        wallet=wallet,
    )
    stopped_transport = LocalDeterministicTransport()
    stopped_signer = Signer(
        key_store=stopped_store,
        transport=stopped_transport,
        stop_controller=Stopped(),
        clock=lambda: NOW,
    )
    stopped_result, stopped_receipt = PolicyBoundSignerGateway(stopped_signer).execute(
        stopped_request, stopped_auth
    )
    assert stopped_result.status is SignerRequestStatus.STOPPED
    assert stopped_receipt is not None
    assert stopped_transport.submissions == []
    assert stopped_signer.audit_log.events[-1].event_type == "SIGNER_FAILURE"


def test_wallet_rotation_and_revocation_block_old_identity() -> None:
    store = EncryptedKeyStore(master_key=b"r" * 32)
    signer, gateway, request, authorization, _ = prepared_local(store=store)
    rotated = store.rotate_wallet(request.wallet_id)
    assert rotated.rotation_version == request.wallet_rotation_version + 1
    result, _ = gateway.execute(request, authorization)
    assert result.status is SignerRequestStatus.REJECTED
    assert result.failure_code == "REJECTED"

    revoked_store = EncryptedKeyStore(master_key=b"v" * 32)
    revoked_signer, revoked_gateway, revoked_request, revoked_auth, _ = prepared_local(
        store=revoked_store
    )
    revoked_signer.revoke_wallet(revoked_request.wallet_id)
    revoked_result, _ = revoked_gateway.execute(revoked_request, revoked_auth)
    assert revoked_result.status is SignerRequestStatus.REJECTED
    assert revoked_signer.key_store.public_identity(revoked_request.wallet_id).status is SignerWalletStatus.REVOKED


def test_devnet_transport_builds_a_deterministic_signed_submission() -> None:
    store = EncryptedKeyStore(master_key=b"d" * 32)
    wallet = store.generate_wallet(
        wallet_id="wallet-devnet",
        agent_id="agent-builder",
        network=SignerNetwork.SOLANA_DEVNET,
    )
    source = store.generate_wallet(
        wallet_id="source-devnet",
        agent_id="agent-builder",
        network=SignerNetwork.SOLANA_DEVNET,
    )
    current_profile = profile(
        profile_id="devnet-profile",
        network=PaymentProfileNetwork.SOLANA_DEVNET,
        token_allowlist=("SPL_TEST_TOKEN",),
        mint_allowlist=("SPL_TEST_MINT",),
        program_allowlist=(SOLANA_TOKEN_PROGRAM_ID,),
        wallet_id=wallet.wallet_id,
        public_key=wallet.public_key,
    )
    current_payment = payment(
        payment_id="payment-devnet",
        idempotency_key="idem-devnet",
        challenge_id="challenge-devnet",
        nonce="nonce-devnet",
        network=PaymentNetwork.SOLANA_DEVNET,
        token="SPL_TEST_TOKEN",
        program_id=SOLANA_TOKEN_PROGRAM_ID,
    )
    evaluation = MppPolicyEngine(clock=lambda: NOW).evaluate(
        payment=current_payment, profile=current_profile, phase=Phase.DEVNET, now=NOW
    )
    assert evaluation.decision is PolicyDecision.ALLOW
    authorization = MppPolicyEngine(clock=lambda: NOW).issue_signer_authorization(evaluation, now=NOW)
    request = SignerRequest.from_payment(
        current_payment,
        profile=current_profile,
        authorization=authorization,
        wallet=wallet,
        token_mint="SPL_TEST_MINT",
        source_token_account=source.public_key,
        recipient_token_account=wallet.public_key,
        recent_blockhash=wallet.public_key,
    )
    transport = DeterministicDevnetTransport()
    signer = Signer(key_store=store, transport=transport, clock=lambda: NOW)
    result, receipt = PolicyBoundSignerGateway(signer).execute(request, authorization)

    assert result.status is SignerRequestStatus.CONFIRMED
    assert receipt is not None
    assert receipt.network is SignerNetwork.SOLANA_DEVNET
    assert transport.submissions[0]["payload_hash"]


def test_mainnet_and_testnet_rpc_endpoints_are_forbidden() -> None:
    with pytest.raises(ValueError, match="devnet"):
        SolanaDevnetRpcTransport("https://api.mainnet-beta.solana.com")
    with pytest.raises(ValueError, match="devnet"):
        SolanaDevnetRpcTransport("https://api.testnet.solana.com")


def test_rpc_transport_uses_only_injected_devnet_methods() -> None:
    calls: list[tuple[str, list[object]]] = []

    def rpc_call(method: str, params: list[object]) -> object:
        calls.append((method, params))
        if method == "getLatestBlockhash":
            return {"result": {"value": {"blockhash": "11111111111111111111111111111111"}}}
        return {"result": "devnet-signature"}

    transport = SolanaDevnetRpcTransport("http://127.0.0.1:8899", rpc_call=rpc_call)
    assert transport.network is SignerNetwork.SOLANA_DEVNET
    assert transport.rpc_url.startswith("http://127.0.0.1")
    rpc_request = SimpleNamespace(network=SignerNetwork.SOLANA_DEVNET)
    assert transport.recent_blockhash(rpc_request) == "11111111111111111111111111111111"
    submission = transport.submit(rpc_request, b"signed-transaction")
    assert submission.external_signature == "devnet-signature"
    assert [method for method, _ in calls] == ["getLatestBlockhash", "sendTransaction"]
