"""QuestionIR 的单轮规范化和多轮增量改写。"""

from __future__ import annotations

from .semantic_ir import FieldProvenance, QuestionIR


def _has_time(scope) -> bool:
    return any(getattr(scope, name) is not None for name in ("year", "quarter", "month", "start", "end"))


def _merge_time(previous, current):
    """Merge explicit time fields without dropping confirmed siblings."""
    if not _has_time(current):
        return previous.model_copy(deep=True)
    values = previous.model_dump(mode="python")
    current_values = current.model_dump(mode="python")
    for key, value in current_values.items():
        if value is not None:
            values[key] = value
    # A new quarter/month changes the grain and therefore clears the other.
    if current.quarter is not None:
        values["month"] = None
    elif current.month is not None:
        values["quarter"] = None
    if values.get("year") is not None and values.get("quarter") is not None:
        values["label"] = f"{values['year']} Q{values['quarter']}"
    elif values.get("year") is not None and values.get("month") is not None:
        values["label"] = f"{values['year']}-{values['month']:02d}"
    elif values.get("year") is not None:
        values["label"] = f"{values['year']} 年"
    return type(previous).model_validate(values)


def _merge_field(previous, current, field: str):
    current_value = getattr(current, field)
    if current_value:
        return current_value
    return getattr(previous, field)


def _merge(previous: QuestionIR, current: QuestionIR) -> QuestionIR:
    time_scope = _merge_time(previous.time_scope, current.time_scope)

    entities = current.entity_scope or previous.entity_scope
    exclusions = current.exclusions or previous.exclusions
    metrics = current.metrics or previous.metrics
    media_scope = current.media_scope or previous.media_scope
    comparison_context = {**previous.comparison_context, **current.comparison_context}
    requested = list(dict.fromkeys([*previous.requested_outputs, *current.requested_outputs]))
    task_type = current.task_type if current.task_type != "unknown" else previous.task_type
    rewritten = previous.rewritten_question
    if current.rewritten_question and current.rewritten_question != current.original_question:
        rewritten = current.rewritten_question
    if current.requested_outputs:
        additions = "、".join(current.requested_outputs)
        rewritten = f"{rewritten}；本轮补充：{additions}"
    provenance = dict(previous.provenance)
    for key, value in current.provenance.items():
        if value.status != "unknown" or key not in provenance:
            provenance[key] = value
    if not _has_time(current.time_scope):
        provenance["time_scope"] = FieldProvenance(source="session", status="confirmed", evidence=[previous.original_question])
    return current.model_copy(update={
        "rewritten_question": rewritten,
        "task_type": task_type,
        "metrics": metrics,
        "time_scope": time_scope,
        "entity_scope": entities,
        "exclusions": exclusions,
        "media_scope": media_scope,
        "comparison_context": comparison_context,
        "requested_outputs": requested,
        "provenance": provenance,
    })


def rewrite_question(
    question: str,
    *,
    previous: QuestionIR | None = None,
    profile_id: str = "chinook-music",
) -> QuestionIR:
    """生成规范问题；有上一轮时只增量继承已确认字段。"""
    current = QuestionIR.from_question(question, profile_id=profile_id)
    return _merge(previous, current) if previous is not None else current


def merge_question_ir(previous: QuestionIR | None, current: QuestionIR) -> QuestionIR:
    """公开多轮合并入口，供状态适配层使用。"""
    return _merge(previous, current) if previous is not None else current
