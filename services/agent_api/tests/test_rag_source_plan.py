import unittest

from services.agent_api.app.agent.semantic_gate import decide
from services.agent_api.app.agent.semantic_ir import QuestionIR
from services.agent_api.app.knowledge.source_plan import SourcePlan, rag_source_plan


class RagSourcePlanTests(unittest.TestCase):
    def test_default_media_scope_requires_d01_d03_d12(self):
        plan = rag_source_plan("默认音乐销售额包括哪些MediaType")
        self.assertEqual(plan.required_docs, ["D01", "D03", "D12"])
        self.assertIn("media_scope", plan.required_roles)

    def test_activity_plan_requires_d10_and_d11(self):
        plan = rag_source_plan("第四季度Rock、Metal和Latin分别怎么安排")
        self.assertEqual(plan.required_docs, ["D10", "D11"])

    def test_metric_method_question_does_not_require_period(self):
        decision = decide(QuestionIR.from_question("一个月销售额有没有增长，应该和什么时间段比较"))
        self.assertEqual(decision.status, "answerable")

    def test_rule_question_with_order_media_uses_d12(self):
        plan = rag_source_plan("同一张订单同时有音频和视频，音乐销售额应该怎么处理")
        self.assertEqual(plan.required_docs, ["D12"])

    def test_authority_roles_cover_supported_documents(self):
        expected = {
            "metric_definition": ("D01", "D02"),
            "media_scope": ("D01", "D03", "D12"),
            "classification_boundary": ("D03",),
            "selection_rule": ("D05",),
            "activity_plan": ("D10", "D11"),
            "mixed_order_media_rule": ("D12",),
            "target_value": ("D06",),
            "quarter_review": ("D07", "D08"),
            "operation_rule": ("D09",),
        }
        from services.agent_api.app.knowledge.source_plan import SOURCE_ROLES
        for role, docs in expected.items():
            self.assertEqual(SOURCE_ROLES[role], docs)


if __name__ == "__main__":
    unittest.main()
