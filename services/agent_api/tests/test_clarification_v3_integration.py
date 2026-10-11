import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from backend.sql_module.service import _controlled_query
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


class GenreUnionTests(unittest.TestCase):
    def test_parameterized_union_deduplicates_and_preserves_single_genre(self):
        context = {"resolved_slots": {"start_date": "2025-07-01", "end_date": "2025-10-01", "media_ids": [1, 2, 4, 5]},
            "entities": [{"type": "genre", "id": x} for x in ("Genre:5", "Genre:1", "Genre:5")]}
        candidate = _controlled_query(context, ["sales_amount"])
        self.assertIn("t.GenreId IN (:genre_id_0,:genre_id_1)", candidate["sql"])
        self.assertEqual(candidate["params"]["genre_id_0"], 1)
        self.assertEqual(candidate["params"]["genre_id_1"], 5)
        context["entities"] = context["entities"][:1]
        self.assertIn("t.GenreId = :genre_id", _controlled_query(context, ["sales_amount"])["sql"])


class ClarificationGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        calls = self.calls
        owner = self
        self.model_sql = "SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) AS sales_amount FROM main.InvoiceLine il JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId JOIN main.Track t ON t.TrackId=il.TrackId WHERE i.InvoiceDate>=:start_date AND i.InvoiceDate<:end_date AND t.MediaTypeId IN (1,2,3,4,5) AND t.GenreId=1 AND t.GenreId=5"

        class AdversarialCandidate:
            def generate_json(self, messages, output_schema, deadline):
                calls.append(messages)
                # Both IDs appear, but impossible AND semantics must not execute.
                return json.dumps({"sql": owner.model_sql,
                    "params": {"start_date": "2025-07-01", "end_date": "2025-10-01"}})

        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=AdversarialCandidate()):
            self.integration = await create_canonical_knowledge_integration(model_mode="live")
        self.service = self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        self.assertTrue(await self.integration.aclose(grace_seconds=3))

    async def ask(self, text):
        return await self.service.ask(AskRequest(question=text, profile_id="chinook-music", session_id="clarification-test"), request_id="test-reply")

    async def test_union_replies_gate_execution_and_query_complete_scope(self):
        for text in ("摇滚相关销售额是多少？", "Rock与Rock And Roll合并", "2025年"):
            response = await self.ask(text)
            self.assertEqual(response.status, "clarification_required")
            self.assertEqual(response.sql_results, [])
            self.assertEqual(response.calculations, [])
            self.assertEqual(self.calls, [])
        response = await self.ask("第三季度")
        self.assertEqual(response.status, "answered", response.limitations)
        self.assertEqual(len(self.calls), 1)
        result = response.sql_results[0]
        self.assertIn("GenreId IN", result.sql)
        self.assertEqual(result.params["genre_id_0"], 1)
        self.assertEqual(result.params["genre_id_1"], 5)
        with sqlite3.connect(Path("data/chinook/Chinook.db").resolve().as_uri() + "?mode=ro", uri=True) as connection:
            expected = connection.execute("SELECT ROUND(SUM(il.UnitPrice*il.Quantity),2) FROM InvoiceLine il JOIN Invoice i ON i.InvoiceId=il.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE i.InvoiceDate>=? AND i.InvoiceDate<? AND t.MediaTypeId IN (1,2,4,5) AND t.GenreId IN (1,5)", ("2025-07-01", "2025-10-01")).fetchone()[0]
        self.assertEqual(result.rows[0]["sales_amount"], expected)
        self.assertTrue(any("模型候选未用于执行" in x for x in response.limitations))

    async def test_invalid_period_and_confirmation_never_call_model(self):
        for text in ("2025Q5音频销售额是多少？", "是", "2025年"):
            response = await self.ask(text)
            self.assertEqual(response.status, "clarification_required")
            self.assertEqual(response.sql_results, [])
        self.assertEqual(self.calls, [])

    async def test_goal_choice_cannot_execute_until_explicit_metric_and_year(self):
        for text in ("第三季度Rock卖得怎么样？", "目标达成情况", "销售额"):
            response = await self.ask(text)
            self.assertEqual(response.status, "clarification_required")
            self.assertEqual(response.sql_results, [])
        self.assertEqual(self.calls, [])
        self.assertEqual(response.intent.name, "target_attainment")

    async def test_classification_capability_exit_without_model(self):
        for text in ("某商品属于什么类型？", "Genre品类"):
            self.assertEqual((await self.ask(text)).status, "clarification_required")
        response = await self.ask("TrackId 1")
        self.assertEqual(response.status, "unsupported")
        self.assertEqual(response.documents, [])
        self.assertEqual(response.sql_results, [])
        self.assertEqual(self.calls, [])

    async def test_both_goal_metrics_survive_an_invalid_model_candidate(self):
        self.model_sql = "SELECT , FROM main.InvoiceLine"
        for text in ("第三季度Rock卖得怎么样？", "目标达成情况", "销售额和销量"):
            self.assertEqual((await self.ask(text)).status, "clarification_required")
        self.assertEqual(self.calls, [])
        response = await self.ask("2025年")
        self.assertEqual(response.status, "answered", response.limitations)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(response.calculations), 2)
        self.assertEqual(response.sql_results[0].rows, [{"sales_amount": 47.52, "units_sold": 48}])
        self.assertTrue(any("多个交易指标使用受控" in text for text in response.limitations))

    async def test_direct_target_without_named_metric_has_no_execution(self):
        response = await self.ask("2025年第三季度Rock达到目标了吗？")
        self.assertEqual(response.status, "clarification_required")
        self.assertEqual([slot.name for slot in response.clarification.missing_slots], ["metric"])
        self.assertEqual(self.calls, [])
        self.assertEqual(response.sql_results, [])


if __name__ == "__main__":
    unittest.main()
