"""Profile-backed named formulas and deterministic Decimal evaluation.

The registry contains only trusted formula metadata.  Runtime callers bind
already-resolved evidence to a named function; arbitrary expression strings
are never evaluated.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FormulaSpec(BaseModel):
    """One profile-declared named formula."""

    model_config = ConfigDict(extra="forbid", strict=True)

    function: str = Field(min_length=1)
    expression: str = Field(min_length=1)
    inputs: list[str] = Field(min_length=1)
    unit: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)


class FormulaRegistry:
    """Immutable-in-practice collection of trusted :class:`FormulaSpec`s."""

    def __init__(self, formulas: Mapping[str, FormulaSpec]):
        self._formulas = {
            function: FormulaSpec.model_validate(spec.model_dump(mode="python"))
            for function, spec in formulas.items()
        }

    @property
    def functions(self) -> tuple[str, ...]:
        return tuple(self._formulas)

    def get(self, function: str) -> FormulaSpec:
        try:
            return self._formulas[function].model_copy(deep=True)
        except KeyError as exc:
            raise ValueError(f"unsupported formula function: {function}") from exc

    # ``formula`` is a readable alias for callers that treat the registry as
    # a catalog rather than a dictionary.
    def formula(self, function: str) -> FormulaSpec:
        return self.get(function)

    def __contains__(self, function: str) -> bool:
        return function in self._formulas

    @classmethod
    def from_profile(cls, profile: str | Path | Mapping[str, Any]) -> "FormulaRegistry":
        """Load formulas from a JSON profile path or decoded profile payload.

        A bare profile name such as ``chinook`` is resolved against the
        repository's ``profiles/<name>/formulas.json`` directory for callers
        that do not want to construct a deployment-relative path.
        """

        if isinstance(profile, Mapping):
            payload = dict(profile)
        else:
            path = Path(profile)
            if not path.exists() and path.name == path.as_posix() and path.suffix == "":
                root = Path(__file__).resolve().parents[4]
                path = root / "profiles" / path.name / "formulas.json"
            if not path.exists():
                raise FileNotFoundError(path)
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        formulas = payload.get("formulas")
        if not isinstance(formulas, list) or not formulas:
            raise ValueError("formula profile must contain a non-empty formulas list")
        parsed: dict[str, FormulaSpec] = {}
        for raw in formulas:
            spec = FormulaSpec.model_validate(raw)
            if spec.function in parsed:
                raise ValueError(f"duplicate formula function: {spec.function}")
            if len(spec.inputs) != len(set(spec.inputs)):
                raise ValueError(f"duplicate formula inputs: {spec.function}")
            parsed[spec.function] = spec
        return cls(parsed)


class Operand(BaseModel):
    """A resolved numeric fact and its semantic dimensions."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1)
    value: Decimal
    period: str = Field(min_length=1)
    scope: list[int | str]
    unit: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def finite_decimal(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("operand value must be a finite Decimal")
        return value


class FormulaRequest(BaseModel):
    """A named function plus evidence operands that passed semantic binding."""

    model_config = ConfigDict(extra="forbid")

    function: str = Field(min_length=1)
    operands: dict[str, Operand] = Field(min_length=1)


def _as_operand(value: Operand | Mapping[str, Any]) -> Operand:
    return value if isinstance(value, Operand) else Operand.model_validate(value)


def _same_semantics(operands: Sequence[Operand], *, periods: bool = True) -> None:
    if not operands:
        raise ValueError("formula requires at least one operand")
    scopes = {tuple(item.scope) for item in operands}
    metrics = {item.metric for item in operands}
    units = {item.unit for item in operands}
    if len(scopes) != 1 or len(metrics) != 1 or len(units) != 1:
        raise ValueError("formula operands must share period, scope, metric, and unit")
    if periods and len({item.period for item in operands}) != 1:
        raise ValueError("formula operands must share period, scope, metric, and unit")


def _require_function(registry: FormulaRegistry, function: str) -> FormulaSpec:
    return registry.get(function)


def bind_formula(
    registry: FormulaRegistry,
    function: str,
    actual: Operand | Mapping[str, Any] | None = None,
    target: Operand | Mapping[str, Any] | None = None,
    current: Operand | Mapping[str, Any] | None = None,
    previous: Operand | Mapping[str, Any] | None = None,
    *,
    monthly_plan: Sequence[Operand | Mapping[str, Any]] | None = None,
    **named: Operand | Mapping[str, Any],
) -> FormulaRequest:
    """Bind evidence to a profile formula and validate semantic compatibility.

    The positional names support target/growth formulas from the public
    contract.  ``average_order_value`` additionally accepts ``sales_amount``
    and ``order_count`` keyword names, while ``monthly_sum`` accepts a list of
    monthly operands through ``monthly_plan``.
    """

    spec = _require_function(registry, function)
    if function in {"target_difference", "attainment_rate"}:
        if actual is None or target is None:
            raise ValueError(f"{function} requires actual and target operands")
        if current is not None or previous is not None or named or monthly_plan is not None:
            raise ValueError(f"{function} received unexpected operands")
        operands = {"actual": _as_operand(actual), "target": _as_operand(target)}
        _same_semantics(tuple(operands.values()))
    elif function == "growth_rate":
        if current is None or previous is None:
            raise ValueError("growth_rate requires current and previous operands")
        if actual is not None or target is not None or named or monthly_plan is not None:
            raise ValueError("growth_rate received unexpected operands")
        operands = {"current": _as_operand(current), "previous": _as_operand(previous)}
        _same_semantics(tuple(operands.values()), periods=False)
    elif function == "average_order_value":
        sales_value = named.pop("sales_amount", None)
        orders_value = named.pop("order_count", None)
        sales_value = named.pop("sales", sales_value)
        orders_value = named.pop("orders", orders_value)
        if named or monthly_plan is not None:
            raise ValueError("average_order_value received unexpected operands")
        # The historical API used actual/target for the two inputs.  Keep that
        # shape accepted while storing profile-declared names in the request.
        sales_value = sales_value if sales_value is not None else (actual if actual is not None else current)
        orders_value = orders_value if orders_value is not None else (target if target is not None else previous)
        if sales_value is None or orders_value is None:
            raise ValueError("average_order_value requires sales_amount and order_count operands")
        sales = _as_operand(sales_value)
        orders = _as_operand(orders_value)
        if sales.metric != "sales_amount" or sales.unit != "USD":
            raise ValueError("average_order_value sales operand must be sales_amount in USD")
        if orders.metric != "order_count" or orders.unit != "单":
            raise ValueError("average_order_value order operand must be order_count in 单")
        if sales.period != orders.period or tuple(sales.scope) != tuple(orders.scope):
            raise ValueError("average_order_value operands must share period and scope")
        operands = {"sales_amount": sales, "order_count": orders}
    elif function == "monthly_sum":
        if monthly_plan is None and isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, Mapping)):
            monthly_plan = actual
            actual = None
        alternate = named.pop("monthly_values", None)
        alternate = named.pop("plans", alternate)
        if monthly_plan is None and alternate is not None:
            monthly_plan = alternate  # type: ignore[assignment]
        if actual is not None or target is not None or current is not None or previous is not None or named:
            raise ValueError("monthly_sum received unexpected operands")
        values = monthly_plan
        if values is None:
            raise ValueError("monthly_sum requires monthly_plan operands")
        if not values:
            raise ValueError("monthly_sum requires at least one monthly operand")
        bound = [_as_operand(value) for value in values]
        _same_semantics(bound, periods=False)
        operands = {f"month_{index}": value for index, value in enumerate(bound, start=1)}
    else:
        raise ValueError(f"unsupported formula function: {function}")

    expected = set(spec.inputs)
    if function == "monthly_sum":
        # The profile describes the logical input as monthly_plan; each month
        # is represented separately in the request so every source is retained.
        return FormulaRequest(function=function, operands=operands)
    if function in {"average_order_value"}:
        if expected != {"sales_amount", "order_count"}:
            raise ValueError(f"formula profile inputs do not match {function}")
    elif set(operands) != expected:
        raise ValueError(f"formula profile inputs do not match {function}")
    return FormulaRequest(function=function, operands=operands)


