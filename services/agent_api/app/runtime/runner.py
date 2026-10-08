"""Bounded single tool invocation with schema checks, hooks and local events."""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ValidationError

from ..contracts import ApiError, Calculation, RetrievalResponse, SqlQueryResponse, TraceStep
from ..profiles import ProfileRegistry
from ..tools.models import CalculationRequest, InternalModel, SqlTaskOutcome, SqlTaskRequest, ToolFailure, ToolStatus
from .models import ToolContext, ToolEvent, ToolOutcome, ToolSpec

logger = logging.getLogger("agent_api.runtime")


def _error(code: str, message: str, *, retryable: bool = False) -> ApiError:
    return ApiError(code=code, message=message, retryable=retryable)


def _validated(model: type[BaseModel], value: BaseModel | dict, *, strict: bool | None = None) -> BaseModel:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    return model.model_validate(value, strict=strict)


def _finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite tool output")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


def _check_output_scope(data: BaseModel, payload: BaseModel, context: ToolContext) -> None:
    if getattr(data, "profile_id", context.profile_id) != context.profile_id:
        raise ValueError("tool returned another profile's result")
    if isinstance(data, SqlTaskOutcome) and data.query_id != getattr(payload, "query_id", None):
        raise ValueError("SQL task ID does not match invocation")
    if isinstance(data, SqlQueryResponse) and data.query_id != getattr(payload, "query_id", None):
        raise ValueError("SQL query ID does not match invocation")
    sql_results = data.sql_results if isinstance(data, SqlTaskOutcome) else [data] if isinstance(data, SqlQueryResponse) else []
    for result in sql_results:
        if result.row_count != len(result.rows) or len(result.columns) != len(set(result.columns)):
            raise ValueError("SQL output row count/columns are inconsistent")
        if any(set(row) != set(result.columns) for row in result.rows):
            raise ValueError("SQL output row keys are inconsistent")
        if result.row_count > getattr(payload, "max_rows", result.row_count):
            raise ValueError("SQL output exceeded requested row limit")
    if isinstance(data, RetrievalResponse):
        if data.retrieval_id != getattr(payload, "retrieval_id", None):
            raise ValueError("retrieval ID does not match invocation")
        if len(data.chunks) > getattr(payload, "top_k", 0):
            raise ValueError("retrieval exceeded requested chunk limit")
        ids = [chunk.chunk_id for chunk in data.chunks]
        if len(ids) != len(set(ids)) or any(chunk.retrieval_id not in (None, data.retrieval_id) for chunk in data.chunks):
            raise ValueError("retrieval chunk identities are inconsistent")
    if isinstance(data, Calculation) and isinstance(payload, CalculationRequest):
        required = {item.source_ref for item in payload.operands.values()} | {payload.formula_ref}
        if data.calculation_id != payload.calculation_id or set(data.inputs) != required:
            raise ValueError("calculation output does not preserve input provenance")
        if data.unit != payload.result_unit:
            raise ValueError("calculation result unit is inconsistent")


def _domain_state(data: BaseModel) -> tuple[ToolStatus, ApiError | None, tuple[str, ...]]:
    if isinstance(data, SqlTaskOutcome):
        return data.status, data.error, tuple(data.source_refs)
    if isinstance(data, SqlQueryResponse):
        return data.status, data.error, (data.query_id,)
    if isinstance(data, RetrievalResponse):
        return data.status, data.error, tuple(chunk.chunk_id for chunk in data.chunks)
    if isinstance(data, Calculation):
        return "success", None, tuple(dict.fromkeys([data.calculation_id, *data.inputs]))
    raise ValueError("no domain-state classifier for this tool output model")


