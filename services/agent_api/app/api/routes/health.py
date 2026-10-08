"""Health-check endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    service: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="agent-api")

