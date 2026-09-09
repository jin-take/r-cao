# MPP Paid Service Agent

Issue #8 adds the receiving-side MPP boundary for local/devnet validation.

## Scope

The Service Agent is deliberately separated from Reward, Treasury, salary, and Agent-to-Agent transfer commands. A paid Service can only execute after a Task-bound Challenge and public payment proof match exactly.

Implemented checks:

- HTTP 402 Challenge generation with amount, token, recipient, expiry, Service, Task, run, trace, correlation, nonce, cluster, and idempotency key.
- Explicit Service allowlist through `MppServiceRegistry`.
- Task allowlist support per Service.
- Challenge hash validation.
- Payment ID, Challenge ID, idempotency key, network, cluster, token, and amount matching.
- Expiry and confirmation checks.
- Receipt reuse and external-signature replay detection.
- Idempotent retries return the original result without executing the Service twice.
- Secret-free audit payload correlating payer Agent, Task, Run, Service result, payment, receipt, and Challenge hash.

## Local fixture API

The mock paid Service is intentionally a standalone FastAPI application so it does not inherit Owner Control Plane mutation surfaces.

```bash
cd services/rcao
RCAO_MPP_SERVICE_MODE=local uvicorn app.mpp_service_api:app --host 127.0.0.1 --port 8011
```

Create a Challenge:

```http
POST /api/v1/mpp/services/local-echo
```

The endpoint returns `402 Payment Required` with the structured MPP Challenge.

Retry after the client obtains a public proof:

```http
POST /api/v1/mpp/services/local-echo/paid
```

The paid endpoint accepts `MppServicePaymentSubmission` and only returns the Service result when all Challenge/proof fields match.

## Security boundary

`LocalFixturePaymentVerifier` is structural and exists only for deterministic local tests. It must not be used for real assets or mainnet. A devnet integration must inject a trusted `PaymentConfirmationVerifier` that verifies chain/provider confirmation independently of caller-supplied fields.

The Service Agent does not call Reward Ledger, Treasury, salary, or Agent transfer APIs. Receipt persistence and Operations search are expanded by Issue #12; this issue emits the correlation-ready audit payload required for that integration.
