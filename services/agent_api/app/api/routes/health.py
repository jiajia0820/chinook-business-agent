"""Health-check endpoint."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict


router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    service: str
    run_mode: str | None = None
    model_mode: str | None = None


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = getattr(request.app.state, "settings", None)
    return HealthResponse(status="ok", service="agent-api",
                          run_mode=getattr(settings, "run_mode", None),
                          model_mode=getattr(settings, "model_mode", None))

