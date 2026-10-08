"""Validated context and JSON-only checkpoint channels (no model messages)."""

from typing import Literal, TypedDict

from pydantic import Field, JsonValue

from ..contracts import Clarification, TimeRange
from ..tools.models import InternalModel, Question


class BusinessTask(InternalModel):
    original_question: Question
    normalized_question: Question
    intent: str
    route: Literal["sql", "rag", "cross_source"]
    slots: dict[str, JsonValue] = Field(default_factory=dict)
    entities: list[dict[str, JsonValue]] = Field(default_factory=list)
    business_metric_ids: list[str] = Field(default_factory=list)
    time_range: TimeRange | None = None
    needs_calculation: bool = False


class SessionMemory(InternalModel):
    profile_id: str
    config_version: str
    pending: BusinessTask | None = None
    last_task: BusinessTask | None = None
    last_answer_summary: str | None = None


class ParsedTurn(InternalModel):
    decision: Literal["ready", "clarification", "unsupported", "cancelled"]
    task: BusinessTask | None = None
    clarification: Clarification | None = None
    reason: str | None = None
    reused_context: bool = False
    rules: list[str] = Field(default_factory=list)


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
    calculations: list[dict]
    calculation_binding: dict | None
    calculation_evidence: dict | None
    evidence_refs: list[str]
    events: list[dict]
    node_path: list[str]
    limitations: list[str]
    error: dict | None
    response: dict | None
