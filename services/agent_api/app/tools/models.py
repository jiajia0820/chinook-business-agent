"""Validated application state, separate from model messages and API payloads."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from ..contracts import ApiError, MetricDefinition, SqlQueryResponse, TimeRange, TraceStep
from ..contracts.models import NonEmptyString


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, validate_default=True)


Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Capability = Literal["sql", "rag", "calculate"]
ToolStatus = Literal["success", "failed", "rejected", "unsupported", "blocked", "timeout", "cancelled"]


class ToolFailure(Exception):
    """An intentional, already sanitized tool error; not an arbitrary exception."""

    def __init__(self, error: ApiError):
        super().__init__(error.message)
        self.error = error


def check_reference_time(value: str | None) -> str | None:
    if value is not None:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("reference_time must include a timezone")
    return value


class BusinessContextItem(InternalModel):
    ref_id: NonEmptyString
    text: Question


class SqlTaskRequest(InternalModel):
    query_id: NonEmptyString  # Logical B task ID; C generates its own SQL query IDs.
    profile_id: NonEmptyString
    question: Question
    normalized_question: Question | None = None
    entities: list[dict[str, JsonValue]] = Field(default_factory=list)
    time_range: TimeRange | None = None
    resolved_slots: dict[str, JsonValue] = Field(default_factory=dict)
    metric_ids: list[NonEmptyString] = Field(default_factory=list)
    business_context: list[BusinessContextItem] = Field(default_factory=list, max_length=20)
    reference_time: str | None = None
    max_rows: int = Field(default=50, ge=1, le=200)
    show_trace: bool = True
    timeout_ms: int = Field(default=60000, ge=1, le=60000)

    @model_validator(mode="after")
    def validate_context(self) -> SqlTaskRequest:
        check_reference_time(self.reference_time)
        if len(self.metric_ids) != len(set(self.metric_ids)):
            raise ValueError("metric_ids cannot contain duplicates")
        refs = [item.ref_id for item in self.business_context]
        if len(refs) != len(set(refs)):
            raise ValueError("business_context references must be unique")
        return self


class SqlTaskOutcome(InternalModel):
    query_id: NonEmptyString
    profile_id: NonEmptyString
    status: Literal["success", "failed", "rejected", "unsupported"]
    sql_results: list[SqlQueryResponse] = Field(default_factory=list)
    metric_definitions: list[MetricDefinition] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    source_refs: list[NonEmptyString] = Field(default_factory=list)
    error: ApiError | None = None
    diagnostics: dict[str, JsonValue] = Field(default_factory=dict, repr=False)

    @model_validator(mode="after")
    def validate_state(self) -> SqlTaskOutcome:
        if any(result.profile_id != self.profile_id for result in self.sql_results):
            raise ValueError("SQL result profile does not match task profile")
        ids = [result.query_id for result in self.sql_results]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate SQL query IDs")
        if self.status == "success":
            if self.error or not self.sql_results or any(r.status != "success" for r in self.sql_results):
                raise ValueError("successful task requires successful SQL results and no error")
        elif self.error is None:
            raise ValueError("non-successful task must retain an internal structured error")
        if not set(self.source_refs) <= set(ids):
            raise ValueError("task source_refs must identify returned SQL results")
        return self


class CalculationOperand(InternalModel):
    # Decimal parses numeric text deliberately; floats should be converted from
    # their documented source representation by the evidence-binding layer.
    value: Decimal = Field(strict=False)
    unit: NonEmptyString
    source_ref: NonEmptyString


class CalculationRequest(InternalModel):
    calculation_id: NonEmptyString
    profile_id: NonEmptyString
    function: Literal["difference", "attainment_rate", "growth_rate"]
    formula_ref: NonEmptyString
    operands: dict[str, CalculationOperand] = Field(min_length=1)
    result_unit: NonEmptyString
