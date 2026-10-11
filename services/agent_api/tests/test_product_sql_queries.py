import json
import unittest
from unittest.mock import patch

from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
from services.agent_api.app.profiles import ProfileRegistry


class ProductSqlQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_nullable_date_join_cannot_sum_all_history(self):
        class NullableDateJoinModel:
            def generate_json(self, messages, output_schema, deadline):
                return json.dumps({"sql": "SELECT mt.MediaTypeId, mt.Name, SUM(il.UnitPrice*il.Quantity) AS sales_amount, SUM(il.Quantity) AS units_sold FROM main.MediaType mt LEFT JOIN main.Track t ON t.MediaTypeId=mt.MediaTypeId LEFT JOIN main.InvoiceLine il ON il.TrackId=t.TrackId LEFT JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId AND i.InvoiceDate>=:start_date AND i.InvoiceDate<:end_date GROUP BY mt.MediaTypeId,mt.Name ORDER BY mt.MediaTypeId", "params": {"start_date": "2025-07-01", "end_date": "2025-10-01"}})

        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=NullableDateJoinModel()):
            live = await create_canonical_knowledge_integration(model_mode="live")
        try:
            result = await live.create_formal_graph_service().ask(AskRequest(question="2025年第三季度各媒体类型的销售额和销量是多少？", profile_id="chinook-music"), request_id="nullable-date-test")
            self.assertEqual(result.status, "answered")
            rows = result.sql_results[0].rows
            self.assertEqual(len(rows), 2)
            self.assertAlmostEqual(rows[0]["sales_amount"], 111.87, places=2)
            self.assertEqual(rows[0]["units_sold"], 113)
            self.assertTrue(any("可空订单连接风险" in text for text in result.limitations))
        finally:
            self.assertTrue(await live.aclose(grace_seconds=3))

    async def asyncSetUp(self):
        self.integration = await create_canonical_knowledge_integration(model_mode="offline")
        self.service = self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        self.assertTrue(await self.integration.aclose(grace_seconds=3))

    async def test_q3_audio_multi_metric_question_returns_database_answer(self):
        response = await self.service.ask(
            AskRequest(
                question="2025年第三季度音频销售额、销量、订单数和购买客户数分别是多少？",
                profile_id="chinook-music",
            ),
            request_id="product-multi-metric",
        )

        self.assertEqual(response.status, "answered")
        self.assertEqual(response.route, "sql")
        self.assertEqual(response.sql_results[0].rows, [{
            "sales_amount": 112.86,
            "units_sold": 114,
            "order_count": 21,
            "purchasing_customers": 19,
        }])
        self.assertIn("112.86", response.answer)
        self.assertIn("114", response.answer)
        self.assertIn("21", response.answer)
        self.assertIn("19", response.answer)

    async def test_valid_live_sql_variant_is_rendered_instead_of_discarded(self):
        class ValidSqlVariantModel:
            def generate_json(self, messages, output_schema, deadline):
                return json.dumps({
                    "sql": "SELECT COUNT(CustomerId) AS customer_count FROM main.Customer",
                    "params": {},
                })

        profile = ProfileRegistry.defaults().get("chinook-music")
        profile.sql_backend.model_mode = "live"
        profiles = ProfileRegistry([profile])
        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=ValidSqlVariantModel()):
            live_integration = await create_canonical_knowledge_integration(
                profiles=profiles,
                model_mode="live",
            )
        try:
            response = await live_integration.create_formal_graph_service().ask(
                AskRequest(
                    question="客户总数是多少？",
                    profile_id="chinook-music",
                ),
                request_id="product-live-sql-variant",
            )

            self.assertEqual(response.status, "answered")
            self.assertEqual(response.sql_results[0].rows, [{"customer_count": 59}])
            self.assertIn("59", response.answer)
            self.assertIn("大模型生成 SQL", response.answer)
            self.assertIn("受控查询计划", response.answer)
            self.assertNotIn("样例库 + 大模型生成 SQL", response.answer)
            self.assertFalse(any("固定三问" in item or "C 离线模型仅支持" in item for item in response.limitations))
        finally:
            self.assertTrue(await live_integration.aclose(grace_seconds=3))

    async def test_live_model_cannot_drop_rock_scope_from_cross_source_calculation(self):
        class MissingGenreFilterModel:
            def generate_json(self, messages, output_schema, deadline):
                return json.dumps({
                    "sql": (
                        "SELECT ROUND(COALESCE(SUM(il.UnitPrice * il.Quantity), 0), 2) AS sales_amount "
                        "FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId "
                        "JOIN main.Track t ON t.TrackId=il.TrackId "
                        "WHERE i.InvoiceDate >= :start_date AND i.InvoiceDate < :end_date "
                        "AND t.MediaTypeId IN (1,2,4,5)"
                    ),
                    "params": {"start_date": "2025-07-01", "end_date": "2025-10-01"},
                })

        profile = ProfileRegistry.defaults().get("chinook-music")
        profile.sql_backend.model_mode = "live"
        profiles = ProfileRegistry([profile])
        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=MissingGenreFilterModel()):
            live_integration = await create_canonical_knowledge_integration(
                profiles=profiles,
                model_mode="live",
            )
        try:
            response = await live_integration.create_formal_graph_service().ask(
                AskRequest(
                    question="2025年第三季度 Rock 销售额达到目标了吗？",
                    profile_id="chinook-music",
                ),
                request_id="product-live-rock-attainment",
            )

            self.assertEqual(response.status, "answered")
            self.assertEqual(response.sql_results[0].rows, [{"sales_amount": 47.52}])
            self.assertAlmostEqual(response.calculations[0].result, 95.04, places=2)
            self.assertIn("Rock", response.answer)
            self.assertIn("达成率：95.04", response.answer)
            self.assertNotIn("人工测试", response.answer)
        finally:
            self.assertTrue(await live_integration.aclose(grace_seconds=3))


if __name__ == "__main__":
    unittest.main()
