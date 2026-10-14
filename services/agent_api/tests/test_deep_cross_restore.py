"""End-to-end restoration checks; runtime never reads evaluation answers."""
import asyncio
import unittest
from dataclasses import replace
from unittest.mock import patch

from scripts.stage2_local_check import UnavailableModel
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


class DeepCrossRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch('backend.sql_module.generation.HTTPJSONModel.from_env', return_value=UnavailableModel()):
            self.integration = await create_canonical_knowledge_integration(model_mode='live')
            self.service = self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        await self.integration.aclose()

    async def test_target_reauthorization_uses_same_full_evidence(self):
        response = await self.service.ask(AskRequest(question='2025年第三季度Rock销售额达到目标了吗？差多少？', profile_id='chinook-music'), request_id='restore-target')
        self.assertEqual(response.status, 'answered', response.limitations)
        self.assertEqual({c.result for c in response.calculations}, {95.04, -2.48})
        self.assertTrue(response.sql_results)
        self.assertTrue(any(c.doc_id == 'D06' for c in response.documents))

    async def test_customer_fast_path_renders_a_verified_sql_answer(self):
        response = await self.service.ask(AskRequest(question='客户数量是多少？', profile_id='chinook-music'), request_id='restore-fast-customer')
        self.assertEqual(response.status, 'answered', response.limitations)
        self.assertEqual(response.sql_results[0].rows[0]['customer_count'], 59)

    async def test_independent_sql_and_rag_start_together(self):
        started = {name: asyncio.Event() for name in ('sql.task', 'rag.retrieve')}
        originals = {name: self.integration.runtime._tools[name] for name in started}
        for name, spec in originals.items():
            async def handler(payload, context, tool=name, original=spec.handler):
                started[tool].set()
                other = 'rag.retrieve' if tool == 'sql.task' else 'sql.task'
                await asyncio.wait_for(started[other].wait(), timeout=1)
                return await original(payload, context)
            self.integration.runtime._tools[name] = replace(spec, handler=handler)
        response = await self.service.ask(AskRequest(question='2025年第三季度Rock销售额达到目标了吗？差多少？', profile_id='chinook-music'), request_id='restore-parallel')
        self.assertTrue(all(x.is_set() for x in started.values()))
        self.assertEqual(response.status, 'answered', response.limitations)

    async def test_monthly_document_sum_uses_three_complete_months(self):
        response = await self.service.ask(AskRequest(question='2025年第三季度全音频月度销售额计划合计是多少？', profile_id='chinook-music'), request_id='restore-month-sum')
        self.assertEqual(response.status, 'answered', response.limitations)
        self.assertEqual([c.result for c in response.calculations], [120.0])
        self.assertEqual(response.sql_results, [])

    async def test_monthly_actual_preserves_reported_conflict(self):
        response = await self.service.ask(AskRequest(question='2025年第三季度每月全音频实际销售额与目标计划分别是多少？', profile_id='chinook-music'), request_id='restore-month-conflict')
        self.assertEqual(response.status, 'answered', response.limitations)
        self.assertIn('冲突', response.answer)
        self.assertIn('36.63', response.answer)
        self.assertIn('112.86', response.answer)

    async def test_independent_noncalculation_sources_start_together(self):
        started = {name: asyncio.Event() for name in ('sql.task', 'rag.retrieve')}
        for name in started:
            spec = self.integration.runtime._tools[name]
            async def handler(payload, context, tool=name, original=spec.handler):
                started[tool].set()
                other = 'rag.retrieve' if tool == 'sql.task' else 'sql.task'
                await asyncio.wait_for(started[other].wait(), timeout=1)
                return await original(payload, context)
            self.integration.runtime._tools[name] = replace(spec, handler=handler)
        response = await self.service.ask(AskRequest(question='查询2025年第三季度Rock实际销售额，并结合运营手册给出运营建议。', profile_id='chinook-music'), request_id='restore-parallel-context')
        self.assertTrue(all(x.is_set() for x in started.values()))
        self.assertEqual(response.status, 'answered', response.limitations)


if __name__ == '__main__':
    unittest.main()
