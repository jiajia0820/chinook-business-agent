import unittest

from services.agent_api.app.agent.semantic_gate import decide
from services.agent_api.app.agent.semantic_ir import QuestionIR
from services.agent_api.app.agent.query_rewrite import rewrite_question


class SemanticGateTests(unittest.TestCase):
    def test_document_metric_definition_is_answerable_without_period(self):
        ir = QuestionIR.from_question("客单价按销售额除以销量还是订单数？")
        decision = decide(ir)
        self.assertEqual(decision.status, "answerable")
        self.assertEqual(decision.blockers, [])

    def test_bare_target_question_asks_only_required_slots(self):
        ir = QuestionIR.from_question("销售额达到目标了吗？")
        decision = decide(ir)
        self.assertEqual(decision.status, "clarification_required")
        self.assertEqual(decision.blockers, ["period", "scope", "target_metric"])

    def test_profit_question_is_unsupported(self):
        ir = QuestionIR.from_question("第三季度利润是多少？")
        decision = decide(ir)
        self.assertEqual(decision.status, "unsupported")

    def test_multiturn_merge_preserves_scope_metric_and_output(self):
        first = rewrite_question("2025年第三季度 Rock 销售额是多少？")
        second = rewrite_question("和目标比呢？", previous=first)
        third = rewrite_question("那下一步重点看什么？", previous=second)
        self.assertEqual((third.time_scope.year, third.time_scope.quarter), (2025, 3))
        self.assertEqual([(item.entity_type, item.entity_id) for item in third.entity_scope], [("genre", 1)])
        self.assertEqual(third.metrics, ["sales_amount"])
        self.assertIn("target", third.requested_outputs)
        self.assertIn("operation_advice", third.requested_outputs)
        self.assertEqual(third.task_type, "business_rule")


if __name__ == "__main__":
    unittest.main()
