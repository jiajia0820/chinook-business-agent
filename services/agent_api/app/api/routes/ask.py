"""Public question-answer endpoint."""

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ...contracts import ApiError, AskRequest, AskResponse
from ...services.ask_service import AskService, get_ask_service


router = APIRouter(prefix="/api/v1", tags=["question-answering"])

# Graph node names are stable internal identifiers; labels are display-only.
STAGE_LABELS = {
    "initialize": "会话初始化",
    "parse": "意图与时间解析",
    "validate": "请求契约校验",
    "start_route": "路由判定",
    "context": "会话上下文装载",
    "call_sql": "SQL 生成与只读执行",
    "after_sql": "SQL 结果整理",
    "call_rag": "文档片段检索",
    "after_rag": "检索结果整理",
    "calculate": "业务计算",
    "evidence_check": "证据充分性校验",
    "compose": "答案与证据组装",
}


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


@router.post(
    "/ask/stream",
    responses={
        422: {"model": ApiError, "description": "请求格式错误"},
        500: {"model": ApiError, "description": "服务内部异常"},
        503: {"model": ApiError, "description": "Agent 服务尚未就绪"},
    },
)
async def ask_stream(
    payload: AskRequest,
    request: Request,
    service: Annotated[AskService, Depends(get_ask_service)],
) -> StreamingResponse:
    # Preflight failures raise here, before any byte of the stream is sent.
    stream = service.ask_stream(payload, request_id=request.state.request_id)

    async def events():
        try:
            async for kind, value in stream:
                if kind == "stage":
                    data = json.dumps({"node": value, "label": STAGE_LABELS.get(value, value)}, ensure_ascii=False)
                    yield f"event: stage\ndata: {data}\n\n"
                else:
                    yield f"event: result\ndata: {value.model_dump_json()}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never leak internals into a public progress stream.
            data = json.dumps({"code": "STREAM_INTERRUPTED", "message": "进度流中断；本轮结果不可用，请重新提问。", "retryable": False}, ensure_ascii=False)
            yield f"event: error\ndata: {data}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

