"""Low-privilege semantic routing proposals for otherwise ambiguous turns."""

from typing import Literal

from pydantic import Field

from .state import BusinessTask
from ..tools.models import InternalModel


QuestionType = Literal["general_knowledge", "business_fact", "business_rule", "mixed_business", "unclear"]


class RouteProposal(InternalModel):
    question_type: QuestionType
    needs_tool: bool
    confidence: float = Field(ge=0.8, le=1)


def parse_route_proposal(payload: str) -> RouteProposal | None:
    try:
        return RouteProposal.model_validate_json(payload)
    except Exception:
        return None


def semantic_route_messages(question: str) -> list[dict[str, str]]:
    instructions = (
        "你是一个低权限问题分类器，只负责为模糊问题判断问题类型和是否需要工具。"
        "question_type 只能是 general_knowledge、business_fact、business_rule、mixed_business、unclear。"
        "general_knowledge 表示一般知识解释；business_fact 表示需要业务数据库事实；"
        "business_rule 表示需要业务文档；mixed_business 表示数据库和业务文档都可能需要；"
        "不确定就选 unclear。不要调用工具，不要输出事实，不要编造项目或公司数据。"
        '只输出JSON：{"question_type":"...","needs_tool":true,"confidence":0.0}。'
    )
    return [
        {"role": "system", "content": instructions},
        {"role": "user", "content": question},
    ]


def task_from_proposal(question: str, proposal: RouteProposal) -> BusinessTask | None:
    if proposal.question_type == "general_knowledge" and not proposal.needs_tool:
        intent, route = "general_knowledge", "general"
    elif proposal.question_type == "business_fact":
        intent, route = "fact_query", "sql"
    elif proposal.question_type == "business_rule":
        intent, route = "document_rule", "rag"
    elif proposal.question_type == "mixed_business":
        intent, route = "cross_source_query", "cross_source"
    else:
        return None
    return BusinessTask(
        original_question=question,
        normalized_question=question,
        intent=intent,
        route=route,
        retrieval_question=question,
        source_roles=["general_knowledge"] if route == "general" else [],
    )
