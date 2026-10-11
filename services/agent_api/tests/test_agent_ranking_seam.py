import unittest

from backend.sql_module.service import requested_top_n, _controlled_query


class RankingSeamTests(unittest.TestCase):
    def test_explicit_counts_not_year_or_row_limit(self):
        for q, expected in [("2025年第三季度最高的三个品类", 3), ("最高的五个商品", 5), ("销售额最高的品类", 1), ("Top 12", 12), ("前二十五名", 25), ("各品类销售额", None)]:
            with self.subTest(q=q):
                self.assertEqual(requested_top_n(q), expected)

    def test_registered_plan_enforces_order_and_count(self):
        context = {"resolved_slots": {"start_date": "2025-07-01", "end_date": "2025-10-01", "media_ids": [1, 2, 4, 5]}}
        plan = _controlled_query(context, ["track_sales"], candidate_sql="SELECT 1 LIMIT 50", max_rows=50, top_n=5)
        self.assertIn("sales_amount DESC", plan["sql"])
        self.assertTrue(plan["sql"].endswith("LIMIT 5"))
        self.assertEqual(plan["params"]["start_date"], "2025-07-01")
