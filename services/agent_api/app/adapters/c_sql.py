"""Map the reviewed C draft to internal B results, keeping the public API stable."""

from __future__ import annotations

import math
import re
from typing import Literal
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field, JsonValue, ValidationError, model_validator

from ..contracts import ApiError, MetricDefinition, SqlQueryResponse, TraceStep
from ..contracts.models import NonEmptyString
from ..profiles import BusinessProfile, ProfileRegistry
from ..tools.models import BusinessContextItem, InternalModel, Question, SqlTaskOutcome, SqlTaskRequest, ToolFailure, check_reference_time


class CMappingError(ToolFailure):
    def __init__(self, code: str, message: str):
        super().__init__(ApiError(code=code, message=message, retryable=False))


def _finite_tree(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON value")
    if isinstance(value, dict):
        for item in value.values():
            _finite_tree(item)
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)


class COptions(InternalModel):
    max_rows: int = Field(default=50, ge=1, le=200)
    show_trace: bool = True


class CContext(InternalModel):
    resolved_slots: dict[str, JsonValue] = Field(default_factory=dict)
    metric_ids: list[NonEmptyString] = Field(default_factory=list)
    business_context: list[BusinessContextItem] = Field(default_factory=list, max_length=20)
    reference_time: str | None = None

    @model_validator(mode="after")
    def validate_time(self) -> CContext:
        check_reference_time(self.reference_time)
        _finite_tree(self.resolved_slots)
        return self


class CRequest(InternalModel):
    request_id: NonEmptyString
    profile_id: NonEmptyString
    question: Question
    context: CContext = Field(default_factory=CContext)
    options: COptions = Field(default_factory=COptions)

    def payload(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)


class CError(InternalModel):
    code: NonEmptyString
    message: NonEmptyString
    retryable: bool
    details: JsonValue | None = None


class CSource(InternalModel):
    type: NonEmptyString
    name: str | None = None
    tables: list[NonEmptyString] = Field(default_factory=list)


class CSqlResult(InternalModel):
    query_id: NonEmptyString
    profile_id: NonEmptyString
    status: Literal["success", "rejected", "failed"]
    columns: list[NonEmptyString]
    rows: list[dict[str, JsonValue]]
    row_count: int = Field(ge=0)
    truncated: bool
    execution_ms: int | float = Field(ge=0)
    source: CSource
    error: CError | None = None

    @model_validator(mode="after")
    def validate_result(self) -> CSqlResult:
        if self.row_count != len(self.rows) or len(self.columns) != len(set(self.columns)):
            raise ValueError("row count or column identity is inconsistent")
        if any(set(row) != set(self.columns) for row in self.rows):
            raise ValueError("row columns do not match declared columns")
        if (self.status == "success") != (self.error is None):
            raise ValueError("SQL status and error are inconsistent")
        _finite_tree(self.rows)
        return self


class CQueryArtifact(InternalModel):
    query_id: NonEmptyString
    sql: NonEmptyString
    params: dict[str, JsonValue]
    selected_tables: list[NonEmptyString]
    schema_version: NonEmptyString
    metric_refs: list[NonEmptyString]
    attempt_count: int = Field(ge=1)


class CMetricDefinition(InternalModel):
    metric_id: NonEmptyString
    name: NonEmptyString
    definition: NonEmptyString
    unit: str | None = None
    source_refs: list[NonEmptyString]


class CTrace(InternalModel):
    step: int = Field(ge=1)
    tool: NonEmptyString
    status: NonEmptyString
    source_refs: list[NonEmptyString]
    summary: str | None = None
    duration_ms: int | float = Field(ge=0)


class CDraftResponse(InternalModel):
    request_id: NonEmptyString
    profile_id: NonEmptyString
    status: Literal["answered", "error", "unsupported"]
    sql_results: list[CSqlResult] = Field(default_factory=list)
    query_artifacts: list[CQueryArtifact] = Field(default_factory=list)
    metric_definitions: list[CMetricDefinition] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    clarification: None = None  # Current C does not own dialogue clarification.
    trace: list[CTrace] = Field(default_factory=list)
    error: CError | None = None

    @model_validator(mode="after")
    def validate_state(self) -> CDraftResponse:
        _finite_tree(self.model_dump(mode="python"))
        query_ids = [result.query_id for result in self.sql_results]
        artifact_ids = [artifact.query_id for artifact in self.query_artifacts]
        metric_ids = [metric.metric_id for metric in self.metric_definitions]
        if any(len(ids) != len(set(ids)) for ids in (query_ids, artifact_ids, metric_ids)):
            raise ValueError("duplicate result, artifact or metric IDs")
        if not set(artifact_ids) <= set(query_ids):
            raise ValueError("orphan query artifact")
        if self.status == "answered":
            if self.error or not self.sql_results or any(r.status != "success" for r in self.sql_results):
                raise ValueError("answered draft has no successful evidence")
        elif self.error is None:
            raise ValueError("C draft failure must include a structured error")
        if self.status == "unsupported" and self.sql_results:
            raise ValueError("unsupported draft cannot contain executed SQL")
        return self


