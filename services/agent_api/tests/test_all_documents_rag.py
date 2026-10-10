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

    async def test_alias_question_returns_authoritative_classification_document(self):
        response = await self.ask("重金属是否并入 Metal？")
        self.assertEqual(response.status, "answered")
        self.assertEqual(response.documents[0].doc_id, "D03")

    async def test_multidoc_question_returns_both_review_documents(self):
        response = await self.ask("比较 2025 年第二季度和第三季度经营复盘的核心结果")
        self.assertEqual(response.route, "rag")
        self.assertEqual({chunk.doc_id for chunk in response.documents}, {"D07", "D08"})

    async def test_multidoc_answer_is_a_conclusion_separate_from_source_chunks(self):
        response = await self.ask("结合二季度和三季度复盘，销售额、订单数和购买客户数发生了什么变化？")
        self.assertEqual(response.status, "answered")
        self.assertIn("108.90", response.answer)
        self.assertIn("112.86", response.answer)
        self.assertIn("3.96", response.answer)
        self.assertIn("19", response.answer)
        self.assertIn("21", response.answer)
        self.assertNotIn("【D07", response.answer)
        self.assertNotIn("检索到", response.answer)

    async def test_alias_answer_is_concise_and_keeps_raw_text_in_documents(self):
        response = await self.ask("重金属是否并入 Metal？")
        self.assertEqual(response.status, "answered")
        self.assertIn("Genre:3", response.answer)
        self.assertIn("Genre:13", response.answer)
        self.assertNotIn("这份说明用于处理", response.answer)
        self.assertNotIn("【D03", response.answer)
        self.assertTrue(any("Heavy Metal" in chunk.text for chunk in response.documents))

    async def test_poster_answer_is_concise_and_keeps_ocr_evidence_separate(self):
        response = await self.ask("活动报名截止时间和需要提交的材料是什么？")
        self.assertEqual(response.status, "answered")
        self.assertIn("10月13日18:00", response.answer)
        self.assertIn("商品清单", response.answer)
        self.assertNotIn("【D11", response.answer)


if __name__ == "__main__":
    unittest.main()
