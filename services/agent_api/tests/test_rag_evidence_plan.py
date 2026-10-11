import unittest

from services.agent_api.app.agent.graph import _rag_plan


class RagEvidencePlanTests(unittest.TestCase):
    def test_cross_source_operation_plan_requires_actual_and_policy_sources(self):
        plan = _rag_plan("第三季度销售额最高的品类是什么，运营手册建议关注什么？", "cross_source_query")
        self.assertEqual(set(plan["required_sources"]), {"D08", "D09"})
        self.assertEqual(set(plan["evidence_requirements"]), {"actual", "operation_rule"})

    def test_multidoc_target_activity_plan_requires_all_three_documents(self):
        plan = _rag_plan("D06目标、D08复盘和D10活动方案之间如何衔接？", "document_rule")
        self.assertEqual(set(plan["required_sources"]), {"D06", "D08", "D10"})

    def test_growth_plan_requires_formula_and_both_review_documents(self):
        plan = _rag_plan("第三季度比第二季度增长了多少，D02建议如何解读？", "growth_rate")
        self.assertEqual(set(plan["required_sources"]), {"D01", "D02", "D07", "D08"})
        self.assertIn("formula", plan["evidence_requirements"])


if __name__ == "__main__":
    unittest.main()
