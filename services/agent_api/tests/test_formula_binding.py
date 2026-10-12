import unittest
from decimal import Decimal
from pathlib import Path

from services.agent_api.app.evidence.formulas import (
    FormulaRegistry,
    bind_formula,
    evaluate_formula,
)


PROFILE_FORMULAS = Path(__file__).resolve().parents[3] / "profiles" / "chinook" / "formulas.json"


class FormulaBindingTests(unittest.TestCase):
    def setUp(self):
        self.registry = FormulaRegistry.from_profile(PROFILE_FORMULAS)

    def operand(self, *, metric="sales_amount", value="21.78", period="2025 Q3", scope=None, unit="USD", source_ref="sql-1"):
        return {
            "metric": metric,
            "value": Decimal(value),
            "period": period,
            "scope": [3] if scope is None else scope,
            "unit": unit,
            "source_ref": source_ref,
        }

    def test_registry_loads_named_formulas(self):
        self.assertEqual(
            set(self.registry.functions),
            {"target_difference", "attainment_rate", "growth_rate", "average_order_value", "monthly_sum"},
        )
        self.assertEqual(self.registry.get("target_difference").expression, "actual - target")

    def test_target_difference_requires_same_scope_period_metric_and_unit(self):
        request = bind_formula(
            self.registry,
            function="target_difference",
            actual=self.operand(),
            target=self.operand(value="24.0", source_ref="D06#P2"),
        )
        self.assertEqual(request.function, "target_difference")
        self.assertEqual(request.operands["actual"].value, Decimal("21.78"))
        self.assertEqual(evaluate_formula(request), Decimal("-2.22"))

        for changed in (
            {"period": "2025 Q2"},
            {"scope": [5]},
            {"metric": "units_sold"},
            {"unit": "件"},
        ):
            target = self.operand(value="24.0", source_ref="D06#P2")
            target.update(changed)
            with self.assertRaises(ValueError):
                bind_formula(self.registry, "target_difference", self.operand(), target=target)

    def test_growth_allows_different_periods_but_requires_same_metric_scope_and_unit(self):
        request = bind_formula(
            self.registry,
            function="growth_rate",
            current=self.operand(value="112.86", period="2025 Q3", source_ref="D08#P1"),
            previous=self.operand(value="108.90", period="2025 Q2", source_ref="D07#P1"),
        )
        self.assertEqual(evaluate_formula(request), Decimal("3.636363636363636363636363636"))

        previous = self.operand(value="108.90", period="2025 Q2", source_ref="D07#P1")
        previous["metric"] = "units_sold"
        with self.assertRaises(ValueError):
            bind_formula(self.registry, "growth_rate", current=self.operand(value="112.86"), previous=previous)

    def test_zero_denominators_fail_explicitly(self):
        with self.assertRaisesRegex(ZeroDivisionError, "target is zero"):
            evaluate_formula(
                bind_formula(
                    self.registry,
                    "attainment_rate",
                    actual=self.operand(),
                    target=self.operand(value="0", source_ref="D06#P2"),
                )
            )
        with self.assertRaisesRegex(ZeroDivisionError, "previous is zero"):
            evaluate_formula(
                bind_formula(
                    self.registry,
                    "growth_rate",
                    current=self.operand(value="10"),
                    previous=self.operand(value="0", period="2025 Q2", source_ref="D07#P1"),
                )
            )
        with self.assertRaisesRegex(ZeroDivisionError, "order_count is zero"):
            evaluate_formula(
                bind_formula(
                    self.registry,
                    "average_order_value",
                    actual=self.operand(metric="sales_amount", value="10", unit="USD"),
                    target=self.operand(metric="order_count", value="0", unit="单", source_ref="sql-2"),
                )
            )

    def test_average_order_value_uses_sales_and_order_count(self):
        request = bind_formula(
            self.registry,
            "average_order_value",
            actual=self.operand(metric="sales_amount", value="108.90", unit="USD"),
            target=self.operand(metric="order_count", value="19", unit="单", source_ref="sql-2"),
        )
        self.assertEqual(evaluate_formula(request), Decimal("5.731578947368421052631578947"))

    def test_monthly_sum_adds_decimal_operands(self):
        months = [
            self.operand(metric="sales_amount", value="38.00", period="2025-07", source_ref="D06#P2#07"),
            self.operand(metric="sales_amount", value="40.00", period="2025-08", source_ref="D06#P2#08"),
            self.operand(metric="sales_amount", value="42.00", period="2025-09", source_ref="D06#P2#09"),
        ]
        request = bind_formula(self.registry, "monthly_sum", monthly_plan=months)
        self.assertEqual(evaluate_formula(request), Decimal("120.00"))


if __name__ == "__main__":
    unittest.main()
