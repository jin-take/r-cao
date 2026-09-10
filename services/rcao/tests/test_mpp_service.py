from datetime import datetime, timedelta, timezone

import pytest

from app.mpp_client import MppPaymentProof
from app.mpp_service import (
    MppPaidServiceAgent,
    MppServiceChallengeRequest,
    MppServiceDefinition,
    MppServicePaymentRejected,
    MppServicePaymentSubmission,
    MppServiceReplayError,
)
from app.payment_boundary import PaymentNetwork


def _request(key: str = "idem-1", task_id: str = "task-1") -> MppServiceChallengeRequest:
    return MppServiceChallengeRequest(
        agent_id="agent-alpha",
        task_id=task_id,
        run_id="run-1",
        trace_id="trace-1",
        correlation_id="corr-1",
        idempotency_key=key,
    )


def _agent(now: datetime | None = None) -> MppPaidServiceAgent:
    current = now or datetime.now(timezone.utc)
    return MppPaidServiceAgent(
        service=MppServiceDefinition(
            service_id="svc-local-echo",
            recipient="svc-local-recipient",
            token="LOCAL_TEST_USDC",
            amount_units=25,
            network=PaymentNetwork.LOCAL,
            cluster="LOCAL",
            allowed_tasks=("task-1", "task-2"),
        ),
        executor=lambda request: {"echo": request.task_id},
        clock=lambda: current,
    )


def _proof(challenge, *, signature: str = "sig-1", amount: int | None = None) -> MppPaymentProof:
    return MppPaymentProof(
        payment_id=challenge.payment_id,
        challenge_id=challenge.challenge_id,
        idempotency_key=challenge.idempotency_key,
        signer_request_id="signer-request-1",
        signer_result_id="signer-result-1",
        signer_receipt_id="signer-receipt-1",
        request_hash="a" * 64,
        external_signature=signature,
        network=challenge.network,
        token=challenge.token,
        amount_units=amount if amount is not None else challenge.amount,
    )


def _submission(challenge, *, receipt_id: str = "receipt-1", signature: str = "sig-1", cluster: str = "LOCAL", confirmed: bool = True, amount: int | None = None):
    return MppServicePaymentSubmission(
        challenge_id=challenge.challenge_id,
        challenge_hash=challenge.challenge_hash(),
        cluster=cluster,
        proof=_proof(challenge, signature=signature, amount=amount),
        receipt_id=receipt_id,
        confirmed=confirmed,
    )


def test_issue_challenge_is_http_402_compatible_and_idempotent():
    agent = _agent()
    request = _request()

    first = agent.issue_challenge(request)
    second = agent.issue_challenge(request)

    assert first == second
    assert first.service_id == "svc-local-echo"
    assert first.task_id == "task-1"
    assert first.network is PaymentNetwork.LOCAL
    assert first.cluster == "LOCAL"
    assert first.token == "LOCAL_TEST_USDC"
    assert first.amount == 25


def test_execute_returns_correlated_service_result_and_audit_payload():
    agent = _agent()
    challenge = agent.issue_challenge(_request())

    result = agent.execute(_submission(challenge))

    assert result.status == "SUCCEEDED"
    assert result.result == {"echo": "task-1"}
    assert result.task_id == "task-1"
    assert result.payment_id == challenge.payment_id
    assert result.audit["event_type"] == "MPP_SERVICE_EXECUTED"
    assert result.audit["challenge_hash"] == challenge.challenge_hash()
    assert result.audit["receipt_id"] == "receipt-1"
    assert result.audit["payer_agent_id"] == "agent-alpha"


def test_exact_retry_is_idempotent_without_second_service_execution():
    calls = []
    current = datetime.now(timezone.utc)
    agent = MppPaidServiceAgent(
        service=MppServiceDefinition(
            service_id="svc-local-echo",
            recipient="svc-local-recipient",
            token="LOCAL_TEST_USDC",
            amount_units=25,
        ),
        executor=lambda request: calls.append(request.task_id) or {"ok": True},
        clock=lambda: current,
    )
    challenge = agent.issue_challenge(_request())
    submission = _submission(challenge)

    first = agent.execute(submission)
    second = agent.execute(submission)

    assert first.replayed is False
    assert second.replayed is True
    assert calls == ["task-1"]


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda challenge: _submission(challenge, cluster="DEVNET"), "cluster"),
        (lambda challenge: _submission(challenge, confirmed=False), "not confirmed"),
        (lambda challenge: _submission(challenge, amount=26), "does not match"),
    ],
)
def test_rejects_invalid_payment(mutator, match):
    agent = _agent()
    challenge = agent.issue_challenge(_request())

    with pytest.raises(MppServicePaymentRejected, match=match):
        agent.execute(mutator(challenge))


def test_rejects_tampered_challenge_hash():
    agent = _agent()
    challenge = agent.issue_challenge(_request())
    submission = _submission(challenge).model_copy(update={"challenge_hash": "0" * 64})

    with pytest.raises(MppServicePaymentRejected, match="hash mismatch"):
        agent.execute(submission)


def test_rejects_receipt_and_proof_reuse_across_challenges():
    agent = _agent()
    challenge_one = agent.issue_challenge(_request("idem-1", "task-1"))
    agent.execute(_submission(challenge_one, receipt_id="receipt-shared", signature="sig-shared"))

    challenge_two = agent.issue_challenge(_request("idem-2", "task-2"))
    with pytest.raises(MppServiceReplayError, match="receipt was already used"):
        agent.execute(
            _submission(
                challenge_two,
                receipt_id="receipt-shared",
                signature="sig-new",
            )
        )
    with pytest.raises(MppServiceReplayError, match="payment proof was already used"):
        agent.execute(
            _submission(
                challenge_two,
                receipt_id="receipt-new",
                signature="sig-shared",
            )
        )


def test_rejects_expired_and_non_allowlisted_task():
    current = datetime.now(timezone.utc)
    agent = _agent(current)
    with pytest.raises(MppServicePaymentRejected, match="not allowlisted"):
        agent.issue_challenge(_request(task_id="task-99"))

    challenge = agent.issue_challenge(_request())
    agent.clock = lambda: challenge.expires_at + timedelta(seconds=1)
    with pytest.raises(MppServicePaymentRejected, match="expired"):
        agent.execute(_submission(challenge))
