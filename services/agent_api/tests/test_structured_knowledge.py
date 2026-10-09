import unittest

from services.agent_api.app.contracts import RetrievalRequest
from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry


class StructuredKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = ProfileRegistry.defaults().get("chinook-music")
        cls.index = load_knowledge_index(KNOWLEDGE_ROOT, cls.profile, max_chars=800)

    def test_d06_monthly_sales_target_returns_structured_cell(self):
        response = self.index.retrieve(RetrievalRequest(
            retrieval_id="structured-d06-september", profile_id="chinook-music",
            query="2025年9月全音频销售额计划",
            filters={
                "preferred_doc": "D06",
                "answer_type": "table_cell",
                "slots": {"year": 2025, "month": 9, "metric": "sales_amount"},
            },
            top_k=3,
        ))
        self.assertEqual(response.status, "success")
        self.assertTrue(response.chunks)
        cells = [chunk.structured_data for chunk in response.chunks if chunk.structured_data]
        self.assertTrue(any(cell["row_key"] == "2025-09" and cell["column_key"] == "sales_amount" and cell["value"] == 42.0 for cell in cells))

    def test_d06_quarter_plan_has_structured_sum_cell(self):
        response = self.index.retrieve(RetrievalRequest(
            retrieval_id="structured-d06-quarter", profile_id="chinook-music",
            query="2025年第三季度全音频月度销售额计划合计",
            filters={"preferred_doc": "D06", "answer_type": "table_cell"},
            top_k=3,
        ))
        self.assertEqual(response.status, "success")
        cells = [chunk.structured_data for chunk in response.chunks if chunk.structured_data]
        self.assertTrue(any(cell["row_key"] == "2025-Q3" and cell["column_key"] == "sales_amount" and cell["value"] == 120.0 for cell in cells))

    def test_month_question_resolves_to_a_month_time_range(self):
        memory = SessionMemory(profile_id=self.profile.profile_id, config_version=self.profile.config_version)
        parsed = parse_question("2025年9月全音频销售额计划是多少？", self.profile, memory)
        self.assertEqual(parsed.decision, "ready")
        self.assertEqual(parsed.task.time_range.start, "2025-09-01")
        self.assertEqual(parsed.task.time_range.end, "2025-10-01")


if __name__ == "__main__":
    unittest.main()
