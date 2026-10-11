import unittest
import json
from dataclasses import replace
from unittest.mock import patch

from services.agent_api.app.contracts import AskRequest, AskOptions
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


class CrossCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.integration = await create_canonical_knowledge_integration()
        self.service = self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        self.assertTrue(await self.integration.aclose(grace_seconds=3))

    async def ask(self, q, session="cross-complete", top_k=5):
        return await self.service.ask(AskRequest(question=q, profile_id="chinook-music", session_id=session, options=AskOptions(top_k=top_k)), request_id="verify")

    async def test_multiple_calculations_are_independently_bound(self):
        answer = await self.ask("2025年第三季度Rock销售额达到目标了吗？差多少？")
        self.assertEqual(answer.status, "answered", answer.limitations)
        self.assertEqual([c.result for c in answer.calculations], [95.04, -2.48])
        self.assertEqual(len({c.calculation_id for c in answer.calculations}), 2)
        self.assertIn("未达标", answer.answer)
        self.assertIn("95.04%", answer.answer)

    async def test_multidoc_preserves_response_ids_and_budget(self):
        answer = await self.ask("D03的分类边界如何影响D05选品？")
        self.assertEqual(answer.status, "answered", answer.limitations)
        self.assertEqual({c.doc_id for c in answer.documents}, {"D03", "D05"})
        self.assertEqual(len({c.retrieval_id for c in answer.documents}), 2)
        self.assertLessEqual(len(answer.documents), 5)
        self.assertTrue(all(step.tool == "rag.retrieve" for step in answer.trace))

    async def test_missing_year_no_tool_and_new_turn_clears_evidence(self):
        missing = await self.ask("第三季度全音频销售额和销量是否都达标？")
        self.assertEqual(missing.status, "clarification_required")
        self.assertEqual(missing.trace, [])
        answer = await self.ask("新问题：客户总数是多少？")
        self.assertEqual(answer.status, "answered")
        self.assertEqual(answer.documents, [])
        self.assertEqual(answer.calculations, [])

    async def test_budget_gap_is_not_answered(self):
        answer = await self.ask("D03分类与D05选品规则对比", top_k=1)
        self.assertEqual(answer.status, "insufficient_evidence")

    async def test_unregistered_target_is_not_rock_target(self):
        answer = await self.ask("2025年第三季度Heavy Metal销售额达到目标了吗？")
        self.assertNotEqual(answer.status, "answered")
        self.assertEqual(answer.calculations, [])

    async def test_non_arithmetic_cross_does_not_require_calculator(self):
        answer = await self.ask("2025年第三季度Rock销售额是多少，运营手册建议关注什么？")
        self.assertEqual(answer.status, "answered", answer.limitations)
        self.assertEqual(answer.route, "cross_source")
        self.assertEqual(answer.calculations, [])
        self.assertIn("47.52", answer.answer)
        self.assertIn("D09", {chunk.doc_id for chunk in answer.documents})

    async def test_top_genre_result_reaches_document_query(self):
        class CandidateModel:
            def generate_json(self, messages, output_schema, deadline):
                # Registered Top-N correction still uses the real read-only DB.
                return json.dumps({"sql": "SELECT COUNT(*) AS customer_count FROM main.Customer", "params": {}})

        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=CandidateModel()):
            integration = await create_canonical_knowledge_integration(model_mode="live")
        spec = integration.runtime._tools["rag.retrieve"]
        requests = []

        async def capture(request, context):
            requests.append(request)
            return await spec.handler(request, context)

        integration.runtime._tools["rag.retrieve"] = replace(spec, handler=capture)
        try:
            answer = await integration.create_formal_graph_service().ask(AskRequest(
                question="2025年第三季度销售额最高的品类是什么，运营手册建议关注什么？", profile_id="chinook-music"), request_id="rank-document")
            self.assertEqual(answer.status, "answered", answer.limitations)
            self.assertEqual(answer.sql_results[0].rows[0]["genre"], "Rock")
            self.assertTrue(any("Rock" in request.query for request in requests))
            self.assertEqual(answer.calculations, [])
            self.assertEqual(answer.trace[0].tool, "sql.task")
        finally:
            self.assertTrue(await integration.aclose(grace_seconds=3))

    async def test_all_audio_amount_and_units_use_separate_targets(self):
        class CandidateModel:
            def generate_json(self, messages, output_schema, deadline):
                # Scope/column correction will run the approved aggregate plan.
                return json.dumps({"sql": "SELECT COUNT(*) AS customer_count FROM main.Customer", "params": {}})

        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=CandidateModel()):
            integration = await create_canonical_knowledge_integration(model_mode="live")
        try:
            answer = await integration.create_formal_graph_service().ask(AskRequest(
                question="2025年第三季度全音频销售额和销量是否都达到目标？", profile_id="chinook-music"), request_id="dual-target")
            self.assertEqual(answer.status, "answered", answer.limitations)
            self.assertEqual([c.result for c in answer.calculations], [94.05, 91.2])
            self.assertEqual(len({c.calculation_id for c in answer.calculations}), 2)
            self.assertIn("sales_amount", answer.answer)
            self.assertIn("units_sold", answer.answer)
        finally:
            self.assertTrue(await integration.aclose(grace_seconds=3))

    async def test_empty_source_has_only_one_repair(self):
        spec = self.integration.runtime._tools["rag.retrieve"]
        calls = []

        async def empty(request, context):
            calls.append(request)
            result = await spec.handler(request, context)
            return result.model_copy(update={"chunks": []})

        self.integration.runtime._tools["rag.retrieve"] = replace(spec, handler=empty)
        answer = await self.ask("D03分类与D05选品规则对比")
        self.assertEqual(len(calls), 3)  # two planned sources + one repair total
        self.assertEqual(answer.status, "insufficient_evidence")
        self.assertEqual(answer.documents, [])

    async def test_tampered_snapshot_cannot_issue_calculation(self):
        spec = self.integration.runtime._tools["rag.retrieve"]

        async def corrupt(request, context):
            result = await spec.handler(request, context)
            return result.model_copy(update={"chunks": [chunk.model_copy(update={"text": chunk.text + "\nforged"}) for chunk in result.chunks]})

        self.integration.runtime._tools["rag.retrieve"] = replace(spec, handler=corrupt)
        answer = await self.ask("2025年第三季度Rock销售额达到目标了吗？")
        self.assertNotEqual(answer.status, "answered")
        self.assertEqual(answer.calculations, [])
        self.assertFalse(any(step.tool == "calculator.calculate" for step in answer.trace))
        self.assertEqual(answer.documents, [])
