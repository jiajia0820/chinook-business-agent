"""Low-privilege semantic routing proposals for otherwise ambiguous turns."""

import json
from typing import Literal

from pydantic import Field

from .state import BusinessTask
from ..tools.models import InternalModel


ROUTABLE_ROUTES = Literal["general", "sql", "rag", "cross_source", "clarification"]


class RouteProposal(InternalModel):
    route: ROUTABLE_ROUTES
    intent: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0.8, le=1)


def parse_route_proposal(payload: str) -> RouteProposal | None:
    try:
        return RouteProposal.model_validate_json(payload)
    except Exception:
        return None


def semantic_route_messages(question: str) -> list[dict[str, str]]:
    instructions = (
        "你是一个低权限问题分类器，只负责为模糊问题提出路由建议。"
        "可选 route 只有 general、sql、rag、cross_source、clarification。"
        "general 只表示一般知识解释；sql 表示需要业务数据库事实；rag 表示需要业务文档；"
        "cross_source 表示需要数据库和业务文档共同处理；不确定就选 clarification。"
        "不要调用工具，不要输出事实，不要编造项目或公司数据。置信度低于0.8时不要给高置信建议。"
        '只输出JSON：{"route":"...","intent":"...","confidence":0.0}。'
    )
    return [
        {"role": "system", "content": instructions},
        {"role": "user", "content": question},
    ]


def task_from_general_proposal(question: str, proposal: RouteProposal) -> BusinessTask | None:
    if proposal.route != "general" or proposal.intent != "general_knowledge":
        return None
    return BusinessTask(
        original_question=question,
        normalized_question=question,
        intent="general_knowledge",
        route="general",
        retrieval_question=question,
        source_roles=["general_knowledge"],
    )
