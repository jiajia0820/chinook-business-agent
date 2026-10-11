import asyncio
import unittest

from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


class SessionSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.integration = await create_canonical_knowledge_integration()
        self.service = self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        self.assertTrue(await self.integration.aclose(grace_seconds=3))

    async def ask(self, q, session="safe"):
        return await self.service.ask(AskRequest(question=q, profile_id="chinook-music", session_id=session), request_id="safety-" + session)

    async def test_cancel_does_not_restore_pending_or_call(self):
        await self.ask("第三季度Rock销售额是多少？")
        await self.ask("取消")
        result = await self.ask("按2025年。")
        self.assertEqual(result.status, "clarification_required")
        self.assertEqual(result.trace, [])
        self.assertEqual(result.documents, [])
        self.assertEqual(result.calculations, [])

    async def test_parallel_sessions_do_not_share_conditions(self):
        await self.ask("第三季度Rock销售额是多少？", "one")
        await self.ask("客户怎么分层？", "two")
        first, second = await asyncio.gather(self.ask("按2025年。", "one"), self.ask("按2025年第三季度。", "two"))
        self.assertEqual(first.route, "sql")
        self.assertEqual(second.route, "rag")
        self.assertNotIn("Rock", second.entities)
        self.assertEqual(first.documents, [])
        self.assertTrue(second.documents)

    async def test_foreign_profile_does_not_inherit(self):
        await self.ask("第三季度Rock销售额是多少？")
        result = await self.service.ask(AskRequest(question="按2025年。", profile_id="not-configured", session_id="safe"), request_id="foreign")
        self.assertEqual(result.status, "error")
        self.assertEqual(result.trace, [])
        self.assertEqual(result.documents, [])
