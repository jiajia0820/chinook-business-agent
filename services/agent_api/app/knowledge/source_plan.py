"""Authoritative document planning for business RAG questions.

The planner is deliberately pure: it classifies evidence roles and returns the
smallest set of approved knowledge documents needed to answer a question.
"""

from __future__ import annotations

import re
from typing import Iterable

from pydantic import Field

from ..tools.models import InternalModel


# The tuple order is the retrieval priority.  D12 is a fallback/default source;
# the other documents are primary for the role they own.
SOURCE_ROLES: dict[str, tuple[str, ...]] = {
    "metric_definition": ("D01", "D02"),
    "media_scope": ("D01", "D03", "D12"),
    "classification_boundary": ("D03",),
    "selection_rule": ("D05",),
    "activity_plan": ("D10", "D11"),
    "mixed_order_media_rule": ("D12",),
    "target_value": ("D06",),
    "quarter_review": ("D07", "D08"),
    "operation_rule": ("D09",),
    # D04 is retained for the existing customer segmentation workflow.  It is
    # not part of the compact public role table above, but remains authoritative
    # when that workflow is explicitly requested.
    "customer_segmentation": ("D04",),
}

ROLE_TERMS: dict[str, tuple[str, ...]] = {
    "mixed_order_media_rule": ("同一张订单", "同一订单", "同时有音频和视频", "音频和视频时", "音乐销售额应该怎么处理"),
    "media_scope": ("mediatype", "媒体格式", "音频范围", "音乐范围", "默认音乐", "范围差异", "包括哪些"),
    "classification_boundary": ("分类", "归类", "genre", "重金属", "heavy metal", "摇滚", "rock and roll", "拉丁"),
    "selection_rule": ("选品", "候选", "商品推荐", "推荐商品", "trackid"),
    "activity_plan": ("活动方案", "活动安排", "活动什么时候", "海报", "报名", "分别怎么安排", "主页面", "延伸栏目"),
    "target_value": ("目标", "达标", "达成率", "差多少", "差额", "计划"),
    "quarter_review": ("复盘", "经营复盘", "核心结果", "比较", "对比", "变化"),
    "operation_rule": ("运营", "运营手册", "手册", "建议", "重点看", "下一步", "怎么运营"),
    "metric_definition": ("指标定义", "指标口径", "销售额口径", "定义", "口径", "公式", "怎么计算", "如何计算", "客单价", "成交均价"),
    "customer_segmentation": ("客户分层", "高价值客户", "a层", "b层", "c层"),
}

ROLE_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "metric_definition": ("指标定义", "口径", "公式"),
    "media_scope": ("媒体格式", "音频范围", "音乐商品", "默认音乐范围"),
    "classification_boundary": ("分类边界", "Genre", "别名", "单独统计"),
    "selection_rule": ("选品规则", "候选筛选", "商品推荐"),
    "activity_plan": ("活动方案", "活动安排", "报名", "海报"),
    "mixed_order_media_rule": ("同一订单", "音频", "视频", "音乐销售额"),
    "target_value": ("目标值", "目标表", "达成率"),
    "quarter_review": ("经营复盘", "季度核心结果", "销售额", "订单数", "购买客户数"),
    "operation_rule": ("品类运营", "运营动作", "重点观察", "建议"),
    "customer_segmentation": ("客户分层", "层级阈值"),
}


class SourcePlan(InternalModel):
    required_docs: list[str] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)
    preferred_doc: str | None = None
    query_terms: list[str] = Field(default_factory=list)
    min_evidence_count: int = 0


def _contains(question: str, terms: Iterable[str]) -> bool:
    folded = question.casefold()
    return any(term.casefold() in folded for term in terms)


def _explicit_docs(question: str) -> list[str]:
    return list(dict.fromkeys(doc.upper() for doc in re.findall(r"(?<![A-Za-z0-9])D(?:0[1-9]|1[0-2])(?![A-Za-z0-9])", question, re.I)))


def _roles_for(question: str) -> list[str]:
    q = question.strip()
    roles: list[str] = []
    # Specific rules must win over broad terms such as “包括哪些”.
    if _contains(q, ROLE_TERMS["mixed_order_media_rule"]):
        roles.append("mixed_order_media_rule")
    else:
        if _contains(q, ROLE_TERMS["media_scope"]):
            roles.append("media_scope")
    if _contains(q, ROLE_TERMS["classification_boundary"]):
        roles.append("classification_boundary")
    if _contains(q, ROLE_TERMS["selection_rule"]):
        roles.append("selection_rule")
    if _contains(q, ROLE_TERMS["activity_plan"]):
        roles.append("activity_plan")
    if _contains(q, ROLE_TERMS["quarter_review"]):
        roles.append("quarter_review")
    if _contains(q, ROLE_TERMS["target_value"]):
        roles.append("target_value")
    if _contains(q, ROLE_TERMS["operation_rule"]):
        roles.append("operation_rule")
    if _contains(q, ROLE_TERMS["customer_segmentation"]):
        roles.append("customer_segmentation")
    if _contains(q, ROLE_TERMS["metric_definition"]):
        roles.append("metric_definition")
    # “第四季度 Rock、Metal 和 Latin 分别怎么安排” is an activity plan even
    # without the literal word 活动.
    if not roles and "分别怎么安排" in q and any(term in q.casefold() for term in ("rock", "metal", "latin", "摇滚", "金属", "拉丁")):
        roles.append("activity_plan")
    return roles


def rag_source_plan(question: str, *, explicit_docs: Iterable[str] | None = None) -> SourcePlan:
    """Return the minimal role-based source contract for a RAG question."""
    q = str(question or "").strip()
    explicit = [item.upper() for item in explicit_docs] if explicit_docs is not None else _explicit_docs(q)
    roles = _roles_for(q)
    docs: list[str] = []
    for doc in explicit:
        if doc not in docs:
            docs.append(doc)
    for role in roles:
        for doc in SOURCE_ROLES[role]:
            if doc not in docs:
                docs.append(doc)
    terms: list[str] = []
    for role in roles:
        for term in ROLE_QUERY_TERMS.get(role, ()):
            if term not in terms:
                terms.append(term)
    if not terms and q:
        terms.append(q)
    return SourcePlan(
        required_docs=docs,
        required_roles=roles,
        preferred_doc=docs[0] if docs else None,
        query_terms=terms,
        min_evidence_count=len(docs),
    )


__all__ = ["SOURCE_ROLES", "SourcePlan", "rag_source_plan"]