def build_c_request(task: SqlTaskRequest, *, request_id: str, profile: BusinessProfile) -> CRequest:
    """Whitelist, not model_dump of the whole B task. No SQL generation here."""
    task = SqlTaskRequest.model_validate(task.model_dump(mode="python"))
    if task.profile_id != profile.profile_id:
        raise CMappingError("INVALID_REQUEST", "查询任务与业务 profile 不一致。")
    backend = profile.sql_backend
    if not profile.supports("sql") or backend is None:
        raise CMappingError("UNSUPPORTED_CAPABILITY", "当前 profile 没有 SQL 能力。")
    if not set(task.metric_ids) <= set(backend.registered_metric_ids):
        raise CMappingError("INVALID_REQUEST", "指定指标尚未在 C 后端注册。")
    if not set(task.resolved_slots) <= set(backend.declared_slots):
        raise CMappingError("INVALID_REQUEST", "resolved_slots 含 C 后端未声明的槽位。")
    for name, value in task.resolved_slots.items():
        if not backend.declared_slots[name].accepts(value):
            raise CMappingError("INVALID_REQUEST", "槽位值类型或枚举范围不符合 C 配置。")
    required = {name for name, rule in backend.declared_slots.items() if rule.required_by_default}
    if not required <= set(task.resolved_slots):
        raise CMappingError("MISSING_REQUIRED_SLOT", "缺少 C 配置中的必要槽位。")
    return CRequest(
        request_id=request_id,
        profile_id=backend.profile_id,
        question=task.normalized_question or task.question,
        context=CContext(
            resolved_slots=task.resolved_slots,
            metric_ids=task.metric_ids,
            business_context=task.business_context,
            reference_time=task.reference_time,
        ),
        options=COptions(max_rows=task.max_rows, show_trace=task.show_trace),
    )


def create_c_sql_spec(handler: Callable[..., Awaitable[BaseModel | dict]], profiles: ProfileRegistry, *, timeout_ms: int = 60000):
    """Registration recipe with C policy preflight; it still never imports C."""
    from ..runtime.models import ToolSpec

    async def before_call(payload, context):
        try:
            build_c_request(payload, request_id=context.request_id, profile=profiles.get(context.profile_id))
        except CMappingError as exc:
            return exc.error
        return None

    return ToolSpec(
        name="sql.task",
        capability="sql",
        input_model=SqlTaskRequest,
        output_model=SqlTaskOutcome,
        handler=handler,
        timeout_ms=timeout_ms,
        before_call=before_call,
    )


_ERROR_MESSAGES = {
    "INVALID_REQUEST": "查询工具拒绝了不符合约定的请求。",
    "PROFILE_NOT_FOUND": "SQL 后端 profile 不存在或不可访问。",
    "PROFILE_UNAVAILABLE": "SQL 后端当前不可用。",
    "MISSING_REQUIRED_SLOT": "查询所需条件尚未补齐。",
    "SCHEMA_LINKING_FAILED": "查询对象无法与授权表结构对齐。",
    "SQL_READ_ONLY_VIOLATION": "查询未通过只读安全校验。",
    "SQL_EXECUTION_FAILED": "查询候选未通过校验或执行。",
    "UNSUPPORTED_CAPABILITY": "SQL 后端暂不支持本次任务。",
    "INTERNAL_ERROR": "查询工具内部异常。",
}
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")


def _public_error(error: CError | None) -> ApiError | None:
    if error is None:
        return None
    code = error.code if _SAFE_CODE.fullmatch(error.code) else "INTERNAL_ERROR"
    details = {}
    if isinstance(error.details, dict):
        for key in ("reason", "execution_code"):
            value = error.details.get(key)
            if isinstance(value, str) and _SAFE_CODE.fullmatch(value):
                details[key] = value
    return ApiError(code=code, message=_ERROR_MESSAGES.get(code, "查询工具未完成本次任务。"), retryable=error.retryable, details=details or None)


