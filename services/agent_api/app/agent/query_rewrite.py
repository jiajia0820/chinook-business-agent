"""QuestionIR 的单轮规范化和多轮增量改写。"""

from __future__ import annotations

from .semantic_ir import FieldProvenance, QuestionIR


def _merge(previous: QuestionIR, current: QuestionIR) -> QuestionIR:
    time_scope = current.time_scope
    if time_scope.year is None and time_scope.quarter is None and time_scope.month is None:
        time_scope = previous.time_scope.model_copy(deep=True)

    entities = current.entity_scope or previous.entity_scope
    exclusions = current.exclusions or previous.exclusions
    metrics = current.metrics or previous.metrics
    requested = list(dict.fromkeys([*previous.requested_outputs, *current.requested_outputs]))
    task_type = current.task_type if current.task_type != "unknown" else previous.task_type
    rewritten = previous.rewritten_question
    if current.requested_outputs:
        additions = "、".join(current.requested_outputs)
        rewritten = f"{rewritten}；本轮补充：{additions}"
    provenance = dict(previous.provenance)
    provenance.update(current.provenance)
    if current.time_scope.year is None and current.time_scope.quarter is None and current.time_scope.month is None:
        provenance["time_scope"] = FieldProvenance(source="session", status="confirmed", evidence=[previous.original_question])
    return current.model_copy(update={
        "rewritten_question": rewritten,
        "task_type": task_type,
        "metrics": metrics,
        "time_scope": time_scope,
        "entity_scope": entities,
        "exclusions": exclusions,
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
