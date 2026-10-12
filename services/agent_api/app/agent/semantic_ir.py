"""共享的、可序列化的问题语义中间表示。

这一层只做问题结构化，不执行 SQL、检索或计算。旧的 ``BusinessTask``
仍然是运行时主契约；``QuestionIR`` 作为兼容字段挂在任务上，供后续
SQL、RAG 和跨源阶段逐步迁移。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, JsonValue

from ..tools.models import InternalModel, Question


class FieldProvenance(InternalModel):
    source: Literal["user", "profile_default", "inferred", "session", "document"]
    status: Literal["confirmed", "defaulted", "inferred", "unknown", "conflict"]
    evidence: list[str] = Field(default_factory=list)


class EntityMention(InternalModel):
    surface: str
    entity_type: str
    canonical_name: str
    entity_id: int | str
    included: bool = True
    boundary_status: Literal["confirmed", "ambiguous", "separate"] = "confirmed"
    provenance: FieldProvenance


class TimeScope(InternalModel):
    year: int | None = None
    quarter: int | None = None
    month: int | None = None
    start: str | None = None
    end: str | None = None
    label: str | None = None


def _period(question: str) -> TimeScope:
    """Parse only explicit year/quarter/month values.

    Entity identifiers are masked first so ``TrackId 1791`` never becomes a
    time value. Relative dates are intentionally left unknown.
    """
    text = re.sub(
        r"(?i)(?:TrackId|CustomerId|GenreId|PlaylistId)\s*[:：=]?\s*\d+(?:\s*[-–至]\s*\d+)?",
        " ",
        question,
    )
    years = re.findall(r"(?<![A-Za-z\d])(\d{4})(?!\d)", text)
    quarter_matches = re.findall(r"(?:第?([一二三四1-4])季度|Q([1-4]))", text, re.I)
    quarters = [int(a or b) if (a or b).isdigit() else "一二三四".index(a) + 1 for a, b in quarter_matches]
    months = re.findall(r"(?<!\d)(1[0-2]|[1-9])\s*月", text)
    year = int(years[0]) if years else None
    quarter = quarters[0] if quarters else None
    month = int(months[0]) if months else None
    label = None
    if year is not None and quarter is not None:
        label = f"{year} Q{quarter}"
    elif year is not None and month is not None:
        label = f"{year}-{month:02d}"
    elif year is not None:
        label = f"{year} 年"
    return TimeScope(year=year, quarter=quarter, month=month, label=label)


def _metric_ids(question: str, profile_id: str) -> list[str]:
    try:
        from ..profiles import ProfileRegistry

        profile = ProfileRegistry.defaults().get(profile_id)
        terms = profile.question_rules.metric_terms
    except (KeyError, ValueError):
        terms = {
            "sales_amount": ["销售额", "销售金额", "营收"],
            "units_sold": ["销量", "销售件数", "销售量"],
            "order_count": ["订单数", "订单数量", "订单量"],
            "purchasing_customers": ["购买客户数", "购买人数"],
        }
    found: list[str] = []
    folded = question.casefold()
    for metric_id, aliases in terms.items():
        if any(alias.casefold() in folded for alias in aliases) and metric_id not in found:
            found.append(metric_id)
    return found


def _task_type(question: str, metrics: list[str]) -> str:
    if any(term in question for term in ("按", "除以", "分母", "还是")) and any(
        term in question for term in ("客单价", "销售额", "销量", "订单数")
    ):
        return "business_rule"
    if any(term in question for term in ("下一步", "重点看", "建议关注", "运营", "运营手册")):
        return "business_rule"
    if any(term in question for term in ("结合文档", "和目标比", "目标", "实际")):
        return "cross_source" if any(term in question for term in ("实际", "结合", "和目标比")) else "business_rule"
    if any(term in question for term in ("定义", "口径", "规则", "公式", "怎么计算")):
        return "business_rule"
    if metrics:
        return "business_fact"
    return "unknown"


class QuestionIR(InternalModel):
    original_question: Question
    rewritten_question: Question
    task_type: str
    route: Literal["general", "sql", "rag", "cross_source"] = "sql"
    metrics: list[str] = Field(default_factory=list)
    time_scope: TimeScope = Field(default_factory=TimeScope)
    entity_scope: list[EntityMention] = Field(default_factory=list)
    exclusions: list[EntityMention] = Field(default_factory=list)
    media_scope: list[int] = Field(default_factory=list)
    comparison_context: dict[str, JsonValue] = Field(default_factory=dict)
    requested_outputs: list[str] = Field(default_factory=list)
    clarification_blockers: list[str] = Field(default_factory=list)
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict)

    @classmethod
    def from_question(cls, question: str, profile_id: str = "chinook-music") -> "QuestionIR":
        """从显式问题条件生成初始 IR，不调用任何外部工具。"""
        from .entity_linking import link_entities

        cleaned = question.strip()
        linked = link_entities(cleaned, profile_id=profile_id)
        time_scope = _period(cleaned)
        metrics = _metric_ids(cleaned, profile_id)
        rewritten = cleaned
        mentions = [*linked.entities, *linked.excluded_mentions]
        for mention in sorted(mentions, key=lambda item: len(item.surface), reverse=True):
            rewritten = rewritten.replace(mention.surface, mention.canonical_name)

        # RoutePolicy is the single source of truth for task type, route, and
        # requested outputs.  Keep a lazy import here to avoid a module cycle
        # because route_policy accepts QuestionIR as input.
        from .route_policy import classify_question

        _policy_type, route, requested = classify_question(cleaned, metrics)
        # Keep the historical semantic labels on the raw IR (notably
        # ``business_rule`` for formula/definition questions).  route_question
        # upgrades this to the explicit policy labels when callers ask for a
        # routed IR.
        task_type = _task_type(cleaned, metrics)
        # Preserve the historical output vocabulary while adding the explicit
        # route-policy roles.  This keeps multi-turn target/growth/advice
        # obligations available to downstream planners.
        legacy_requested: list[str] = []
        if any(term in cleaned for term in ("目标", "达标", "和目标比")):
            legacy_requested.append("target")
        if any(term in cleaned for term in ("增长", "环比", "同比", "变化")):
            legacy_requested.append("growth_rate")
        if any(term in cleaned for term in ("建议", "重点", "怎么运营", "运营手册", "下一步")):
            legacy_requested.append("operation_advice")
        requested = list(dict.fromkeys([*legacy_requested, *requested]))

        provenance = {
            "time_scope": FieldProvenance(
                source="user" if any(value is not None for value in (time_scope.year, time_scope.quarter, time_scope.month)) else "inferred",
                status="confirmed" if any(value is not None for value in (time_scope.year, time_scope.quarter, time_scope.month)) else "unknown",
                evidence=[cleaned] if time_scope.year is not None or time_scope.quarter is not None or time_scope.month is not None else [],
            ),
            "metrics": FieldProvenance(source="user", status="confirmed" if metrics else "unknown", evidence=metrics),
            "entity_scope": FieldProvenance(source="user", status="confirmed" if linked.entities else "unknown", evidence=[item.surface for item in linked.entities]),
        }
        return cls(
            original_question=cleaned,
            rewritten_question=rewritten,
            task_type=task_type,
            route=route,
            metrics=metrics,
            time_scope=time_scope,
            entity_scope=linked.entities,
            exclusions=linked.excluded_mentions,
            requested_outputs=requested,
            provenance=provenance,
        )
