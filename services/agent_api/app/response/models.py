"""Internal per-turn input. Deliberately excludes old answers/diagnostics/permits."""

from dataclasses import dataclass
from typing import Literal, Protocol

from ..agent.state import BusinessTask
from ..contracts import ApiError, AskOptions, AskResponse, Calculation, Clarification, RetrievalResponse
from ..profiles.models import BusinessProfile
from ..runtime import ToolEvent
from ..tools.models import CalculationRequest, SqlTaskOutcome


ExecutionMode = Literal["fixture", "offline_sql_integration", "offline_sql_d12_integration", "calculation_fixture"]


@dataclass(frozen=True)
class ComposeContext:
    request_id: str
    session_id: str
    profile: BusinessProfile
    status: Literal["answered", "clarification_required", "insufficient_evidence", "unsupported", "error"]
    route: str
    task: BusinessTask | None
    options: AskOptions
    sql: SqlTaskOutcome | None = None
    rag: RetrievalResponse | None = None
    calculations: tuple[Calculation, ...] = ()
    binding: CalculationRequest | None = None
    binding_audit: dict | None = None
    available_source_refs: tuple[str, ...] = ()
    events: tuple[ToolEvent, ...] = ()
    limitations: tuple[str, ...] = ()
    clarification: Clarification | None = None
    error: ApiError | None = None
    parser_reason: str | None = None
    reset_context: bool = False


class ResponseComposer(Protocol):
    execution_mode: ExecutionMode
    source_banner: str

    def compose(self, context: ComposeContext) -> AskResponse: ...
