"""Scope/period/unit and exact this-turn provenance, before Calculator calls."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
import re

from ..agent.state import BusinessTask
from ..contracts import RetrievalResponse, SqlQueryResponse
from ..profiles.models import BusinessProfile
from ..tools.calculation_permit import CalculationPermit, _issue_permit
from ..tools.local_calculator import FORMULAS, OPERANDS, validate_numeric, validate_request
from ..tools.models import CalculationRequest, SqlTaskOutcome
from ..tools.references import sql_cell_ref
from .models import CalculationEvidenceBundle, DocumentLocator, DocumentScalarEvidence, EvidencePeriod, EvidenceScope, SqlScalarEvidence


FUNCTION_BY_INTENT = {"target_difference": "difference", "target_attainment": "attainment_rate", "growth_rate": "growth_rate"}


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def result_hash(result: SqlQueryResponse) -> str:
    value = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text_hash(value)


def task_period(task: BusinessTask) -> EvidencePeriod:
    if task.time_range is None or task.time_range.end_inclusive:
        raise ValueError("missing explicit exclusive period")
    period = EvidencePeriod(start=task.time_range.start, end=task.time_range.end)
    year, quarter = task.slots.get("year"), task.slots.get("quarter")
    if type(year) is not int or not 1 <= year <= 9998 or quarter is not None and (type(quarter) is not int or not 1 <= quarter <= 4):
        raise ValueError("invalid period slots")
    start = date(year, (quarter - 1) * 3 + 1 if quarter else 1, 1)
    end = date(year + 1, 1, 1) if not quarter or quarter == 4 else date(year, quarter * 3 + 1, 1)
    if period.start != start.isoformat() or period.end != end.isoformat():
        raise ValueError("period does not match explicit slots")
    return period


def comparison_period(task: BusinessTask) -> EvidencePeriod:
    value = task.slots.get("comparison_period")
    if not isinstance(value, dict) or set(value) != {"year", "quarter"}:
        raise ValueError("missing comparison period")
    year, quarter = value["year"], value["quarter"]
    if type(year) is not int or type(quarter) is not int or not 1 <= year <= 9998 or not 1 <= quarter <= 4:
        raise ValueError("invalid comparison period")
    start = date(year, (quarter - 1) * 3 + 1, 1)
    end = date(year + 1, 1, 1) if quarter == 4 else date(year, quarter * 3 + 1, 1)
    period = EvidencePeriod(start=start.isoformat(), end=end.isoformat())
    current = task_period(task)
    if period.end > current.start:
        raise ValueError("overlapping/reversed comparison periods")
    return period


def task_scope(task: BusinessTask) -> EvidenceScope:
    if any(entity.get("type") not in {"genre", "country"} for entity in task.entities):
        raise ValueError("unsupported semantic scope dimension")
    return EvidenceScope(media_ids=sorted(task.slots.get("media_ids", [])),
        genre_ids=sorted(entity["id"] for entity in task.entities if entity["type"] == "genre"),
        country_ids=sorted(entity["id"] for entity in task.entities if entity["type"] == "country"))


def _document_text(locator: DocumentLocator, chunks: dict, mode: str) -> str:
    chunk = chunks.get(locator.chunk_id)
    if chunk is None or chunk.doc_id != locator.doc_id or chunk.source_uri != locator.source_uri or text_hash(chunk.text) != locator.content_sha256 or locator.end > len(chunk.text):
        raise ValueError("document identity/hash/span mismatch")
    if mode == "fixture":
        if chunk.doc_type != "fixture" or not chunk.doc_id.startswith("fixture-") or not chunk.source_uri.startswith("fixture://"):
            raise ValueError("fixture provenance masquerades as business document")
        if re.search(r"(?m)^version=" + re.escape(locator.version) + r"$", chunk.text) is None:
            raise ValueError("fixture document version mismatch")
    else:
        # The future business resolver also needs an approved version catalog.
        # No such resolver is supplied by the present real D12 factory.
        if chunk.doc_type == "fixture" or chunk.source_uri.startswith("fixture://"):
            raise ValueError("business provenance cannot use a fixture")
        raise ValueError("verified business document catalog not yet installed")
    return chunk.text[locator.start:locator.end]


@dataclass(frozen=True)
class AuthorizedCalculation:
    request: CalculationRequest
    permit: CalculationPermit
    audit: dict


def authorize_calculation(bundle: CalculationEvidenceBundle, *, task: BusinessTask, sql: SqlTaskOutcome | None,
    rag: RetrievalResponse | None, profile: BusinessProfile, context, calculation_id: str, expected_mode: str) -> AuthorizedCalculation:
    bundle = CalculationEvidenceBundle.model_validate(bundle.model_dump(mode="python"))
    task = BusinessTask.model_validate(task.model_dump(mode="python"))
    sql = SqlTaskOutcome.model_validate(sql.model_dump(mode="python")) if sql is not None else None
    rag = RetrievalResponse.model_validate(rag.model_dump(mode="python")) if rag is not None else None
    if (bundle.request_id, bundle.session_id, bundle.profile_id, bundle.calculation_id) != (context.request_id, context.session_id, context.profile_id, calculation_id) or profile.profile_id != context.profile_id or bundle.source_mode != expected_mode or not profile.supports("calculate"):
        raise ValueError("wrong turn/profile/source mode")
    function = FUNCTION_BY_INTENT.get(task.intent)
    if not task.needs_calculation or function is None or bundle.function != function or set(bundle.operands) != OPERANDS[function]:
        raise ValueError("wrong intent/function/operand names")
    if len(task.business_metric_ids) != 1:
        raise ValueError("missing/ambiguous metric")
    metric_id = task.business_metric_ids[0]
    metric = profile.metric(metric_id)
    if metric is None:
        raise ValueError("unregistered business metric")
    period, scope = task_period(task), task_scope(task)
    prior = comparison_period(task) if function == "growth_rate" else None
    if sql is None or sql.status != "success" or sql.profile_id != profile.profile_id or rag is None or rag.status != "success" or rag.profile_id != profile.profile_id:
        raise ValueError("missing successful source objects")
    results = {result.query_id: result for result in sql.sql_results}
    chunks = {chunk.chunk_id: chunk for chunk in rag.chunks}
    if len(chunks) != len(rag.chunks) or any(chunk.retrieval_id != rag.retrieval_id for chunk in rag.chunks):
        raise ValueError("ambiguous/stale retrieval objects")
    formula = bundle.formula
    expected_unit = metric.unit if function == "difference" else "%"
    if not formula.confirmed or formula.function != function or formula.metric_id != metric_id or formula.period != period or formula.scope != scope or formula.input_unit != metric.unit or formula.result_unit != expected_unit:
        raise ValueError("formula semantics mismatch")
    if _document_text(formula.locator, chunks, bundle.source_mode) != FORMULAS[function]:
        raise ValueError("formula does not match approved named function")
    operands = {}
    for role, evidence in bundle.operands.items():
        expected_period = prior if role == "previous" else period
        if not evidence.confirmed or evidence.metric_id != metric_id or evidence.period != expected_period or evidence.scope != scope or evidence.unit != metric.unit:
            raise ValueError("operand semantics mismatch")
        validate_numeric(evidence.value, evidence.unit)
        if isinstance(evidence, SqlScalarEvidence):
            if role == "target":
                raise ValueError("target must be confirmed document evidence")
            result = results.get(evidence.query_id)
            if result is None or result.status != "success" or result.truncated or result.row_count != len(result.rows) or len(result.columns) != len(set(result.columns)) or any(set(row) != set(result.columns) for row in result.rows) or result_hash(result) != evidence.result_sha256 or evidence.row_index >= len(result.rows) or evidence.column not in result.columns:
                raise ValueError("SQL pointer/hash/status mismatch")
            if bundle.source_mode == "fixture" and (result.source.type != "fixture" or not result.query_id.startswith("fixture-") or result.source.tables):
                raise ValueError("fixture SQL masquerades as a database")
            if bundle.source_mode != "fixture" and result.source.type != "database":
                raise ValueError("invalid business SQL provenance")
            value = result.rows[evidence.row_index].get(evidence.column)
            if type(value) not in (int, float, str) or isinstance(value, str) and not re.fullmatch(r"\d+(?:\.\d{1,6})?", value):
                raise ValueError("invalid SQL numeric representation")
            raw_value = Decimal(str(value))
            validate_numeric(raw_value, evidence.unit)
            if raw_value != evidence.value:
                raise ValueError("SQL scalar mismatch")
            ref = sql_cell_ref(result.query_id, evidence.row_index, evidence.column)
        elif isinstance(evidence, DocumentScalarEvidence):
            if role != "target":
                raise ValueError("actual/current/previous require SQL evidence")
            raw = _document_text(evidence.locator, chunks, bundle.source_mode)
            text = chunks[evidence.locator.chunk_id].text
            before = text[evidence.locator.start - 1:evidence.locator.start] if evidence.locator.start else ""
            after = text[evidence.locator.end:evidence.locator.end + 1]
            if any(re.fullmatch(r"[A-Za-z0-9_.+\-]", char) for char in (before, after) if char):
                raise ValueError("document number is a partial/signed/date token")
            if not re.fullmatch(r"\d+(?:\.\d{1,6})?", raw) or Decimal(raw) != evidence.value:
                raise ValueError("document scalar span mismatch")
            ref = evidence.locator.chunk_id
        else:
            raise ValueError("unsupported evidence type")
        operands[role] = {"value": evidence.value, "unit": evidence.unit, "source_ref": ref}
    request = validate_request(CalculationRequest(calculation_id=bundle.calculation_id, profile_id=profile.profile_id,
        function=function, formula_ref=formula.locator.chunk_id, operands=operands, result_unit=expected_unit))
    required = {request.formula_ref, *(item.source_ref for item in request.operands.values())}
    if not required <= set(context.available_source_refs):
        raise ValueError("not this invocation's available sources")
    permit = _issue_permit(request, request_id=context.request_id, session_id=context.session_id)
    return AuthorizedCalculation(request, permit, bundle.model_dump(mode="json"))