class ToolRuntime:
    def __init__(self, profiles: ProfileRegistry):
        self.profiles = profiles
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError("duplicate tool registration")
        if not issubclass(spec.output_model, (SqlTaskOutcome, SqlQueryResponse, RetrievalResponse, Calculation)):
            raise ValueError("tool output requires an explicitly supported domain-state classifier")
        self._tools[spec.name] = spec

    async def run(
        self,
        name: str,
        payload: BaseModel | dict,
        context: ToolContext,
        *,
        on_event: Callable[[ToolEvent], None] | None = None,
    ) -> ToolOutcome:
        # The opaque permit never enters JSON/checkpoints or payload schemas.
        # Preserve it explicitly when defensively revalidating server context.
        context = ToolContext.model_validate(context.model_dump(mode="python") | {"calculation_permit": context.calculation_permit})
        context = context.model_copy(update={"call_id": context.call_id or "call-" + str(uuid.uuid4())})
        started = time.monotonic()
        running_task = asyncio.current_task()
        initial_cancellations = running_task.cancelling() if running_task else 0
        events: list[ToolEvent] = []  # Never shared between requests or stored globally.

        def emit(event: str, status: str, refs: tuple[str, ...] = (), code: str | None = None) -> None:
            item = ToolEvent(
                request_id=context.request_id,
                session_id=context.session_id,
                profile_id=context.profile_id,
                call_id=context.call_id,
                tool=name,
                event=event,
                timestamp=time.monotonic(),
                status=status,
                duration_ms=round((time.monotonic() - started) * 1000) if event == "end" else 0,
                source_refs=refs,
                error_code=code,
            )
            events.append(item)
            if on_event:
                try:
                    on_event(item)
                except Exception:
                    # An observer cannot overwrite a business outcome or leak
                    # its exception text. Cancellation is not swallowed here.
                    logger.warning("tool_event_observer_failed", extra={"tool_call_id": context.call_id})

        def finish(status: ToolStatus, data: BaseModel | None, error: ApiError | None, refs: tuple[str, ...] = ()) -> ToolOutcome:
            emit("end", status, refs, error.code if error else None)
            return ToolOutcome(status, data, error, refs, tuple(events))

        emit("start", "running")
        spec = self._tools.get(name)
        if spec is None:
            return finish("blocked", None, _error("TOOL_NOT_FOUND", "工具未注册。"))
        try:
            profile = self.profiles.get(context.profile_id)
        except KeyError:
            return finish("blocked", None, _error("PROFILE_NOT_FOUND", "业务 profile 不存在。"))
        if not profile.supports(spec.capability):
            return finish("unsupported", None, _error("UNSUPPORTED_CAPABILITY", "当前 profile 不具备该工具能力。"))
        try:
            strict = None if issubclass(spec.input_model, InternalModel) else True
            validated = _validated(spec.input_model, payload, strict=strict)
            if getattr(validated, "profile_id", None) != context.profile_id:
                raise ValueError("input profile does not match context")
            _finite(validated.model_dump(mode="python"))
            if isinstance(validated, CalculationRequest):
                required = {item.source_ref for item in validated.operands.values()} | {validated.formula_ref}
                if not required <= set(context.available_source_refs):
                    return finish("blocked", None, _error("MISSING_SOURCE_REFERENCE", "计算输入或公式缺少已确认的来源引用。"))
        except (ValidationError, ValueError):
            return finish("blocked", None, _error("INVALID_TOOL_INPUT", "工具参数不符合 Schema 或请求范围。"))

        deadline = started + spec.timeout_ms / 1000
        if isinstance(validated, SqlTaskRequest):
            deadline = min(deadline, started + validated.timeout_ms / 1000)
        if context.deadline is not None:
            deadline = min(deadline, context.deadline)
        if deadline <= time.monotonic():
            return finish("timeout", None, _error("TOOL_TIMEOUT", "工具调用预算已耗尽。", retryable=True))
        context = context.model_copy(update={"deadline": deadline})

        def check_can_continue(timeout_scope: asyncio.Timeout) -> None:
            if timeout_scope.expired() or time.monotonic() >= deadline:
                raise TimeoutError()
            if running_task and running_task.cancelling() > initial_cancellations:
                # A hook/tool may suppress CancelledError, but the application
                # must still honor cancellation of this invocation.
                raise asyncio.CancelledError()

        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())) as timeout_scope:
                if spec.before_call:
                    blocked = await spec.before_call(validated.model_copy(deep=True), context)
                    check_can_continue(timeout_scope)
                    if blocked is not None:
                        blocked = ApiError.model_validate(blocked.model_dump(mode="python"))
                        return finish("blocked", None, blocked)
                check_can_continue(timeout_scope)
                raw = await spec.handler(validated.model_copy(deep=True), context)
                check_can_continue(timeout_scope)
                try:
                    output_strict = True if issubclass(spec.output_model, Calculation) else None
                    data = _validated(spec.output_model, raw, strict=output_strict)
                    _finite(data.model_dump(mode="python"))
                    _check_output_scope(data, validated, context)
                    if spec.after_call:
                        # Hooks can transform structured results, but a failing
                        # tool cannot be turned into success by the hook.
                        original_status, _, _ = _domain_state(data)
                        changed = await spec.after_call(data.model_copy(deep=True), context)
                        check_can_continue(timeout_scope)
                        data = _validated(spec.output_model, changed, strict=output_strict)
                        _finite(data.model_dump(mode="python"))
                        _check_output_scope(data, validated, context)
                        if original_status != "success" and _domain_state(data)[0] == "success":
                            raise ValueError("after_call cannot promote domain failure to success")
                    status, error, refs = _domain_state(data)
                except (ValidationError, ValueError, TypeError):
                    return finish("failed", None, _error("INVALID_TOOL_OUTPUT", "工具输出不符合 Schema、领域状态或来源范围。"))
                check_can_continue(timeout_scope)
                return finish(status, data, error, refs)
        except TimeoutError:
            return finish("timeout", None, _error("TOOL_TIMEOUT", "工具调用超时。", retryable=True))
        except asyncio.CancelledError:
            emit("end", "cancelled", code="TOOL_CANCELLED")
            raise
        except ToolFailure as exc:
            return finish("failed", None, exc.error)
        except Exception:
            # Never expose exception repr, Pydantic input_value or credentials.
            return finish("failed", None, _error("INTERNAL_ERROR", "工具调用异常。"))


def events_to_trace(
    events: Iterable[ToolEvent],
    *,
    request_id: str,
    profile_id: str,
    session_id: str,
    show_trace: bool = True,
    start_step: int = 1,
) -> list[TraceStep]:
    if type(start_step) is not int or start_step < 1:
        raise ValueError("start_step must be positive")
    items = list(events)
    if any(event.request_id != request_id or event.profile_id != profile_id or event.session_id != session_id for event in items):
        raise ValueError("events from different request/session/profile cannot share a trace")
    terminal_ids = [event.call_id for event in items if event.event == "end"]
    if len(terminal_ids) != len(set(terminal_ids)):
        raise ValueError("duplicate terminal events")
    if not show_trace:
        return []
    return [
        TraceStep(step=start_step + index, tool=event.tool, status=event.status, source_refs=list(event.source_refs), duration_ms=event.duration_ms)
        for index, event in enumerate(item for item in items if item.event == "end")
    ]
