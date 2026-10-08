"""Three bounded Decimal operations, never eval/exec document formulas."""

import asyncio
from decimal import Decimal, ROUND_HALF_UP, localcontext

from ..contracts import ApiError, Calculation
from .models import CalculationRequest, ToolFailure


FORMULAS = {
    "difference": "actual - target",
    "attainment_rate": "actual / target * 100",
    "growth_rate": "(current - previous) / previous * 100",
}
OPERANDS = {"difference": {"actual", "target"}, "attainment_rate": {"actual", "target"}, "growth_rate": {"current", "previous"}}
INPUT_UNITS = frozenset({"USD", "CNY", "人", "件", "单", "条"})
COUNT_UNITS = frozenset({"人", "件", "单", "条"})
MAX_VALUE = Decimal("1000000000000")
QUANTUM = Decimal("0.01")


def failure(code: str, message: str):
    return ToolFailure(ApiError(code=code, message=message, retryable=False))


def validate_numeric(value: Decimal, unit: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or unit not in INPUT_UNITS:
        raise failure("CALCULATION_INPUT_INVALID", "计算输入必须是有限数值和支持的明确单位。")
    sign, digits, exponent = value.as_tuple()
    if len(digits) > 19 or exponent < -6 or exponent > 12 or value < 0 or value > MAX_VALUE:
        raise failure("CALCULATION_INPUT_INVALID", "计算输入须为非负数、不超过 1e12，最多 6 位小数、19 位系数。")
    if unit in COUNT_UNITS and value != value.to_integral_value():
        raise failure("CALCULATION_INPUT_INVALID", "计数单位不接受非整数输入。")


def validate_request(request: CalculationRequest) -> CalculationRequest:
    request = CalculationRequest.model_validate(request.model_dump(mode="python"))
    if set(request.operands) != OPERANDS[request.function]:
        raise failure("CALCULATION_INPUT_INVALID", "计算输入名或数量不符合命名函数。")
    units = {item.unit for item in request.operands.values()}
    if len(units) != 1:
        raise failure("CALCULATION_INPUT_INVALID", "计算输入单位不同，未自动换算。")
    unit = next(iter(units))
    for operand in request.operands.values():
        validate_numeric(operand.value, operand.unit)
    if request.result_unit != (unit if request.function == "difference" else "%"):
        raise failure("CALCULATION_INPUT_INVALID", "计算结果单位与函数不一致。")
    return request


def calculate_decimal(request: CalculationRequest) -> Decimal:
    """Pure arithmetic for unit tests; does not authorize a business tool call."""
    request = validate_request(request)
    values = {name: operand.value for name, operand in request.operands.items()}
    with localcontext() as context:
        context.prec = 60
        if request.function == "difference":
            result = values["actual"] - values["target"]
        elif request.function == "attainment_rate":
            if values["target"] == 0:
                raise failure("CALCULATION_ZERO_DENOMINATOR", "目标值为零，不能计算达成率。")
            result = values["actual"] / values["target"] * 100
        else:
            if values["previous"] == 0:
                raise failure("CALCULATION_ZERO_DENOMINATOR", "基期为零，不能计算增长率。")
            result = (values["current"] - values["previous"]) / values["previous"] * 100
        result = result.quantize(QUANTUM, rounding=ROUND_HALF_UP)
    # Public v0.3 uses a JSON number, not Decimal text. Reject magnitude or
    # float conversion that cannot preserve the already-rounded decimal value.
    if abs(result) > MAX_VALUE or Decimal(str(float(result))) != result:
        raise failure("CALCULATION_RESULT_OUT_OF_RANGE", "计算结果超过公开数值范围或不能安全保留舍入精度。")
    return result


def authorization_error(request, context):
    permit = context.calculation_permit
    if permit is None or not permit.matches(request, request_id=context.request_id, session_id=context.session_id):
        return ApiError(code="CALCULATION_BINDING_REQUIRED", message="计算缺少本轮已校验的服务端证据授权。", retryable=False)
    return None


class LocalCalculator:
    async def run(self, request: CalculationRequest, context) -> Calculation:
        request = validate_request(request)
        error = authorization_error(request, context)
        if error:
            raise ToolFailure(error)
        await asyncio.sleep(0)  # Cooperative cancellation; arithmetic is bounded.
        result = calculate_decimal(request)
        return Calculation(calculation_id=request.calculation_id, formula=FORMULAS[request.function],
            inputs=list(dict.fromkeys([request.formula_ref, *(item.source_ref for item in request.operands.values())])),
            result=float(result), unit=request.result_unit)


def create_local_calculator_spec(calculator: LocalCalculator | None = None, *, timeout_ms: int = 5000):
    from ..runtime.models import ToolSpec

    async def preflight(request, context):
        return authorization_error(request, context)

    async def verify_output(result, context):
        request = context.calculation_permit._authorized_request()
        if result.formula != FORMULAS[request.function] or Decimal(str(result.result)) != calculate_decimal(request):
            raise ValueError("calculator output is not the approved arithmetic result")
        return result

    return ToolSpec(name="calculator.calculate", capability="calculate", input_model=CalculationRequest,
        output_model=Calculation, handler=(calculator or LocalCalculator()).run, timeout_ms=timeout_ms, before_call=preflight, after_call=verify_output)
