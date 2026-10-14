"""Pure semantic gate for the shared :class:`QuestionIR`.

The gate deliberately runs before any SQL, retrieval, or calculation.  It
only answers the question ``can this IR proceed?`` and returns one of three
states: answerable, clarification_required, or unsupported.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..profiles import BusinessProfile, ProfileRegistry
from ..tools.models import InternalModel
from .semantic_ir import QuestionIR


class GateDecision(InternalModel):
    status: Literal["answerable", "clarification_required", "unsupported"]
    blockers: list[str] = Field(default_factory=list)
    reason: str = ""


_PERIOD_FIELDS = ("year", "quarter", "month", "start", "end")
_PERIOD_METRICS = {
    "sales_amount",
    "units_sold",
    "order_count",
    "purchasing_customers",
    "average_order_value",
    "genre_sales",
    "track_sales",
    "album_sales",
    "artist_sales",
    "media_type_sales",
    "billing_country_sales",
}


def _profile(profile: BusinessProfile | None) -> BusinessProfile:
    return profile or ProfileRegistry.defaults().get("chinook-music")


def _has_period(ir: QuestionIR) -> bool:
    return any(getattr(ir.time_scope, name) is not None for name in _PERIOD_FIELDS)


def _has_scope(ir: QuestionIR, question: str) -> bool:
    if ir.entity_scope:
        return True
    folded = question.casefold()
    return any(term in folded for term in ("全部音频", "全音频", "全部音乐", "全店", "全部品类"))


def _is_document_rule(ir: QuestionIR, question: str) -> bool:
    if ir.task_type in {"business_rule", "cross_source"}:
        return True
    if any(term in question for term in (
        "定义", "口径", "规则", "公式", "怎么计算", "如何计算", "手册",
        "方案", "建议", "重点看", "下一步", "目标表", "复盘",
    )):
        return True
    # Method questions may mention a transaction metric while asking for the
    # documented comparison rule rather than a database fact.
    return any(term in question for term in (
        "应该和什么时间段比较", "应该与什么时间段比较", "比较什么时间段",
        "时间段比较", "比较原则", "应该怎么处理", "如何处理", "范围差异", "分母", "客单价", "成交均价",
    ))


def _unsupported_reason(question: str, profile: BusinessProfile) -> str | None:
    folded = question.casefold()
    for metric, terms in profile.question_rules.unsupported_terms.items():
        if any(term.casefold() in folded for term in terms):
            from .task_policy import unsupported_metric_label
            return f"当前数据不支持{unsupported_metric_label(metric)}，不能用其他指标替代。"
    return None


def decide(task: QuestionIR, profile: BusinessProfile | None = None) -> GateDecision:
    """Decide whether a normalized question may proceed to evidence gathering.

    Decision order is intentional: data gaps win over document defaults;
    document rule questions are not made ambiguous merely because they have
    no SQL period; target comparisons use their own three-slot contract.
    """

    active = _profile(profile)
    question = task.original_question.strip()

    # 1. Explicit data gaps are hard refusals and must win over all defaults.
    unsupported = _unsupported_reason(question, active)
    if unsupported:
        return GateDecision(status="unsupported", reason=unsupported)

    # 2. An IR conflict cannot be resolved by silently choosing one value.
    conflicts = [
        name for name, provenance in task.provenance.items()
        if provenance.status == "conflict"
    ]
    if conflicts:
        return GateDecision(
            status="clarification_required",
            blockers=conflicts,
            reason="已确认条件存在冲突，请明确更正。",
        )

    # 3. Target comparisons have a deliberately narrow public contract.
    target_requested = "target" in task.requested_outputs or any(
        term in question for term in ("达标", "达到目标", "目标了吗", "完成目标", "和目标比")
    )
    if target_requested:
        blockers: list[str] = []
        if not _has_period(task):
            blockers.append("period")
        if not _has_scope(task, question):
            blockers.append("scope")
        # QuestionIR does not infer a target metric from a similarly named
        # actual metric.  This keeps a bare target question explicit.
        blockers.append("target_metric")
        return GateDecision(
            status="clarification_required",
            blockers=list(dict.fromkeys(blockers)),
            reason="目标比较需要明确周期、范围和目标指标。",
        ) if blockers else GateDecision(status="answerable")

    # 4. Rule/definition questions are answerable from authoritative docs;
    # they do not need a transaction period or SQL scope.  This check follows
    # the target branch so "和目标比" still receives target blockers.
    if _is_document_rule(task, question):
        return GateDecision(status="answerable")

    # 5. Transaction metrics require a period; document-only rules already
    # exited above.  Scope uses the profile's declared default when absent.
    if task.metrics and any(metric in _PERIOD_METRICS for metric in task.metrics) and not _has_period(task):
        return GateDecision(
            status="clarification_required",
            blockers=["period"],
            reason="交易指标需要明确观察周期。",
        )

    # Unknown free-form questions need an intent before tools can be chosen.
    if task.task_type == "unknown" and not task.metrics and not task.requested_outputs:
        return GateDecision(status="clarification_required", blockers=["intent"], reason="请明确要查询的指标或业务规则。")

    return GateDecision(status="answerable")

