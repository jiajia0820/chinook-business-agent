"""Invocation-local context and events inspired by pi's tool lifecycle."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import inspect
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import ApiError
from ..contracts.models import NonEmptyString
from ..tools.models import Capability, InternalModel, ToolStatus
from ..tools.calculation_permit import CalculationPermit


class ToolContext(InternalModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True, arbitrary_types_allowed=True)
    request_id: NonEmptyString
    session_id: NonEmptyString
    profile_id: NonEmptyString
    call_id: NonEmptyString | None = None  # Allocated afresh by run() when omitted.
    deadline: float | None = Field(default=None, ge=0)  # time.monotonic(), not wall time.
    available_source_refs: tuple[NonEmptyString, ...] = ()
    calculation_permit: CalculationPermit | None = Field(default=None, exclude=True, repr=False)


class ToolEvent(InternalModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)
    request_id: NonEmptyString
    session_id: NonEmptyString
    profile_id: NonEmptyString
    call_id: NonEmptyString
    tool: NonEmptyString
    event: Literal["start", "end"]
    timestamp: float = Field(ge=0)
    status: Literal["running", "success", "failed", "rejected", "unsupported", "blocked", "timeout", "cancelled"]
    duration_ms: int = Field(default=0, ge=0)
    source_refs: tuple[NonEmptyString, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class ToolOutcome:
    status: ToolStatus
    data: BaseModel | None
    error: ApiError | None
    source_refs: tuple[str, ...]
    events: tuple[ToolEvent, ...]

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[BaseModel | dict]]
BeforeCall = Callable[[BaseModel, ToolContext], Awaitable[ApiError | None]]
AfterCall = Callable[[BaseModel, ToolContext], Awaitable[BaseModel | dict]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    capability: Capability
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    timeout_ms: int = 60000
    before_call: BeforeCall | None = None
    after_call: AfterCall | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip() or self.name != self.name.strip():
            raise ValueError("tool name must be nonempty and have no surrounding whitespace")
        if self.capability not in ("sql", "rag", "calculate"):
            raise ValueError("unknown capability")
        if type(self.timeout_ms) is not int or self.timeout_ms < 1:
            raise ValueError("timeout_ms must be a positive integer")
        if not isinstance(self.input_model, type) or not isinstance(self.output_model, type):
            raise ValueError("tools require model classes, not model instances")
        if not issubclass(self.input_model, BaseModel) or not issubclass(self.output_model, BaseModel):
            raise ValueError("tools require Pydantic input/output models")
        if not callable(self.handler):
            raise ValueError("tool handler must be callable")
        for handler in (self.handler, self.before_call, self.after_call):
            if handler is not None and not (inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(getattr(handler, "__call__", None))):
                raise ValueError("handlers/hooks must be asynchronous; wrap synchronous C in 3C")