def normalize_c_response(
    raw: dict,
    *,
    task: SqlTaskRequest,
    request_id: str,
    profile: BusinessProfile,
) -> SqlTaskOutcome:
    """Keep raw diagnostics internal; do not pass this object to the web directly."""
    build_c_request(task, request_id=request_id, profile=profile)
    try:
        draft = CDraftResponse.model_validate(raw)
        backend = profile.sql_backend
        assert backend is not None
        if draft.request_id != request_id or draft.profile_id != backend.profile_id:
            raise ValueError("C response request/backend identity mismatch")
        if any(result.profile_id != backend.profile_id for result in draft.sql_results):
            raise ValueError("nested SQL result backend identity mismatch")
        artifacts = {artifact.query_id: artifact for artifact in draft.query_artifacts}
        limitations = list(draft.limitations)
        results = []
        for result in draft.sql_results:
            artifact = artifacts.get(result.query_id)
            if artifact and (result.status != "success" or artifact.selected_tables != result.source.tables):
                raise ValueError("artifact is not consistent with its accepted SQL result")
            if result.row_count > task.max_rows:
                raise ValueError("C result exceeded requested row limit")
            resolved = not (result.error and result.error.code in ("PROFILE_NOT_FOUND", "PROFILE_UNAVAILABLE"))
            name = backend.display_name if resolved else "未解析数据源"
            if result.source.name and result.source.name != name:
                limitations.append("工具来源名称与服务端配置不一致，展示配置确认的数据源名称。")
            results.append(SqlQueryResponse(
                query_id=result.query_id,
                profile_id=profile.profile_id,
                status=result.status,
                columns=result.columns,
                rows=result.rows,
                row_count=result.row_count,
                truncated=result.truncated,
                execution_ms=round(result.execution_ms),
                source={"type": result.source.type, "name": name, "tables": result.source.tables},
                sql=artifact.sql if artifact else None,
                params=artifact.params if artifact else None,
                error=_public_error(result.error),
            ))
            if result.status == "success" and artifact is None:
                limitations.append("查询结果没有对应的已接受 SQL artifact，无法展示 SQL 与参数。")
        metrics = []
        known_refs = {item.ref_id for item in task.business_context}
        for metric in profile.metric_definitions:
            known_refs.update(metric.source_refs)
        for metric in draft.metric_definitions:
            if metric.metric_id not in backend.registered_metric_ids:
                raise ValueError("C returned an unregistered metric")
            configured = profile.metric(metric.metric_id)
            if not set(metric.source_refs) <= known_refs:
                raise ValueError("C metric contains unrecognized source references")
            unit = (metric.unit or "").strip()
            if unit and configured and unit != configured.unit:
                raise ValueError("C metric unit conflicts with configured metric")
            if not unit and configured:
                unit = configured.unit
                limitations.append("工具未返回指标单位，采用服务端已登记指标定义的单位。")
            if not unit:
                limitations.append("指标单位缺失，未补造或返回该指标定义。")
                continue
            metrics.append(MetricDefinition(metric_id=metric.metric_id, name=metric.name, definition=metric.definition, unit=unit, source_refs=metric.source_refs))
        query_ids = [result.query_id for result in results]
        known_refs.update(query_ids)
        trace = []
        for index, step in enumerate(draft.trace, 1):
            if not set(step.source_refs) <= known_refs:
                raise ValueError("trace contains unrecognized source references")
            trace.append(TraceStep(step=index, tool=step.tool, status=step.status, source_refs=step.source_refs, duration_ms=round(step.duration_ms)))
        status = "success" if draft.status == "answered" else "unsupported" if draft.status == "unsupported" else "rejected" if any(r.status == "rejected" for r in results) else "failed"
        error = _public_error(draft.error)
        if status == "unsupported" and error:
            limitations.append(error.message)
        return SqlTaskOutcome(
            query_id=task.query_id,
            profile_id=profile.profile_id,
            status=status,
            sql_results=results,
            metric_definitions=metrics,
            limitations=list(dict.fromkeys(limitations)),
            trace=trace,
            source_refs=query_ids,
            error=error,
            diagnostics={
                "backend_profile_id": backend.profile_id,
                "profile_config_version": profile.config_version,
                "model_mode": backend.model_mode,
                "c_draft": draft.model_dump(mode="json"),
            },
        )
    except (ValidationError, ValueError) as exc:
        # Do not leak Pydantic's input_value, connection strings or raw C errors.
        raise CMappingError("INVALID_TOOL_OUTPUT", "C 返回形状、来源或请求标识不符合联调约定。") from exc
