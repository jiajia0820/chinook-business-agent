"""Public catalog of data profiles available to the current worker."""

from fastapi import APIRouter, Request

from ...contracts import ProfileOption


router = APIRouter(prefix="/api/v1", tags=["profiles"])


@router.get("/profiles", response_model=list[ProfileOption])
async def profiles(request: Request) -> list[ProfileOption]:
    return [ProfileOption.model_validate(item) for item in getattr(request.app.state, "profile_catalog", ())]
