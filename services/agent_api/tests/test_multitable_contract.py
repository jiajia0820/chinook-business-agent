import unittest

from backend.sql_module.query_intent import parse_query_intent, validate_intent
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
from services.agent_api.app.profiles import ProfileRegistry


class MultiTableContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.memory = SessionMemory(
            profile_id=self.profile.profile_id,
            config_version=self.profile.config_version,
        )

    def test_support_rep_distribution_routes_to_sql_contract(self):
        result = parse_query_intent(
            "2025年第三季度购买音频客户按支持负责人分布如何？",
            ["purchasing_customers"],
        )
        self.assertEqual(result["dimension"], "support_rep")
        self.assertEqual(result["measures"], ["purchasing_customers"])
        self.assertTrue(validate_intent(result))

        task = parse_question(
            "2025年第三季度购买音频客户按支持负责人分布如何？",
            self.profile,
            self.memory,
        )
        self.assertEqual(task.decision, "ready")
        self.assertEqual(task.task.route, "sql")

    def test_mixed_order_count_allows_no_period(self):
        task = parse_question(
            "数据库中同时含音频和视频明细的订单有多少张？",
            self.profile,
            self.memory,
        )
        self.assertEqual(task.decision, "ready")
        self.assertEqual(task.task.route, "sql")
        self.assertEqual(task.task.business_metric_ids, ["mixed_order_count"])

    async def test_registered_multitable_queries_execute_without_model(self):
        integration = await create_canonical_knowledge_integration(model_mode="offline")
        try:
            service = integration.create_formal_graph_service()
            support = await service.ask(
                AskRequest(
                    profile_id="chinook-music",
                    question="2025年第三季度购买音频客户按支持负责人分布如何？",
                ),
                request_id="support-distribution-contract",
            )
            self.assertEqual(support.status, "answered")
            self.assertEqual(
                support.sql_results[0].rows,
                [
                    {"support_rep": "Jane Peacock", "purchasing_customers": 8},
                    {"support_rep": "Margaret Park", "purchasing_customers": 6},
                    {"support_rep": "Steve Johnson", "purchasing_customers": 5},
                ],
            )
            mixed = await service.ask(
                AskRequest(
                    profile_id="chinook-music",
                    question="数据库中同时含音频和视频明细的订单有多少张？",
                ),
                request_id="mixed-order-contract",
            )
            self.assertEqual(mixed.status, "answered")
            self.assertEqual(mixed.sql_results[0].rows, [{"mixed_order_count": 17}])
        finally:
            await integration.aclose()


if __name__ == "__main__":
    unittest.main()
