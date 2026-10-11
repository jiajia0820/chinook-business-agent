import unittest
from dataclasses import replace

from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
from services.agent_api.app.knowledge.facts import fact_matches


class FactCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_snapshot_fact_completion_and_nonempty_repair(self):
        integration = await create_canonical_knowledge_integration()
        try:
            service = integration.create_formal_graph_service()
            cases = [
                ("客户如何分层？", "customer_thresholds", ["12.00", "5.00"]),
                ("客户怎么分层？", "customer_thresholds", ["12.00", "5.00"]),
                ("A层客户怎么维护？", "customer_maintenance", ["每月至少一次", "定向推荐"]),
                ("报名要交哪些材料？", "registration_materials", ["商品清单", "选品理由", "文案"]),
                ("D10和D11里的活动什么时候举行？", "activity_period", ["10月20日", "11月2日"]),
            ]
            for index, (question, role, terms) in enumerate(cases):
                result = await service.ask(AskRequest(question=question, profile_id="chinook-music", session_id=f"facts-{index}"), request_id=f"facts-{index}")
                self.assertEqual(result.status, "answered", (question, result.answer, result.limitations))
                self.assertTrue(any(fact_matches(role, c.text) for c in result.documents), role)
                for term in terms:
                    self.assertIn(term, result.answer)
                self.assertLessEqual(len(result.documents), 5)
            spec = integration.runtime._tools["rag.retrieve"]
            calls = []

            async def unrelated(request, context):
                calls.append(request)
                # Return valid, nonempty but insufficient source text.
                result = await spec.handler(request.model_copy(update={"query": "客户运营", "filters": {"doc_id": "D04"}, "top_k": 1}), context)
                return result

            integration.runtime._tools["rag.retrieve"] = replace(spec, handler=unrelated)
            result = await service.ask(AskRequest(question="客户分层的各层级阈值和维护动作是什么？", profile_id="chinook-music", session_id="facts-gap"), request_id="facts-gap")
            self.assertEqual(result.status, "insufficient_evidence")
            self.assertLessEqual(len(calls), 4)  # two fact roles + at most two repairs
            self.assertLessEqual(len(result.documents), 5)
        finally:
            self.assertTrue(await integration.aclose(grace_seconds=3))
