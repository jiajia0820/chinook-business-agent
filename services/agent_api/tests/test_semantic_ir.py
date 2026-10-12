import unittest

from services.agent_api.app.agent.semantic_ir import QuestionIR


class QuestionIRTests(unittest.TestCase):
    def test_ir_roundtrip_preserves_provenance(self):
        ir = QuestionIR.from_question("2025年第三季度 Rock 销售额")
        restored = QuestionIR.model_validate(ir.model_dump(mode="python"))
        self.assertEqual(restored.time_scope.quarter, 3)
        self.assertEqual(restored.entity_scope[0].entity_id, 1)
        self.assertEqual(restored.provenance["time_scope"].status, "confirmed")

    def test_ir_is_compatible_with_business_task_json(self):
        from services.agent_api.app.agent.state import BusinessTask

        ir = QuestionIR.from_question("Rock 销售额")
        task = BusinessTask(
            original_question="Rock 销售额",
            normalized_question="Rock 销售额",
            intent="fact_query",
            route="sql",
            question_ir=ir,
        )
        restored = BusinessTask.model_validate_json(task.model_dump_json())
        self.assertEqual(restored.question_ir.entity_scope[0].entity_id, 1)


if __name__ == "__main__":
    unittest.main()
