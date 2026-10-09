import unittest

from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


class CrossSourceCalculationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.integration = await create_canonical_knowledge_integration()
        self.service = self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        self.assertTrue(await self.integration.aclose(grace_seconds=3))

    async def test_q3_rock_attainment_binds_sql_d06_and_calculator(self):
        response = await self.service.ask(
            AskRequest(question="2025年第三季度 Rock 达到目标了吗？", profile_id="chinook-music"),
            request_id="calc-attainment",
        )
        self.assertEqual(response.status, "answered")
        self.assertEqual(response.route, "cross_source")
        self.assertAlmostEqual(response.calculations[0].result, 95.04, places=2)
        self.assertTrue(any(chunk.doc_id == "D06" for chunk in response.documents))
        self.assertTrue(response.sql_results[0].sql)
        self.assertEqual([step.tool for step in response.trace], ["sql.task", "rag.retrieve", "calculator.calculate"])

    async def test_q3_vs_q2_growth_binds_two_periods(self):
        response = await self.service.ask(
            AskRequest(question="2025年第三季度 Rock 与第二季度相比增长多少？", profile_id="chinook-music"),
            request_id="calc-growth",
        )
        self.assertEqual(response.status, "answered")
        self.assertEqual(response.route, "cross_source")
        self.assertAlmostEqual(response.calculations[0].result, 11.63, places=2)
        self.assertTrue(any(chunk.doc_id == "D01" for chunk in response.documents))


if __name__ == "__main__":
    unittest.main()