def evaluate_formula(request: FormulaRequest) -> Decimal:
    """Evaluate a bound named formula with Decimal arithmetic only."""

    if not isinstance(request, FormulaRequest):
        request = FormulaRequest.model_validate(request)
    values = {name: item.value for name, item in request.operands.items()}
    if request.function == "target_difference":
        if set(values) != {"actual", "target"}:
            raise ValueError("target_difference requires actual and target operands")
        _same_semantics(tuple(request.operands.values()))
        return values["actual"] - values["target"]
    if request.function == "attainment_rate":
        if set(values) != {"actual", "target"}:
            raise ValueError("attainment_rate requires actual and target operands")
        _same_semantics(tuple(request.operands.values()))
        if values["target"] == 0:
            raise ZeroDivisionError("target is zero")
        return values["actual"] / values["target"] * Decimal("100")
    if request.function == "growth_rate":
        if set(values) != {"current", "previous"}:
            raise ValueError("growth_rate requires current and previous operands")
        _same_semantics(tuple(request.operands.values()), periods=False)
        if values["previous"] == 0:
            raise ZeroDivisionError("previous is zero")
        return (values["current"] - values["previous"]) / values["previous"] * Decimal("100")
    if request.function == "average_order_value":
        if set(values) != {"sales_amount", "order_count"}:
            raise ValueError("average_order_value requires sales_amount and order_count operands")
        sales = request.operands["sales_amount"]
        orders = request.operands["order_count"]
        if sales.metric != "sales_amount" or sales.unit != "USD":
            raise ValueError("average_order_value sales operand must be sales_amount in USD")
        if orders.metric != "order_count" or orders.unit != "单":
            raise ValueError("average_order_value order operand must be order_count in 单")
        if sales.period != orders.period or tuple(sales.scope) != tuple(orders.scope):
            raise ValueError("average_order_value operands must share period and scope")
        if values["order_count"] == 0:
            raise ZeroDivisionError("order_count is zero")
        return values["sales_amount"] / values["order_count"]
    if request.function == "monthly_sum":
        if not values:
            raise ValueError("monthly_sum requires at least one monthly operand")
        _same_semantics(tuple(request.operands.values()), periods=False)
        return sum(values.values(), Decimal("0"))
    raise ValueError(f"unsupported formula function: {request.function}")


def evaluate_monthly_sum(plans: Sequence[Operand | Mapping[str, Any]]) -> Decimal:
    """Convenience wrapper for callers that already have monthly operands."""

    bound = [_as_operand(plan) for plan in plans]
    if not bound:
        raise ValueError("monthly_sum requires at least one monthly operand")
    _same_semantics(bound, periods=False)
    return sum((item.value for item in bound), Decimal("0"))
