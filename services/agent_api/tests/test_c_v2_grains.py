import unittest
from pathlib import Path

from backend.sql_module.finite_grains import query
from backend.sql_module.profiles import Registry, AccessContext
from backend.sql_module.execution import SQLExecutor


class FiniteGrainTests(unittest.TestCase):
    def test_approved_grains_use_real_readonly_executor(self):
        registry = Registry.load(Path("profiles"))
        access = AccessContext({"chinook": frozenset(registry.profiles["chinook"].database["allowed_tables"])})
        executor = SQLExecutor(registry)
        context = {"metric_ids": ["sales_amount"], "resolved_slots": {"start_date": "2025-07-01", "end_date": "2025-10-01", "media_ids": [1, 2, 4, 5]}, "entities": []}
        for grain in ("month", "customer_audio", "track_set"):
            context["resolved_slots"]["result_grain"] = grain
            context["entities"] = [] if grain == "month" else [{"type": "genre", "id": "Genre:1", "label": "Rock"}]
            context["resolved_slots"]["track_ids"] = [1791, 1793, 1795, 1797, 1799]
            candidate = query(context, 50)
            result = executor.execute_sql({"query_id": grain, "profile_id": "chinook", **candidate, "max_rows": 50, "timeout_ms": 5000}, access)
            self.assertEqual(result["status"], "success", result.get("error"))
            if grain == "month":
                self.assertEqual([r["sales_amount"] for r in result["rows"]], [37.62] * 3)
            elif grain == "customer_audio":
                self.assertTrue(any(r["sales_amount"] > r["selected_genre_amount"] for r in result["rows"]))
            else:
                self.assertEqual(len(result["rows"]), 5)
                self.assertTrue(all(r["sales_amount"] > 0 for r in result["rows"]))

    def test_unknown_scope_or_oversized_set_is_rejected(self):
        base = {"metric_ids": ["track_sales"], "resolved_slots": {"result_grain": "track_set", "start_date": "2025-07-01", "end_date": "2025-10-01", "media_ids": [1, 2], "track_ids": list(range(1, 22))}}
        self.assertIsNone(query(base, 50))
