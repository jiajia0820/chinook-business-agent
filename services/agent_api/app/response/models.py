"""Internal per-turn input. Deliberately excludes old answers/diagnostics/permits."""

from dataclasses import dataclass
import re
from typing import Literal, Protocol

from ..agent.state import BusinessTask
from ..contracts import ApiError, AskOptions, AskResponse, Calculation, Clarification, RetrievalResponse
from ..profiles.models import BusinessProfile
from ..runtime import ToolEvent
from ..tools.models import CalculationRequest, SqlTaskOutcome


ExecutionMode = Literal["fixture", "offline_sql_integration", "offline_sql_d12_integration", "calculation_fixture"]


_GENRE_ID_FORMS = (
    re.compile(r"(?i)\bGenre\s*Id\s*(?:为|是|=|:|：)\s*(\d+)"),
    re.compile(r"(?i)\bGenre\s+(?:为|是|=|:|：)\s*(\d+)"),
)


def normalize_answer_contract(answer: str) -> str:
    """Normalize stable entity spellings before exposing an answer."""
    normalized = answer
    for pattern in _GENRE_ID_FORMS:
        normalized = pattern.sub(lambda match: f"Genre:{match.group(1)}", normalized)
    return normalized


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
    calculation_audits: tuple[dict, ...] = ()
    business_audit: dict | None = None
    available_source_refs: tuple[str, ...] = ()
    events: tuple[ToolEvent, ...] = ()
    limitations: tuple[str, ...] = ()
    clarification: Clarification | None = None
    error: ApiError | None = None
    parser_reason: str | None = None
    reset_context: bool = False
    document_answer: str | None = None
    general_answer: str | None = None
    retrievals: tuple[RetrievalResponse, ...] = ()
    calculation_records: tuple[dict, ...] = ()


class ResponseComposer(Protocol):
    execution_mode: ExecutionMode
    source_banner: str

    def compose(self, context: ComposeContext) -> AskResponse: ...
