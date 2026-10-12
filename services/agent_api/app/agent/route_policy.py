"""Deterministic route policy shared by semantic IR and task parsing.

The policy only classifies the question.  It never calls SQL, retrieval, or
calculation tools, so callers can use it as the single route decision point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import re

if TYPE_CHECKING:  # pragma: no cover - imported lazily at runtime
    from .semantic_ir import QuestionIR


FACT_MARKERS = (
    "多少", "列出", "统计", "排名", "按月份", "按月", "按国家", "按媒体类型",
    "按品类", "按类型", "显示为0", "显示为 0", "没有成交", "订单去重",
    "最高", "排名", "前五", "前三", "Top", "购买", "成交", "收录", "所有媒体类型",
    "销售额是多少", "销量是多少", "订单数是多少", "客户数是多少", "实际销售额",
)
RULE_MARKERS = (
    "怎么处理", "如何处理", "口径", "定义", "规则", "应该", "规定", "手册",
    "方案", "计划", "如何解释", "怎么计算", "如何计算", "建议", "运营", "按", "包括哪些", "包含哪些", "媒体格式",
)
CROSS_MARKERS = ("目标", "达标", "未达", "复盘", "计划", "活动方案", "运营建议", "运营手册", "下一步")
OPERATION_MARKERS = ("活动方案", "运营建议", "运营手册", "下一步", "放在什么位置", "重点看", "建议")


def classify_question(question: str, metrics: list[str] | None = None) -> tuple[str, str, list[str]]:
    """Return ``(task_type, route, requested_outputs)`` for one question."""
    q = question.strip()
    metrics = metrics or []
    actual_markers = tuple(marker for marker in FACT_MARKERS if marker not in {
        "多少", "按月份", "按月", "按国家", "按媒体类型", "按品类", "按类型",
    })
    has_actual = "实际" in q or any(marker in q for marker in actual_markers)
    has_period = bool(re.search(r"\d{4}\s*年|第?[一二三四1-4]季度|Q[1-4]|按月份|按月", q, re.I))
    has_fact_shape = bool(metrics) and (any(marker in q for marker in FACT_MARKERS) or has_period)
    has_rule = any(marker in q for marker in RULE_MARKERS)
    has_target_or_operation = any(marker in q for marker in CROSS_MARKERS)
    has_operation = any(marker in q for marker in OPERATION_MARKERS)

    # Cross-source wins when the question explicitly combines an actual value
    # with a target, review, or operating action.
    if (has_actual and has_target_or_operation) or ("未达目标" in q and has_operation):
        outputs = ["actual", "target"]
        if has_operation:
            outputs.append("operation")
        if "复盘" in q:
            outputs.append("review")
        return "cross_source", "cross_source", outputs

    # A target/plan lookup without an actual database fact is document-only.
    if has_target_or_operation and not has_actual:
        return "document_rule", "rag", ["rule"]

    # Rule wording wins over a bare metric mention.  A question such as
    # “销售额应该怎么处理” asks for a documented policy, not a number.
    if has_rule and not has_fact_shape and not ("实际" in q and any(marker in q for marker in FACT_MARKERS)):
        return "document_rule", "rag", ["rule"]

    if has_fact_shape or has_actual:
        outputs = ["actual"]
        if has_period or any(marker in q for marker in ("季度", "期间")):
            outputs.append("monthly_or_period_result")
        if "没有成交" in q or "显示为0" in q or "显示为 0" in q:
            outputs.append("include_zero")
        return "database_fact", "sql", outputs

    if has_rule:
        return "document_rule", "rag", ["rule"]
    if metrics:
        return "database_fact", "sql", ["actual"]
    return "unknown", "rag", []


def route_question(question: str | "QuestionIR", profile_id: str = "chinook-music") -> "QuestionIR":
    """Convert a question into a routed :class:`QuestionIR` without tools."""
    from .semantic_ir import QuestionIR

    ir = question if isinstance(question, QuestionIR) else QuestionIR.from_question(question, profile_id=profile_id)
    task_type, route, outputs = classify_question(ir.original_question, ir.metrics)
    ir.task_type = task_type
    ir.route = route
    ir.requested_outputs = list(dict.fromkeys([*ir.requested_outputs, *outputs]))
    return ir

