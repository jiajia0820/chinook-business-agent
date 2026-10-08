"""Tools are replaceable. LangGraph, not these interfaces, owns task sequencing."""

from typing import Protocol

from ..contracts import Calculation, RetrievalRequest, RetrievalResponse
from ..runtime.models import ToolContext
from .models import CalculationRequest, SqlTaskOutcome, SqlTaskRequest


class SqlTaskTool(Protocol):
    async def run(self, request: SqlTaskRequest, context: ToolContext) -> SqlTaskOutcome: ...


class RagTool(Protocol):
    async def run(self, request: RetrievalRequest, context: ToolContext) -> RetrievalResponse: ...


class CalculatorTool(Protocol):
    async def run(self, request: CalculationRequest, context: ToolContext) -> Calculation: ...
