"""Pydantic models for interface-contract.md v0.3.

The models intentionally preserve profile-defined fields as JSON values instead
of hard-coding Chinook-specific entities, metrics, or document metadata.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContractModel(BaseModel):
    """Strict base model used to detect accidental contract drift."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class AskStatus(str, Enum):
    ANSWERED = "answered"
    CLARIFICATION_REQUIRED = "clarification_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class Route(str, Enum):
    SQL = "sql"
    RAG = "rag"
    CROSS_SOURCE = "cross_source"
    CLARIFICATION = "clarification"
    UNSUPPORTED = "unsupported"


class SqlExecutionStatus(str, Enum):
    SUCCESS = "success"
    REJECTED = "rejected"
    FAILED = "failed"


class RetrievalStatus(str, Enum):
    """Retrieval status.

    v0.3 shows ``success`` and exposes an error field. ``failed`` is retained as
    the minimal failure counterpart until the contract defines more states.
    """

    SUCCESS = "success"
    FAILED = "failed"


class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
    MISSING_REQUIRED_SLOT = "MISSING_REQUIRED_SLOT"
    SCHEMA_LINKING_FAILED = "SCHEMA_LINKING_FAILED"
    SQL_READ_ONLY_VIOLATION = "SQL_READ_ONLY_VIOLATION"
    SQL_EXECUTION_FAILED = "SQL_EXECUTION_FAILED"
    RAG_NO_EVIDENCE = "RAG_NO_EVIDENCE"
    DOCUMENT_PROCESSING_FAILED = "DOCUMENT_PROCESSING_FAILED"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AskOptions(ContractModel):
    show_trace: bool = True
    max_rows: int = Field(default=50, ge=1, le=200)
    top_k: int = Field(default=5, ge=1, le=10)


class AskRequest(ContractModel):
    question: NonEmptyString
    profile_id: NonEmptyString
    session_id: NonEmptyString | None = None
    user_role: NonEmptyString | None = None
    options: AskOptions = Field(default_factory=AskOptions)


class Intent(ContractModel):
    name: NonEmptyString
    confidence: float | None = Field(default=None, ge=0, le=1)


class TimeRange(ContractModel):
    start: NonEmptyString
    end: NonEmptyString
    end_inclusive: bool
    label: NonEmptyString


class MissingSlot(ContractModel):
    name: NonEmptyString
    description: NonEmptyString


class Clarification(ContractModel):
    question: NonEmptyString
    missing_slots: list[MissingSlot] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)


class ApiError(ContractModel):
    # v0.3 lists recommended error codes rather than a closed enum. Keep the
    # constants above for producers while accepting future compatible codes.
    code: NonEmptyString
    message: NonEmptyString
    retryable: bool
    details: JsonValue | None = None


class SqlQueryRequest(ContractModel):
    query_id: NonEmptyString
    profile_id: NonEmptyString
    sql: NonEmptyString
    params: dict[str, JsonValue] = Field(default_factory=dict)
    max_rows: int = Field(default=50, ge=1, le=200)
    timeout_ms: int = Field(default=5000, ge=1)


class SqlSource(ContractModel):
    type: NonEmptyString
    name: NonEmptyString
    tables: list[str] = Field(default_factory=list)


class SqlQueryResponse(ContractModel):
    query_id: NonEmptyString
    profile_id: NonEmptyString
    status: SqlExecutionStatus
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, JsonValue]] = Field(default_factory=list)
    row_count: int = Field(ge=0)
    truncated: bool
    execution_ms: int = Field(ge=0)
    source: SqlSource
    sql: NonEmptyString | None = None
    params: dict[str, JsonValue] | None = None
    error: ApiError | None = None

    @model_validator(mode="after")
    def validate_error_state(self) -> SqlQueryResponse:
        if self.status == SqlExecutionStatus.SUCCESS and self.error is not None:
            raise ValueError("successful SQL responses cannot contain an error")
        if self.status != SqlExecutionStatus.SUCCESS and self.error is None:
            raise ValueError("rejected or failed SQL responses must contain an error")
        return self


class RetrievalRequest(ContractModel):
    retrieval_id: NonEmptyString
    profile_id: NonEmptyString
    query: NonEmptyString
    filters: dict[str, JsonValue] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=10)


class DocumentChunk(ContractModel):
    chunk_id: NonEmptyString
    doc_id: NonEmptyString
    title: NonEmptyString
    doc_type: NonEmptyString
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    text: NonEmptyString
    score: float | None = Field(default=None, ge=0)
    source_uri: NonEmptyString
    retrieval_id: NonEmptyString | None = None
    bbox: JsonValue | None = None


class RetrievalResponse(ContractModel):
    retrieval_id: NonEmptyString
    profile_id: NonEmptyString
    status: RetrievalStatus
    chunks: list[DocumentChunk] = Field(default_factory=list)
    error: ApiError | None = None

    @model_validator(mode="after")
    def validate_error_state(self) -> RetrievalResponse:
        if self.status == RetrievalStatus.SUCCESS and self.error is not None:
            raise ValueError("successful retrieval responses cannot contain an error")
        if self.status == RetrievalStatus.FAILED and self.error is None:
            raise ValueError("failed retrieval responses must contain an error")
        return self


class Calculation(ContractModel):
    calculation_id: NonEmptyString
    formula: NonEmptyString
    inputs: list[str] = Field(default_factory=list)
    result: int | float
    unit: NonEmptyString


class MetricDefinition(ContractModel):
    metric_id: NonEmptyString
    name: NonEmptyString
    definition: NonEmptyString
    unit: NonEmptyString
    source_refs: list[str] = Field(default_factory=list)


class TraceStep(ContractModel):
    step: int = Field(ge=1)
    tool: NonEmptyString
    status: NonEmptyString
    source_refs: list[str] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)


class AskResponse(ContractModel):
    request_id: NonEmptyString
    session_id: NonEmptyString
    profile_id: NonEmptyString
    status: AskStatus
    answer: str | None
    route: Route
    intent: Intent
    entities: list[dict[str, JsonValue]] = Field(default_factory=list)
    time_range: TimeRange | None
    sql_results: list[SqlQueryResponse] = Field(default_factory=list)
    documents: list[DocumentChunk] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    metric_definitions: list[MetricDefinition] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    clarification: Clarification | None
    trace: list[TraceStep] = Field(default_factory=list)
    error: ApiError | None

    @model_validator(mode="after")
    def validate_business_state(self) -> AskResponse:
        if self.status == AskStatus.ANSWERED and not self.answer:
            raise ValueError("answered responses must contain an answer")

        if self.status == AskStatus.CLARIFICATION_REQUIRED:
            if self.route != Route.CLARIFICATION or self.clarification is None:
                raise ValueError(
                    "clarification_required responses need the clarification route and payload"
                )

        if self.status == AskStatus.UNSUPPORTED and self.route != Route.UNSUPPORTED:
            raise ValueError("unsupported responses must use the unsupported route")

        if self.status == AskStatus.ERROR and self.error is None:
            raise ValueError("error responses must contain a structured error")

        if self.status != AskStatus.ERROR and self.error is not None:
            raise ValueError("non-error responses cannot contain an error")

        return self


def response_json_schema() -> dict[str, Any]:
    """Return the generated response schema for later OpenAPI integration."""

    return AskResponse.model_json_schema()
