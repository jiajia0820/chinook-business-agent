"""Public question-answer endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ...contracts import ApiError, AskRequest, AskResponse
from ...services.ask_service import AskService, get_ask_service


router = APIRouter(prefix="/api/v1", tags=["question-answering"])


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={
        422: {"model": ApiError, "description": "请求格式错误"},
        500: {"model": ApiError, "description": "服务内部异常"},
        503: {"model": ApiError, "description": "Agent 服务尚未就绪"},
    },
)
async def ask(
    payload: AskRequest,
    request: Request,
    service: Annotated[AskService, Depends(get_ask_service)],
) -> AskResponse:
    return await service.ask(payload, request_id=request.state.request_id)

