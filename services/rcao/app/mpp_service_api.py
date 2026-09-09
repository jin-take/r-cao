"""Standalone local MPP paid Service API.

Run only as an explicit local fixture::

    uvicorn app.mpp_service_api:app --host 127.0.0.1 --port 8011

The application is intentionally separate from the Owner Control Plane so a
paid Service cannot inherit Reward/Treasury command surfaces.  It exposes only
challenge creation and paid execution for an allowlisted mock Service.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .mpp_service import (
    MppPaidServiceAgent,
    MppServiceChallengeRequest,
    MppServiceDefinition,
    MppServiceError,
    MppServiceExecutionResult,
    MppServicePaymentSubmission,
    MppServiceRegistry,
)
from .payment_boundary import PaymentNetwork


app = FastAPI(
    title="R-CAO Local MPP Service",
    version="0.1.0",
    description="Local-only paid Service Agent fixture for MPP HTTP 402 validation.",
)


def _require_local_mode() -> None:
    mode = os.getenv("RCAO_MPP_SERVICE_MODE", "local").strip().lower()
    if mode != "local":
        raise RuntimeError(
            "mpp_service_api is a local fixture; RCAO_MPP_SERVICE_MODE must be local"
        )


_require_local_mode()

registry = MppServiceRegistry()
registry.register(
    MppPaidServiceAgent(
        service=MppServiceDefinition(
            service_id="local-echo",
            recipient="rcao-local-paid-service",
            token="LOCAL_TEST_USDC",
            amount_units=25,
            network=PaymentNetwork.LOCAL,
            cluster="LOCAL",
        ),
        executor=lambda request: {
            "message": "paid local Service executed",
            "task_id": request.task_id,
            "run_id": request.run_id,
        },
    )
)


@app.exception_handler(MppServiceError)
async def mpp_service_error(_, exc: MppServiceError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "rcao-local-mpp-service", "mode": "local", "status": "ok"}


@app.post("/api/v1/mpp/services/{service_id}")
def request_paid_service(
    service_id: str,
    request: MppServiceChallengeRequest,
) -> JSONResponse:
    """Return the MPP Challenge as an HTTP 402 response."""

    try:
        service = registry.require(service_id)
    except MppServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    challenge = service.issue_challenge(request)
    return JSONResponse(
        status_code=402,
        headers={"Cache-Control": "no-store"},
        content=challenge.model_dump(mode="json", exclude_none=True),
    )


@app.post(
    "/api/v1/mpp/services/{service_id}/paid",
    response_model=MppServiceExecutionResult,
)
def execute_paid_service(
    service_id: str,
    submission: MppServicePaymentSubmission,
) -> MppServiceExecutionResult:
    """Execute only after the proof matches the issued Challenge exactly."""

    try:
        service = registry.require(service_id)
    except MppServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service.execute(submission)
