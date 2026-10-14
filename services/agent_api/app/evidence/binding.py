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
from .formulas import FormulaRegistry
from .ledger import EvidenceItem, EvidenceLedger
from .verification import verify_same_scope


FUNCTION_BY_INTENT = {"target_difference": "difference", "target_attainment": "attainment_rate", "growth_rate": "growth_rate",
    'average_order_value':'average_order_value','document_sum':'sum','period_difference':'difference'}


def _verify_cross_source_scope(bundle: CalculationEvidenceBundle, *, allow_period_difference: bool = False) -> None:
    """Check the semantic dimensions of operands that come from different sources.

    The per-operand checks below bind each value to the task.  This ledger check
    additionally verifies the relationship between the SQL/document pair before
    issuing a calculator permit, so a cross-source mismatch cannot pass merely
    because each scalar looks individually plausible.  Period-difference uses
    the named ``difference`` function with intentionally distinct periods and
    is excluded by the caller.
    """
    if allow_period_difference and bundle.function == "difference":
        return
    pair = {
        "difference": ("actual", "target"),
        "attainment_rate": ("actual", "target"),
        "growth_rate": ("current", "previous"),
    }.get(bundle.function)
    if pair is None or not all(role in bundle.operands for role in pair):
        return
    items = []
    for role in pair:
        evidence = bundle.operands[role]
        if isinstance(evidence, SqlScalarEvidence):
            source_kind = "sql"
            source_ref = evidence.query_id
        else:
            source_kind = "document"
            source_ref = evidence.locator.chunk_id
        items.append(EvidenceItem(
            fact_role=role,
            metric=evidence.metric_id,
            value=evidence.value,
            unit=evidence.unit,
            period=evidence.period,
            scope=evidence.scope,
            source_ref=source_ref,
            source_kind=source_kind,
            validation_status="verified" if evidence.confirmed else "unverified",
            confirmed=evidence.confirmed,
        ))
    ledger = EvidenceLedger(items=items)
    left, right = (ledger.required(role) for role in pair)
    if not verify_same_scope(left, right):
        raise ValueError("cross-source evidence scope mismatch")


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tampered_chunks(chunks, snapshots) -> list:
    """Return the chunks that drifted from the server's trusted document snapshot.

    ``snapshots`` is captured by a trusted server factory at startup, so it is the
    only authoritative copy of a chunk's text.  A ``DocumentLocator.content_sha256``
    derived from the returned chunk is self-referential and cannot detect tampering;
    this comparison can.  An empty snapshot set means the deployment ships no
    authoritative copy, so nothing is reported instead of rejecting every fixture turn.
    """
    if not snapshots:
        return []
    trusted = {item.chunk_id: item for item in snapshots}
    drifted = []
    for chunk in chunks:
        known = trusted.get(chunk.chunk_id)
        if known is None or (chunk.doc_id, chunk.text, chunk.source_uri) != (known.doc_id, known.text, known.source_uri):
            drifted.append(chunk)
    return drifted


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
    month = task.slots.get("month")
    if month is not None and (type(month) is not int or not 1 <= month <= 12 or quarter is not None):
        raise ValueError("invalid monthly calculation period")
    start = date(year, month or ((quarter - 1) * 3 + 1 if quarter else 1), 1)
    end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) if month else (date(year + 1, 1, 1) if not quarter or quarter == 4 else date(year, quarter * 3 + 1, 1))
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
        if chunk.doc_type == "fixture" or chunk.source_uri.startswith("fixture://"):
            raise ValueError("business provenance cannot use a fixture")
    return chunk.text[locator.start:locator.end]


