import unittest

from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


class AllDocumentsRagTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.integration = await create_canonical_knowledge_integration()
        self.service = self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        self.assertTrue(await self.integration.aclose(grace_seconds=3))

    async def ask(self, question):
        return await self.service.ask(
            AskRequest(question=question, profile_id="chinook-music", session_id="rag-all"),
            request_id="rag-all",
        )

    async def test_each_document_route_has_traceable_evidence(self):
        cases = [
            ("销售额指标定义是什么？", "D01"),
            ("销售分析方法是什么？", "D02"),
            ("音乐分类和商品范围是什么？", "D03"),
            ("客户如何分层？", "D04"),
            ("商品推荐与选品规则是什么？", "D05"),
            ("2025年第三季度经营目标是什么？", "D06"),
            ("2025年第二季度经营复盘是什么？", "D07"),
            ("2025年第三季度经营复盘是什么？", "D08"),
            ("Rock品类运营规则是什么？", "D09"),
            ("音乐主题活动方案是什么？", "D10"),
            ("活动报名截止时间是什么？", "D11"),
            ("默认音乐范围是什么？", "D12"),
        ]
        for question, doc_id in cases:
            with self.subTest(question=question):
                response = await self.ask(question)
                self.assertEqual(response.route, "rag")
                self.assertIn(doc_id, {chunk.doc_id for chunk in response.documents})
                self.assertTrue(response.documents[0].source_uri.startswith("data/knowledge/"))
                self.assertEqual(response.trace[0].tool, "rag.retrieve")

    async def test_unknown_document_question_has_no_fake_answer(self):
        response = await self.ask("火星移民政策的审批流程是什么？")
        self.assertEqual(response.status, "insufficient_evidence")
        self.assertEqual(response.documents, [])

    async def test_metric_definition_prioritizes_the_definition_document(self):
        response = await self.ask("销售额口径是什么？")

        self.assertEqual(response.status, "answered")
        self.assertEqual(response.documents[0].doc_id, "D01")
        self.assertIn("销售额", response.answer)


if __name__ == "__main__":
    unittest.main()
