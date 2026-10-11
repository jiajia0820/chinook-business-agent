import json
import unittest
from unittest.mock import patch

from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


class FiniteTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def test_customer_candidate_month_and_growth_real_sources(self):
        class Candidate:
            def generate_json(self, messages, output_schema, deadline):
                return json.dumps({"sql": "SELECT COUNT(*) AS customer_count FROM main.Customer", "params": {}})
        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=Candidate()):
            integration = await create_canonical_knowledge_integration(model_mode="live")
        try:
            service = integration.create_formal_graph_service()
            async def ask(question, session):
                return await service.ask(AskRequest(question=question, profile_id="chinook-music", session_id=session), request_id=session)
            customer = await ask("2025年第三季度购买Rock的客户里，哪些按客户分层规则属于A层？", "tiers")
            self.assertEqual(customer.status, "answered", (customer.answer, customer.limitations))
            self.assertIn("10、31、48", customer.answer)
            candidate = await ask("2025年第三季度TrackId 1791-1799中，按D05选品和海报要求的Rock主页面商品是否有成交？", "tracks")
            self.assertEqual(candidate.status, "answered", (candidate.answer, candidate.limitations))
            self.assertEqual({r["TrackId"] for r in candidate.sql_results[0].rows}, {1791, 1793, 1795, 1797, 1799})
            monthly = await ask("2025年第三季度全音频每月实际销售额与月度计划差多少？", "months")
            self.assertEqual(monthly.status, "answered", (monthly.answer, monthly.limitations))
            self.assertEqual(len(monthly.calculations), 6)
            self.assertEqual([c.result for c in monthly.calculations if c.unit == "USD"], [-0.38, -2.38, -4.38])
            conflict = await ask("2025年第三季度全音频每月数据库实际销售额与D06月度计划及D08复盘核对", "conflict")
            self.assertEqual(conflict.status, "insufficient_evidence", (conflict.answer, conflict.limitations))
            self.assertIn("36.63", conflict.answer, str([(c.doc_id, c.text[:100]) for c in conflict.documents]) + str(conflict.limitations))
            self.assertIn("37.62", conflict.answer)
            await ask("2025年第二季度全音频销售额是多少？", "growth")
            await ask("换成第三季度呢？", "growth")
            growth = await ask("增长率是多少？", "growth")
            self.assertEqual(growth.status, "answered", (growth.answer, growth.limitations))
            self.assertEqual(growth.calculations[0].result, 3.64)
            self.assertEqual(len(growth.sql_results), 2)
        finally:
            self.assertTrue(await integration.aclose(grace_seconds=3))