def named_formula_matches(text: str, function: str) -> bool:
    """A source must name this function; a numeric table is not a formula."""
    if text.strip() == FORMULAS[function]:
        return True
    labels = {
        'difference': ('销售额差额', '目标差额', '本期销售额 - 上期销售额', '实际销售额 - 目标销售额'),
        'attainment_rate': ('销售额达成率', '目标达成率', '实际销售额 / 目标销售额'),
        'growth_rate': ('增长率', 'period_over_period_growth'),
        'average_order_value': ('客单价', '销售额 / 订单数', '销售额除以订单数'),
        # The trusted catalog explicitly declares D06's monthly-plan sum.
        'sum': ('月度分解', 'sum(monthly_plan)'),
    }
    return any(label in text for label in labels[function])


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
    expected_roles={'sales','orders'} if function=='average_order_value' else (set(bundle.operands) if function=='sum' else OPERANDS.get(function,set()))
    if not task.needs_calculation or function is None or bundle.function != function or set(bundle.operands) != expected_roles:
        raise ValueError("wrong intent/function/operand names")
    _verify_cross_source_scope(bundle, allow_period_difference=task.intent == "period_difference")
    if len(task.business_metric_ids) != 1:
        raise ValueError("missing/ambiguous metric")
    metric_id = task.business_metric_ids[0]
    metric = profile.metric(metric_id)
    if metric is None:
        raise ValueError("unregistered business metric")
    if function=='sum' and metric_id!='sales_amount':
        raise ValueError('monthly monetary plan cannot substitute for other units')
    period, scope = task_period(task), task_scope(task)
    prior = comparison_period(task) if function == "growth_rate" or task.intent=='period_difference' else None
    if (function!='sum' and (sql is None or sql.status != "success" or sql.profile_id != profile.profile_id)) or rag is None or rag.status != "success" or rag.profile_id != profile.profile_id:
        raise ValueError("missing successful source objects")
    results = {result.query_id: result for result in sql.sql_results} if sql else {}
    chunks = {chunk.chunk_id: chunk for chunk in rag.chunks}
    if len(chunks) != len(rag.chunks) or any(chunk.retrieval_id != rag.retrieval_id for chunk in rag.chunks):
        raise ValueError("ambiguous/stale retrieval objects")
    formula = bundle.formula
    expected_unit = 'USD/单' if function == 'average_order_value' else metric.unit if function in {'difference','sum'} else "%"
    input_unit = 'USD' if function == 'average_order_value' else metric.unit
    if not formula.confirmed or formula.function != function or formula.metric_id != metric_id or formula.period != period or formula.scope != scope or formula.input_unit != input_unit or formula.result_unit != expected_unit:
        raise ValueError("formula semantics mismatch")
    # FormulaRegistry is the trusted function catalog.  Document text can
    # attest provenance, but it cannot introduce a new expression or input
    # name at runtime.
    try:
        registry = FormulaRegistry.from_profile("chinook")
        registry_function = {"difference": "target_difference", "attainment_rate": "attainment_rate",
            "growth_rate": "growth_rate", "average_order_value": "average_order_value", "sum": "monthly_sum"}[function]
        spec = registry.get(registry_function)
    except (FileNotFoundError, ValueError, KeyError):
        raise ValueError("formula registry unavailable") from None
    expected_registry_inputs = {"actual", "target"} if function in {"difference", "attainment_rate"} else {"current", "previous"} if function == "growth_rate" else {"sales_amount", "order_count"} if function == "average_order_value" else {"monthly_plan"}
    if set(spec.inputs) != expected_registry_inputs:
        raise ValueError("formula registry inputs mismatch")
    if formula.locator.doc_id not in set(spec.source_refs):
        raise ValueError("formula source is not registry-authorized")
    formula_text = _document_text(formula.locator, chunks, bundle.source_mode)
    if formula_text != FORMULAS[function]:
        if bundle.source_mode != "verified_business" or not named_formula_matches(formula_text, function):
            raise ValueError("formula does not match approved named function")
    operands = {}
    for role, evidence in bundle.operands.items():
        expected_period = prior if role == "previous" or role=='target' and task.intent=='period_difference' else period
        operand_metric=profile.metric('sales_amount' if role=='sales' else 'order_count') if function=='average_order_value' else metric
        if not evidence.confirmed or evidence.metric_id != operand_metric.metric_id or evidence.period != expected_period or evidence.scope != scope or evidence.unit != operand_metric.unit:
            raise ValueError("operand semantics mismatch")
        validate_numeric(evidence.value, evidence.unit)
        if isinstance(evidence, SqlScalarEvidence):
            if role == "target" and task.intent!='period_difference':
                raise ValueError("target must be confirmed document evidence")
            result = results.get(evidence.query_id)
            if result is None or result.status != "success" or result.truncated or result.row_count != len(result.rows) or len(result.columns) != len(set(result.columns)) or any(set(row) != set(result.columns) for row in result.rows) or result_hash(result) != evidence.result_sha256 or evidence.row_index >= len(result.rows) or evidence.column not in result.columns:
                raise ValueError("SQL pointer/hash/status mismatch")
            if bundle.source_mode == "fixture" and (result.source.type != "fixture" or not result.query_id.startswith("fixture-") or result.source.tables):
                raise ValueError("fixture SQL masquerades as a database")
            if bundle.source_mode != "fixture" and result.source.type != "database":
                raise ValueError("invalid business SQL provenance")
            if bundle.source_mode == 'verified_business':
                metric_columns = {'sales_amount': {'sales_amount', 'actual_sales', 'actual_amount'},
                    'units_sold': {'units_sold', 'actual_units', 'units', 'quantity'},
                    'order_count': {'order_count', 'orders'}}
                if evidence.column not in metric_columns.get(evidence.metric_id, {evidence.metric_id}):
                    raise ValueError('SQL metric column mismatch')
            value = result.rows[evidence.row_index].get(evidence.column)
            if type(value) not in (int, float, str) or isinstance(value, str) and not re.fullmatch(r"\d+(?:\.\d{1,6})?", value):
                raise ValueError("invalid SQL numeric representation")
            raw_value = Decimal(str(value))
            normalized_value = raw_value.quantize(Decimal("0.000001"))
            validate_numeric(normalized_value, evidence.unit)
            if normalized_value != evidence.value:
                raise ValueError("SQL scalar mismatch")
            ref = sql_cell_ref(result.query_id, evidence.row_index, evidence.column)
        elif isinstance(evidence, DocumentScalarEvidence):
            if role != "target" and function!='sum':
                raise ValueError("actual/current/previous require SQL evidence")
            raw = _document_text(evidence.locator, chunks, bundle.source_mode)
            text = chunks[evidence.locator.chunk_id].text
            before = text[evidence.locator.start - 1:evidence.locator.start] if evidence.locator.start else ""
            after = text[evidence.locator.end:evidence.locator.end + 1]
            if any(re.fullmatch(r"[A-Za-z0-9_.+\-]", char) for char in (before, after) if char):
                raise ValueError("document number is a partial/signed/date token")
            if not re.fullmatch(r"\d+(?:\.\d{1,6})?", raw) or Decimal(raw) != evidence.value:
                raise ValueError("document scalar span mismatch")
            if bundle.source_mode=='verified_business':
                from ..integrations.business_calculation import quarterly_targets, monthly_plan, target_period_matches
                if not target_period_matches(chunks[evidence.locator.chunk_id], task):
                    raise ValueError('target document period mismatch')
                eligible = [chunk for chunk in rag.chunks if target_period_matches(chunk, task)]
                if function=='sum':
                    if metric.unit != 'USD' or task.slots.get('quarter') is None:
                        raise ValueError('monthly sum requires a quarterly USD plan')
                    plans=monthly_plan(eligible,scope,metric_id)
                    expected_plan={f'month_{year}_{month:02}':(chunk,value,start,end)
                        for month,value,chunk,start,end in plans for year in [task.slots['year']]}
                    quarter_start = (task.slots['quarter'] - 1) * 3 + 1
                    if set(bundle.operands)!=set(expected_plan) or {month for month, *_ in plans} != set(range(quarter_start, quarter_start + 3)):
                        raise ValueError('monthly plan coverage mismatch')
                    bound=expected_plan[role]
                elif task.slots.get('month') is not None:
                    bound=next(((chunk,value,start,end) for month,value,chunk,start,end in monthly_plan(eligible,scope,metric_id)
                        if month==task.slots['month']), None)
                else:
                    bound=quarterly_targets(eligible,scope,metric_id)
                if bound is None or (bound[0].chunk_id,bound[1],bound[2],bound[3]) != (evidence.locator.chunk_id,evidence.value,evidence.locator.start,evidence.locator.end):
                    raise ValueError('document entity/metric/column mismatch')
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
