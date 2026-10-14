"""Role based assembly for SQL and business-document evidence.

The builder is deliberately conservative: scalar calculator inputs come only
from SQL (actual/current/previous) and D06 target tables. Review and operation
chunks are retained as contextual evidence for rendering, but are never
converted into calculator operands.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re
from typing import Any, Iterable

from ..contracts import DocumentChunk, RetrievalResponse, SqlQueryResponse
from ..tools.models import SqlTaskOutcome
from .binding import comparison_period, named_formula_matches, result_hash, task_period, task_scope
from .formulas import FormulaRegistry
from .models import (
    CalculationEvidenceBundle,
    DocumentContextEvidence,
    DocumentLocator,
    DocumentScalarEvidence,
    EvidencePeriod,
    EvidenceScope,
    FormulaEvidence,
    SqlScalarEvidence,
)


ROLE_DOCS = {
    "target": {"D06"},
    "formula": {"D01", "D02"},
    "review": {"D07", "D08"},
    "operation": {"D09", "D10", "D11"},
}
INTENT_FUNCTION = {
    "target_difference": "difference",
    "target_attainment": "attainment_rate",
    "growth_rate": "growth_rate",
    "period_difference": "difference",
    "average_order_value": "average_order_value",
    "document_sum": "sum",
}
REGISTRY_FUNCTION = {
    "difference": "target_difference",
    "attainment_rate": "attainment_rate",
    "growth_rate": "growth_rate",
    "average_order_value": "average_order_value",
    "sum": "monthly_sum",
}


def _as_chunks(documents: RetrievalResponse | Iterable[DocumentChunk] | None) -> list[DocumentChunk]:
    if documents is None:
        return []
    if isinstance(documents, RetrievalResponse):
        return list(documents.chunks)
    if hasattr(documents, "chunks"):
        return list(documents.chunks)
    return [DocumentChunk.model_validate(item) if not isinstance(item, DocumentChunk) else item for item in documents]


def _period_from_task(task) -> EvidencePeriod:
    return task_period(task)


def _scope_from_task(task) -> EvidenceScope:
    return task_scope(task)


def _locator(chunk: DocumentChunk, start: int = 0, end: int | None = None) -> DocumentLocator:
    text = chunk.text
    return DocumentLocator(
        chunk_id=chunk.chunk_id, doc_id=chunk.doc_id, version="V1.0",
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_uri=chunk.source_uri, start=start, end=len(text) if end is None else end,
    )


def _number_span(text: str, value: Decimal, start: int = 0) -> tuple[int, int] | None:
    wanted = format(value, "f")
    for match in re.finditer(r"(?<![A-Za-z0-9_.+\-])\d+(?:\.\d{1,6})?(?![A-Za-z0-9_.+\-])", text[start:]):
        raw = match.group(0)
        if Decimal(raw) == value:
            return start + match.start(), start + match.end()
    return None


def _target_value(chunks: list[DocumentChunk], task, metric_id: str) -> tuple[DocumentChunk, Decimal, int, int] | None:
    from ..integrations.business_calculation import monthly_plan, quarterly_targets, target_period_matches

    scope = _scope_from_task(task)
    eligible = [chunk for chunk in chunks if target_period_matches(chunk, task)]
    month = task.slots.get('month')
    if month is not None:
        return next(((chunk, value, start, end) for plan_month, value, chunk, start, end
            in monthly_plan(eligible, scope, metric_id) if plan_month == month), None)
    return quarterly_targets(eligible, scope, metric_id)


def _formula_chunk(chunks: list[DocumentChunk], function: str, registry: FormulaRegistry) -> DocumentChunk | None:
    approved_docs = set(registry.get(REGISTRY_FUNCTION[function]).source_refs)
    candidates = [c for c in chunks if c.doc_id in approved_docs and named_formula_matches(c.text, function)]
    return next((c for c in candidates if c.doc_id == "D01"), candidates[0] if candidates else None)


def _sql_result(sql: SqlTaskOutcome | SqlQueryResponse | None, index: int = 0) -> SqlQueryResponse | None:
    if sql is None:
        return None
    if isinstance(sql, SqlQueryResponse):
        return sql
    results = getattr(sql, "sql_results", []) or []
    return results[index] if index < len(results) else None


def _column_for_metric(result: SqlQueryResponse, metric_id: str) -> str | None:
    wanted = {
        "sales_amount": ("sales_amount", "actual_sales", "actual_amount"),
        "units_sold": ("units_sold", "actual_units", "units", "quantity"),
        "order_count": ("order_count", "orders"),
    }.get(metric_id, (metric_id,))
    for col in wanted:
        if col in result.columns and result.rows and col in result.rows[0]:
            return col
    return None


def _sql_scalar(result: SqlQueryResponse, metric_id: str, period: EvidencePeriod, scope: EvidenceScope, role: str, row_index: int = 0) -> SqlScalarEvidence | None:
    if result.status != "success" or result.truncated or row_index >= len(result.rows):
        return None
    column = _column_for_metric(result, metric_id)
    if column is None:
        return None
    raw = result.rows[row_index].get(column)
    try:
        value = Decimal(str(raw)).quantize(Decimal("0.000001"))
    except Exception:
        return None
    unit = {"sales_amount": "USD", "units_sold": "件", "order_count": "单"}.get(metric_id, "USD")
    return SqlScalarEvidence(metric_id=metric_id, period=period, scope=scope, unit=unit, value=value,
        confirmed=True, query_id=result.query_id, row_index=row_index, column=column, result_sha256=result_hash(result))


def _context(chunks: list[DocumentChunk], role: str) -> list[DocumentContextEvidence]:
    docs = ROLE_DOCS[role]
    return [DocumentContextEvidence(role=role, locator=_locator(c), text=c.text) for c in chunks if c.doc_id in docs]


def _registry_check(function: str, registry: FormulaRegistry | None) -> None:
    if registry is None:
        return
    registry.get(REGISTRY_FUNCTION[function])


def build_cross_source_bundle(task, sql=None, documents=None, *, calculation_id: str = "calc-cross-source",
                              request_id: str = "request-cross-source", session_id: str = "session-cross-source",
                              profile_id: str = "chinook-music", formula_registry: FormulaRegistry | None = None):
    """Assemble a calculation bundle plus non-calculator review/operation roles."""
    function = INTENT_FUNCTION.get(task.intent)
    if function is None:
        raise ValueError(f"unsupported cross-source intent: {task.intent}")
    metric_id = (task.business_metric_ids or ["sales_amount"])[0]
    period = _period_from_task(task)
    scope = _scope_from_task(task)
    chunks = _as_chunks(documents)
    formula_registry = formula_registry or FormulaRegistry.from_profile('chinook')
    _registry_check(function, formula_registry)
    roles: dict[str, Any] = {}
    current_result = _sql_result(sql, 0)
    if function == "growth_rate" or task.intent == 'period_difference':
        previous_result = _sql_result(sql, 1) or current_result
        previous_row = 1 if previous_result is current_result else 0
        prior = comparison_period(task)
        current = _sql_scalar(current_result, metric_id, period, scope, "current") if current_result else None
        previous = _sql_scalar(previous_result, metric_id, prior, scope, "previous", previous_row) if previous_result else None
        if current is not None: roles['current' if function == 'growth_rate' else 'actual'] = current
        if previous is not None: roles['previous' if function == 'growth_rate' else 'target'] = previous
    elif function == "sum":
        from ..integrations.business_calculation import monthly_plan, target_period_matches
        if metric_id != 'sales_amount' or task.slots.get('quarter') is None:
            raise ValueError('monthly sum requires a quarterly USD plan')
        eligible = [chunk for chunk in chunks if target_period_matches(chunk, task)]
        plans = monthly_plan(eligible, scope, metric_id)
        quarter_start = (task.slots['quarter'] - 1) * 3 + 1
        if {month for month, *_ in plans} != set(range(quarter_start, quarter_start + 3)):
            raise ValueError('missing monthly plan evidence')
        for month, value, chunk, start, end in plans:
            roles[f'month_{task.slots["year"]}_{month:02}'] = DocumentScalarEvidence(metric_id=metric_id, period=period, scope=scope, unit="USD", value=value,
                confirmed=True, locator=_locator(chunk, start, end))
    elif function == "average_order_value":
        sales = _sql_scalar(current_result, "sales_amount", period, scope, "sales") if current_result else None
        orders = _sql_scalar(current_result, "order_count", period, scope, "orders") if current_result else None
        if sales: roles["sales"] = sales
        if orders: roles["orders"] = orders
    else:
        actual = _sql_scalar(current_result, metric_id, period, scope, "actual") if current_result else None
        target = _target_value(chunks, task, metric_id)
        if actual is not None: roles["actual"] = actual
        if target:
            chunk, value, start, end = target
            roles["target"] = DocumentScalarEvidence(metric_id=metric_id, period=period, scope=scope,
                unit={"sales_amount": "USD", "units_sold": "件"}.get(metric_id, "USD"), value=value,
                confirmed=True, locator=_locator(chunk, start, end))
    formula_chunk = _formula_chunk(chunks, function, formula_registry)
    if formula_chunk is None:
        raise ValueError("missing formula evidence")
    formula = FormulaEvidence(function=function, metric_id=metric_id, period=period, scope=scope,
        input_unit={"sales_amount": "USD", "units_sold": "件", "order_count": "单"}.get(metric_id, "USD"),
        result_unit={"difference": {"sales_amount": "USD", "units_sold": "件"}.get(metric_id, "USD"), "sum": "USD", "average_order_value": "USD/单"}.get(function, "%"),
        confirmed=True, locator=_locator(formula_chunk))
    review = _context(chunks, "review")
    operation = _context(chunks, "operation")
    missing = [role for role in (("actual", "target") if function in {"difference", "attainment_rate"} else ("current", "previous") if function == "growth_rate" else ()) if role not in roles]
    return CalculationEvidenceBundle(
        request_id=request_id, session_id=session_id, profile_id=profile_id, calculation_id=calculation_id,
        source_mode="verified_business", function=function, operands=roles, formula=formula,
        review=review, operation=operation, missing_roles=[*missing, *(["operation"] if not operation else [])],
    )


@dataclass(frozen=True)
class CrossSourceCalculation:
    result: Decimal
    function: str
    unit: str


@dataclass(frozen=True)
class CrossSourceResult:
    actual: Decimal | None
    target: Decimal | None
    gap: Decimal | None
    rate: Decimal | None
    calculation: CrossSourceCalculation | None
    review: tuple[DocumentContextEvidence, ...]
    operation: tuple[DocumentContextEvidence, ...]
    missing_roles: list[str]
    calculator_operands: tuple[str, ...]

    def operation_text_contains(self, value: str) -> bool:
        return any(value in item.text for item in self.operation)


def build_cross_source_result(task, sql=None, documents=None, **kwargs) -> CrossSourceResult:
    bundle = build_cross_source_bundle(task, sql, documents, **kwargs)
    operands = bundle.operands
    actual = operands.get("actual")
    target = operands.get("target")
    if actual is None or target is None:
        raise ValueError("missing actual/target evidence")
    if bundle.function == "difference":
        result = actual.value - target.value
        unit = actual.unit
    elif bundle.function == "attainment_rate":
        if target.value == 0:
            raise ValueError("target is zero")
        result = actual.value / target.value * Decimal("100")
        unit = "%"
    else:
        raise ValueError("cross source result helper supports target formulas only")
    missing = [role for role in bundle.missing_roles if role == "operation"]
    return CrossSourceResult(actual.value, target.value, actual.value - target.value, actual.value / target.value * Decimal("100"),
        CrossSourceCalculation(result, bundle.function, unit), tuple(bundle.review), tuple(bundle.operation), missing,
        tuple(role for role in operands if role in {"actual", "target", "current", "previous"}))
