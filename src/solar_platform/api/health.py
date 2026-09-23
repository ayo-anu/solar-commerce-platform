"""Infrastructure-neutral liveness and database-readiness HTTP endpoints."""

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict


class LivenessResponse(BaseModel):
    """Minimal process-liveness response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    """Minimal response after the complete readiness probe succeeds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ready"]


router = APIRouter()


@router.get(
    "/health/live",
    operation_id="get_liveness",
    response_model=LivenessResponse,
    summary="Check process liveness",
    tags=["Health"],
)
async def get_liveness() -> LivenessResponse:
    """Report that this process can serve requests through its event loop."""
    return LivenessResponse(status="ok")


def create_readiness_router(readiness: Callable[[], bool]) -> APIRouter:
    """Bind an infrastructure-neutral synchronous readiness callable."""
    readiness_router = APIRouter()

    @readiness_router.get(
        "/health/ready",
        operation_id="get_readiness",
        response_model=ReadinessResponse,
        summary="Check service readiness",
        tags=["Health"],
    )
    def get_readiness() -> ReadinessResponse:
        if not readiness():
            raise HTTPException(status_code=503)
        return ReadinessResponse(status="ready")

    return readiness_router
