"""问题义务解析测试：只解析问题文本，不触达模型、SQL 或检索。"""

import unittest

from services.agent_api.app.agent.query_obligations import (
    QueryObligations,
    extract_query_obligations,
)


class QueryObligationTests(unittest.TestCase):
    def test_unknown_country_is_unresolved(self):
        result = extract_query_obligations("2025年第三季度英国音频销售额是多少？", "chinook-music")
        self.assertTrue(result.has_unresolved_condition)
        self.assertIn("英国", result.unresolved_terms)

    def test_running_total_requires_month_rows(self):
        result = extract_query_obligations("2025年第三季度每个月销售额累计到当月是多少？", "chinook-music")
        self.assertTrue(result.requires_month_grouping)
        self.assertTrue(result.requires_running_total)
        self.assertTrue(result.requires_multiple_rows)
        self.assertFalse(result.has_unresolved_condition)

    def test_registered_usa_is_consumed(self):
        result = extract_query_obligations("2025年第三季度 USA 音频销售额是多少？", "chinook-music")
        self.assertFalse(result.has_unresolved_condition)
        self.assertIn("Country:USA", result.consumed_entities)

    def test_rule_question_does_not_need_transaction_year(self):
        result = extract_query_obligations("客单价是按销售额除以订单数吗？", "chinook-music")
        self.assertFalse(result.requires_transaction_period)
        self.assertFalse(result.has_unresolved_condition)

    def test_registered_vocabulary_is_not_unresolved(self):
        result = extract_query_obligations("2025年第三季度音频销售额是多少？", "chinook-music")
        self.assertEqual(result.unresolved_terms, ())
        self.assertTrue(result.requires_transaction_period)
        self.assertFalse(result.requires_multiple_rows)

    def test_top_n_requires_rank_and_limit(self):
        result = extract_query_obligations("2025年第三季度音频销售额最高的五个品类是什么？", "chinook-music")
        self.assertTrue(result.requires_rank)
        self.assertEqual(result.requested_limit, 5)
        self.assertTrue(result.requires_multiple_rows)
        self.assertIn("genre", result.required_grouping)

    def test_zero_fill_requires_all_genre_rows(self):
        result = extract_query_obligations("2025年第三季度各品类音频销售额，没有成交的也要显示为0", "chinook-music")
        self.assertTrue(result.requires_multiple_rows)
        self.assertIn("genre", result.required_grouping)
        self.assertFalse(result.has_unresolved_condition)

    def test_unknown_artist_is_unresolved(self):
        result = extract_query_obligations("2025年第三季度歌手周某某的音频销售额是多少？", "chinook-music")
        self.assertTrue(result.has_unresolved_condition)
        self.assertTrue(any("周某某" in term for term in result.unresolved_terms))

    def test_dict_round_trip_keeps_obligations(self):
        original = extract_query_obligations("2025年第三季度每个月销售额累计到当月是多少？", "chinook-music")
        payload = original.to_dict()
        self.assertIsInstance(payload, dict)
        restored = QueryObligations.from_dict(payload)
        self.assertEqual(restored, original)
        self.assertEqual(QueryObligations.from_dict(None), QueryObligations())
        self.assertEqual(QueryObligations.from_dict({}), QueryObligations())


if __name__ == "__main__":
    unittest.main()
