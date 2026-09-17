"""Database-independent process liveness HTTP endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


class LivenessResponse(BaseModel):
    """Minimal process-liveness response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok"]


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
