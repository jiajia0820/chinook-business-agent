"""Validated context and JSON-only checkpoint channels (no model messages)."""

from typing import Literal, TypedDict
import uuid

from pydantic import Field, JsonValue

from ..contracts import Clarification, TimeRange
from ..tools.models import InternalModel, Question
from .semantic_ir import QuestionIR


class BusinessTask(InternalModel):
    original_question: Question
    normalized_question: Question
    intent: str
    route: Literal["general", "sql", "rag", "cross_source"]
    slots: dict[str, JsonValue] = Field(default_factory=dict)
    entities: list[dict[str, JsonValue]] = Field(default_factory=list)
    business_metric_ids: list[str] = Field(default_factory=list)
    time_range: TimeRange | None = None
    needs_calculation: bool = False
    information_needs: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    excluded_entities: list[dict[str, JsonValue]] = Field(default_factory=list)
    slot_provenance: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    retrieval_question: str = ""
    subtask_plan: list[dict[str, JsonValue]] = Field(default_factory=list)
    task_id: str = Field(default_factory=lambda: "business-" + str(uuid.uuid4()))
    task_version: int = 1
    source_roles: list[str] = Field(default_factory=list)
    answer_obligations: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    evidence_coverage: list[dict[str, JsonValue]] = Field(default_factory=list)
    time_issues: list[str] = Field(default_factory=list)
    # 本轮问题的不可丢失义务（过滤/粒度/排名/输出结构）。工具执行前写入，
    # 供 backend 覆盖检查判断模型候选或受控降级是否丢条件。旧 checkpoint 缺失时为空字典。
    query_obligations: dict[str, JsonValue] = Field(default_factory=dict)
    # New semantic IR is optional so old checkpoints and callers remain valid.
    question_ir: QuestionIR | None = None
    semantic_status: Literal["answerable", "clarification_required", "unsupported"] | None = None
    semantic_blockers: list[str] = Field(default_factory=list)


class SessionMemory(InternalModel):
    profile_id: str
    config_version: str
    pending: BusinessTask | None = None
    last_task: BusinessTask | None = None
    last_answer_summary: str | None = None
    pending_slots: list[str] = Field(default_factory=list)
    policy_version: str = "legacy"
    previous_view: BusinessTask | None = None
    active_task_id: str | None = None
    pending_clarification: dict[str, JsonValue] = Field(default_factory=dict)


def logical_copy(task):
    if task is None:
        return None
    copied = task.model_copy(deep=True)
    copied.evidence_coverage, copied.subtask_plan = [], []
    copied.slot_provenance = {k: {name: value for name, value in meta.items() if name not in {"source_ref", "source_refs"}} for k, meta in copied.slot_provenance.items()}
    return copied


class ParsedTurn(InternalModel):
    decision: Literal["ready", "clarification", "unsupported", "cancelled"]
    task: BusinessTask | None = None
    clarification: Clarification | None = None
    reason: str | None = None
    reused_context: bool = False
    rules: list[str] = Field(default_factory=list)
    blocking_slots: list[str] = Field(default_factory=list)


class AgentState(TypedDict, total=False):
    # Default replacement reducers are intentional. No append across turns.
    memory: dict
    request_id: str
    session_id: str
    profile_id: str
    request: dict
    parsed: dict
    status: str
    route: str
    tool_plan: list[str]
    sql_outcome: dict | None
    retrieval_outcome: dict | None
    retrieval_outcomes: list[dict]
    calculation_records: list[dict]
    calculations: list[dict]
    calculation_binding: dict | None
    calculation_evidence: dict | None
    calculation_audits: list[dict]
    evidence_refs: list[str]
    business_sources: dict | None
    business_audit: dict | None
    events: list[dict]
    node_path: list[str]
    limitations: list[str]
    error: dict | None
    response: dict | None
    turn_deadline: float
