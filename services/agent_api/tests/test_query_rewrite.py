import unittest

from services.agent_api.app.agent.query_rewrite import rewrite_question
from services.agent_api.app.agent.semantic_ir import QuestionIR


class QueryRewriteTests(unittest.TestCase):
    def test_rewrite_keeps_canonical_entity_and_time(self):
        ir = rewrite_question("2025年第三季度 摇滚 销售额")
        self.assertIsInstance(ir, QuestionIR)
        self.assertIn("Rock", ir.rewritten_question)
        self.assertIn("2025", ir.rewritten_question)
        self.assertEqual(ir.metrics, ["sales_amount"])

    def test_rewrite_followup_inherits_confirmed_scope(self):
        previous = rewrite_question("2025年第三季度 Rock 销售额")
        current = rewrite_question("和目标比呢？", previous=previous)
        self.assertEqual(current.time_scope.quarter, 3)
        self.assertEqual(current.time_scope.year, 2025)
        self.assertEqual(current.entity_scope[0].entity_id, 1)
        self.assertIn("target", current.requested_outputs)

    def test_rewrite_does_not_invent_missing_period(self):
        ir = rewrite_question("Rock 销售额")
        self.assertIsNone(ir.time_scope.year)
        self.assertEqual(ir.provenance["time_scope"].status, "unknown")


if __name__ == "__main__":
    unittest.main()
