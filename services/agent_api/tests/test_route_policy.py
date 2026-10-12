import unittest

from services.agent_api.app.agent.route_policy import route_question


class RoutePolicyTests(unittest.TestCase):
    def test_monthly_database_breakdown_is_sql(self):
        task = route_question("按月份列出2025年音频销售额和音频订单数")
        self.assertEqual(task.route, "sql")
        self.assertEqual(task.task_type, "database_fact")

    def test_multi_filter_database_fact_is_sql(self):
        task = route_question("2025年第三季度USA的Rock音频销售额、销量和订单数")
        self.assertEqual(task.route, "sql")
        self.assertGreaterEqual(set(task.requested_outputs), {"actual", "monthly_or_period_result"})

    def test_left_join_zero_sales_request_is_sql(self):
        task = route_question("所有媒体类型都列出，没有成交的显示为0")
        self.assertEqual(task.route, "sql")
        self.assertIn("include_zero", task.requested_outputs)

    def test_rule_question_with_order_words_is_rag(self):
        task = route_question("同一张订单有音频和视频时，音乐销售额应该怎么处理")
        self.assertEqual(task.route, "rag")
        self.assertEqual(task.task_type, "document_rule")

    def test_actual_target_operation_question_is_cross_source(self):
        task = route_question("第三季度Metal未达目标，活动方案把Metal放在什么位置")
        self.assertEqual(task.route, "cross_source")
        self.assertGreaterEqual(set(task.requested_outputs), {"actual", "target", "operation"})


if __name__ == "__main__":
    unittest.main()
