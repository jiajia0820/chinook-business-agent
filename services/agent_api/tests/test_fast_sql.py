import json
import importlib.util
import copy
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.sql_module.catalog import load_catalog
from backend.sql_module.profiles import AccessContext, Registry, validate_profile
from backend.sql_module.schema import reflect
from backend.sql_module.validation import validate_sql
from backend.sql_module.service import NLQueryService, _controlled_query
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.agent.state import BusinessTask
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.runtime import ToolContext
from services.agent_api.app.tools.models import SqlTaskRequest


class CountingModel:
    def __init__(self):
        self.calls = 0

    def generate_json(self, messages, output_schema, deadline):
        self.calls += 1
        return json.dumps({"sql": "SELECT COUNT(CustomerId) AS customer_count FROM main.Customer", "params": {}})


def request(question, metrics, slots=None, entities=None):
    return {
        "request_id": "registered-test", "profile_id": "chinook", "question": question,
        "context": {"metric_ids": metrics, "resolved_slots": slots or {}, "entities": entities or []},
        "options": {"max_rows": 50, "show_trace": True},
    }


class RegisteredSqlTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load()
        self.access = AccessContext.local(self.registry)
        self.model = CountingModel()
        self.service = NLQueryService(self.registry, self.model)
        self.slots = {"start_date": "2025-07-01", "end_date": "2025-10-01", "media_ids": [1, 2, 4, 5]}

    def run_registered(self, value, access=None):
        self.assertTrue(hasattr(self.service, "answer_registered"), "missing registered-query entry point")
        return self.service.answer_registered(value, access or self.access)

    def test_controlled_entrypoint_uses_plan_without_reparsing_question(self):
        value = request(
            "2025年第三季度销售额最高的五个账单国家是什么？",
            ["billing_country_sales", "sales_amount"],
            {**self.slots, "query_intent": {
                "dimension": "billing_country", "top_n": 5,
                "order_by": "sales_amount", "order_desc": True,
                "measures": ["sales_amount"],
            }},
        )
        result = self.service.answer_registered(value, self.access, use_controlled_plan=True)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(result["sql_results"][0]["rows"]), 5)
        self.assertEqual(result["execution_path"], "controlled_registered_query")
        self.assertEqual(self.model.calls, 0)

    def test_controlled_entrypoint_handles_natural_metric_wording(self):
        value = request(
            "2025年第三季度 Metal 卖了多少件？",
            ["units_sold"],
            {**self.slots, "query_intent": {"measures": ["units_sold"]}},
            [{"type": "genre", "id": "Genre:3", "label": "Metal"}],
        )
        result = self.service.answer_registered(value, self.access, use_controlled_plan=True)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["sql_results"][0]["rows"], [{"units_sold": 22}])
        self.assertEqual(self.model.calls, 0)

    def test_registered_scalar_is_exact_existing_plan_without_model_calls(self):
        value = request("2025年第三季度 Rock 音频销售额和销量是多少？", ["sales_amount", "units_sold"], self.slots,
                        [{"type": "genre", "id": "Genre:1", "label": "Rock"}])
        with patch.object(self.service.executor, "execute_sql", wraps=self.service.executor.execute_sql) as execute:
            result = self.run_registered(value)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(self.model.calls, 0)
        self.assertEqual(execute.call_count, 1)
        expected = _controlled_query(value["context"], value["context"]["metric_ids"])
        artifact = result["query_artifacts"][0]
        self.assertEqual((artifact["sql"], artifact["params"]), (expected["sql"], expected["params"]))
        self.assertEqual(execute.call_args.args[0]["sql"], artifact["sql"])
        self.assertEqual(result["sql_results"][0]["rows"], [{"sales_amount": 47.52, "units_sold": 48}])
        self.assertTrue(any("未调用模型" in step["summary"] for step in result["trace"]))
        self.assertFalse(any(step["tool"] == "sql.generate" for step in result["trace"]))

    def test_country_and_multiple_genres_are_retained_in_scalar_params(self):
        value = request("2025Q3 USA Rock和Metal音频订单数是多少？", ["order_count"], self.slots,
                        [{"type": "country", "id": "Country:USA", "label": "USA"},
                         {"type": "genre", "id": "Genre:1", "label": "Rock"},
                         {"type": "genre", "id": "Genre:3", "label": "Metal"}])
        result = self.run_registered(value)
        self.assertEqual(result["status"], "answered")
        params = result["query_artifacts"][0]["params"]
        self.assertEqual(params, {"start_date": "2025-07-01", "end_date": "2025-10-01", "billing_country": "USA", "genre_id_0": 1, "genre_id_1": 3})
        self.assertIn("MediaTypeId IN (1,2,4,5)", result["query_artifacts"][0]["sql"])

    def test_audio_multi_metric_retains_every_registered_measure(self):
        metrics = ["sales_amount", "units_sold", "order_count", "purchasing_customers"]
        result = self.run_registered(request("2025年第三季度音频销售额、销量、订单数和购买客户数分别是多少？", metrics, self.slots))
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["sql_results"][0]["columns"], metrics)
        self.assertEqual(result["sql_results"][0]["rows"], [{"sales_amount": 112.86, "units_sold": 114, "order_count": 21, "purchasing_customers": 19}])
        self.assertEqual(self.model.calls, 0)

    def test_catalog_attributes_use_registered_id_query_without_model(self):
        slots = {"query_intent": {"operation": "track_attributes", "track_ids": [2, 5]}}
        result = self.run_registered(request("TrackId 2、5 的商品属性、标价、媒体类型是什么？", ["track_attributes"], slots))
        self.assertEqual(result["status"], "answered")
        self.assertEqual(self.model.calls, 0)
        self.assertEqual(result["query_artifacts"][0]["params"], {"track_0": 2, "track_1": 5})
        self.assertEqual([row["TrackId"] for row in result["sql_results"][0]["rows"]], [2, 5])

    def test_only_unfiltered_customer_count_can_use_existing_registered_plan(self):
        result = self.run_registered(request("客户总数是多少？", ["customer_count"]))
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["sql_results"][0]["rows"], [{"customer_count": 59}])
        self.assertEqual(self.model.calls, 0)
        for value in (
            request("USA客户总数是多少？", ["customer_count"], entities=[{"type": "country", "id": "Country:USA", "label": "USA"}]),
            request("2025Q3客户总数是多少？", ["customer_count"], self.slots),
            request("音频客户总数是多少？", ["customer_count"], {"media_ids": [1, 2, 4, 5]}),
        ):
            with self.subTest(value=value):
                self.assertIsNone(self.run_registered(value))

    def test_unknown_question_filters_fall_back_without_executing_or_dropping_them(self):
        questions = [
            "2025Q3 VIP客户的音频销售额是多少？",
            "2025Q3仅巴黎的音频销售额是多少？",
            "2025Q3有折扣的音频销售额是多少？",
            "2025Q3扣除退款后的音频销售额是多少？",
            "2025Q3订单金额大于100美元的音频销售额是多少？",
            "2025Q3音频销售额是多少，排除Metal？",
            "2025Q3女性客户的音频销售额是多少？",
            "2025Q3美国音频销售额是多少？",
            "2025Q3的Rock和Metal音频销售额是多少？",
        ]
        with patch.object(self.service.executor, "execute_sql", wraps=self.service.executor.execute_sql) as execute:
            for question in questions:
                with self.subTest(question=question):
                    self.assertIsNone(self.run_registered(request(question, ["sales_amount"], self.slots)))
            self.assertEqual(execute.call_count, 0)
        self.assertEqual(self.model.calls, 0)

    def test_unsupported_grains_and_known_unused_slots_fall_back(self):
        for extra in (
            {"comparison_period": {"year": 2025, "quarter": 2}}, {"result_grain": "month"},
            {"playlist_id": 1}, {"track_ids": [1]},
            {"query_intent": {"dimension": "month", "measures": ["sales_amount"]}},
            {"query_intent": {"dimension": "genre", "top_n": 3}},
            {"query_intent": {"dimension": "media_type", "include_empty": True}},
            {"query_intent": {"dimension": "customer", "amount_filter": {"operator": "gte", "value": 10}}},
        ):
            with self.subTest(extra=extra):
                self.assertIsNone(self.run_registered(request("2025Q3音频销售额是多少？", ["sales_amount"], {**self.slots, **extra})))

    def test_explicit_time_media_and_catalog_ids_must_match_context(self):
        for question in ("2024Q3音频销售额是多少？", "2025Q2音频销售额是多少？", "2025年8月音频销售额是多少？", "2025Q3视频销售额是多少？"):
            with self.subTest(question=question):
                self.assertIsNone(self.run_registered(request(question, ["sales_amount"], self.slots)))
        self.assertIsNone(self.run_registered(request("TrackId 2、9的标价是什么？", ["track_attributes"],
                                                     {"query_intent": {"operation": "track_attributes", "track_ids": [2, 5]}})))

    def test_request_schema_catalog_and_access_validation_are_not_bypassed(self):
        value = request("2025Q3音频销售额是多少？", ["sales_amount"], self.slots)
        with patch("backend.sql_module.schema.reflect", wraps=reflect) as schema:
            self.assertEqual(self.run_registered(value)["status"], "answered")
            self.assertEqual(schema.call_count, 1)
        invalid = {**value, "options": {"max_rows": 0}}
        self.assertEqual(self.run_registered(invalid)["status"], "error")
        unknown = request("2025Q3音频销售额是多少？", ["unknown_metric"], self.slots)
        self.assertEqual(self.run_registered(unknown)["status"], "error")
        invalid_slot = request("2025Q3音频销售额是多少？", ["sales_amount"], {**self.slots, "unknown_filter": "VIP"})
        self.assertEqual(self.run_registered(invalid_slot)["status"], "error")
        inaccessible = AccessContext({"chinook": frozenset({"main.Customer"})})
        self.assertEqual(self.run_registered(value, inaccessible)["status"], "error")
        self.assertEqual(self.model.calls, 0)

    def test_registered_query_still_passes_readonly_validator(self):
        value = request("客户总数是多少？", ["customer_count"])
        with patch("backend.sql_module.service._controlled_query", return_value={"sql": "DELETE FROM main.Customer", "params": {}}):
            result = self.run_registered(value)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["sql_results"][0]["status"], "rejected")
        self.assertEqual(result["error"]["code"], "SQL_READ_ONLY_VIOLATION")


class RegisteredMetadataCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        original = Registry.load().profiles["chinook"]
        self.database = self.root / "Chinook.db"
        shutil.copyfile(original.connection_url().database, self.database)
        self.metrics = self.root / "metrics.json"
        shutil.copyfile(original.directory / "metrics.json", self.metrics)
        self.profile_data = copy.deepcopy(original.data)
        self.profile_data["database"]["sqlite_path"] = "Chinook.db"
        self.registry = Registry([validate_profile(self.profile_data, self.root)])
        self.access = AccessContext.local(self.registry)
        self.model = CountingModel()
        self.service = NLQueryService(self.registry, self.model)
        self.total = request("客户总数是多少？", ["customer_count"])

    def test_different_queries_share_metadata_but_every_sql_is_validated(self):
        queries = [self.total,
                   request("2025Q3音频销售额是多少？", ["sales_amount"], {"start_date": "2025-07-01", "end_date": "2025-10-01", "media_ids": [1, 2, 4, 5]}),
                   request("TrackId 2的标价是什么？", ["track_attributes"], {"query_intent": {"operation": "track_attributes", "track_ids": [2]}})]
        with patch("backend.sql_module.schema.reflect", wraps=reflect) as schema, \
             patch("backend.sql_module.service.load_catalog", wraps=load_catalog) as catalog, \
             patch("backend.sql_module.execution.validate_sql", wraps=validate_sql) as readonly:
            results = [self.service.answer_registered(value, self.access) for value in queries]
        self.assertEqual([value["status"] for value in results], ["answered"] * 3)
        self.assertEqual(schema.call_count, 1)
        self.assertEqual(catalog.call_count, 1)
        self.assertEqual(readonly.call_count, 3)
        self.assertEqual(self.model.calls, 0)

    def test_catalog_file_change_invalidates_only_catalog(self):
        with patch("backend.sql_module.schema.reflect", wraps=reflect) as schema, \
             patch("backend.sql_module.service.load_catalog", wraps=load_catalog) as catalog:
            first = self.service.answer_registered(self.total, self.access)
            updated = json.loads(self.metrics.read_text(encoding="utf-8"))
            updated["version"] = "cache-revision"
            selected = next(item for item in updated["metrics"] if item["metric_id"] == "customer_count")
            selected["definition"] = "更新后的注册客户记录数定义"
            self.metrics.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")
            second = self.service.answer_registered(self.total, self.access)
        self.assertEqual(second["status"], "answered")
        self.assertNotEqual(first["metric_definitions"], second["metric_definitions"])
        self.assertEqual(second["metric_definitions"][0]["definition"], selected["definition"])
        self.assertEqual(second["query_artifacts"][0]["metric_refs"], ["customer_count@cache-revision"])
        self.assertEqual(schema.call_count, 1)
        self.assertEqual(catalog.call_count, 2)

    def test_invalid_modified_catalog_cannot_reuse_previously_valid_definitions(self):
        self.assertEqual(self.service.answer_registered(self.total, self.access)["status"], "answered")
        updated = json.loads(self.metrics.read_text(encoding="utf-8"))
        updated["metrics"][0]["required_columns"] = ["main.Customer.NoSuchColumn"]
        self.metrics.write_text(json.dumps(updated), encoding="utf-8")
        result = self.service.answer_registered(self.total, self.access)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["sql_results"], [])

    def test_cached_access_does_not_grant_tables_to_a_later_caller(self):
        self.assertEqual(self.service.answer_registered(self.total, self.access)["status"], "answered")
        for access in (AccessContext({}), AccessContext({"chinook": frozenset({"main.Customer"})})):
            with self.subTest(access=access):
                result = self.service.answer_registered(self.total, access)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["sql_results"], [])
        self.assertEqual(self.service.answer_registered(self.total, self.access)["status"], "answered")

    def test_database_schema_change_invalidates_metadata_and_results_are_never_cached(self):
        first = self.service.answer_registered(self.total, self.access)
        with sqlite3.connect(self.database) as connection:
            connection.execute("ALTER TABLE Customer ADD COLUMN CacheNote TEXT")
            connection.execute("DELETE FROM Customer WHERE CustomerId = (SELECT MAX(CustomerId) FROM Customer)")
        connection.close()
        second = self.service.answer_registered(self.total, self.access)
        self.assertEqual(second["status"], "answered")
        self.assertEqual(second["sql_results"][0]["rows"], [{"customer_count": 58}])
        self.assertNotEqual(first["query_artifacts"][0]["schema_version"], second["query_artifacts"][0]["schema_version"])

    def test_profile_changes_and_other_profile_ids_do_not_reuse_wrong_snapshot(self):
        first = self.service.schemas.get_schema("chinook", self.access)
        modified = copy.deepcopy(self.profile_data)
        modified["database"]["display_name"] = "新数据源说明"
        self.registry.profiles["chinook"] = validate_profile(modified, self.root)
        second = self.service.schemas.get_schema("chinook", self.access)
        self.assertEqual(second["database"]["display_name"], "新数据源说明")
        self.assertNotEqual(first["schema_version"], second["schema_version"])
        modified["profile_id"] = "separate-profile"
        self.registry.profiles["separate-profile"] = validate_profile(modified, self.root)
        snapshot = self.service.schemas.get_schema("separate-profile", AccessContext.local(self.registry))
        self.assertEqual(snapshot["profile_id"], "separate-profile")

    def test_cached_objects_are_isolated_from_response_and_schema_mutation(self):
        original = self.service.schemas.get_schema("chinook", self.access)
        original["tables"].clear()
        snapshot = self.service.schemas.get_schema("chinook", self.access)
        self.assertTrue(snapshot["tables"])
        first = self.service.answer_registered(self.total, self.access)
        first["metric_definitions"][0]["source_refs"].append("untrusted-ref")
        second = self.service.answer_registered(self.total, self.access)
        self.assertNotIn("untrusted-ref", second["metric_definitions"][0]["source_refs"])

    def test_warm_cache_does_not_bypass_readonly_sql_validation(self):
        self.assertEqual(self.service.answer_registered(self.total, self.access)["status"], "answered")
        with patch("backend.sql_module.service._controlled_query", return_value={"sql": "DELETE FROM main.Customer", "params": {}}):
            result = self.service.answer_registered(self.total, self.access)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["code"], "SQL_READ_ONLY_VIOLATION")


class FastSqlIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_controlled_plan_keeps_entities_in_canonical_offline_mode(self):
        integration = await create_canonical_knowledge_integration(model_mode="offline")
        try:
            answer = await integration.create_formal_graph_service().ask(
                AskRequest(profile_id="chinook-music", question="2025年第三季度 Metal 卖了多少件？"),
                request_id="controlled-metal",
            )
            self.assertEqual(answer.status, "answered")
            self.assertEqual(answer.sql_results[0].rows, [{"units_sold": 22}])
        finally:
            await integration.aclose(grace_seconds=3)

    async def test_adapter_default_on_and_explicit_off_keep_distinct_sql_sources(self):
        model = CountingModel()
        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=model):
            integration = await create_canonical_knowledge_integration(model_mode="live")
        try:
            task = SqlTaskRequest(query_id="fast-task", question="客户总数是多少？", profile_id="chinook-music", metric_ids=["customer_count"])
            context = ToolContext(request_id="fast-sql-on", session_id="fast-test", profile_id="chinook-music")
            with patch.dict(os.environ, {}):
                os.environ.pop("AGENT_FAST_SQL", None)
                fast = await integration.sql_tool.run(task, context)
            self.assertEqual(fast.status, "success")
            self.assertEqual(model.calls, 0)
            self.assertEqual(fast.sql_results[0].sql, "SELECT COUNT(*) AS customer_count FROM main.Customer")
            self.assertTrue(any("未调用模型" in item for item in fast.limitations))
            self.assertEqual(fast.diagnostics["model_mode"], "live")
            with patch.dict(os.environ, {"AGENT_FAST_SQL": "off"}):
                live = await integration.sql_tool.run(task, context.model_copy(update={"request_id": "fast-sql-off"}))
            self.assertEqual(live.status, "success")
            self.assertEqual(model.calls, 1)
            self.assertEqual(live.sql_results[0].sql, "SELECT COUNT(CustomerId) AS customer_count FROM main.Customer")
        finally:
            self.assertTrue(await integration.aclose(grace_seconds=3))

    async def test_unknown_filter_is_given_to_existing_model_chain(self):
        class RecordingModel(CountingModel):
            def generate_json(self, messages, output_schema, deadline):
                self.question = json.loads(messages[-1]["content"])["question"]
                return super().generate_json(messages, output_schema, deadline)

        model = RecordingModel()
        with patch("backend.sql_module.generation.HTTPJSONModel.from_env", return_value=model):
            integration = await create_canonical_knowledge_integration(model_mode="live")
        try:
            question = "VIP客户总数是多少？"
            task = SqlTaskRequest(query_id="unknown-filter-task", question=question, profile_id="chinook-music", metric_ids=["customer_count"])
            context = ToolContext(request_id="unknown-filter-call", session_id="fast-test", profile_id="chinook-music")
            with patch.dict(os.environ, {"AGENT_FAST_SQL": "on"}):
                outcome = await integration.sql_tool.run(task, context)
            self.assertEqual(model.calls, 1)
            self.assertEqual(model.question, question)
            self.assertFalse(any("SQL 快路径" in item for item in outcome.limitations))
        finally:
            self.assertTrue(await integration.aclose(grace_seconds=3))

    async def test_offline_registered_sql_is_not_labelled_as_fixed_demo_model(self):
        integration = await create_canonical_knowledge_integration(model_mode="offline")
        try:
            task = SqlTaskRequest(query_id="offline-fast", question="客户总数是多少？", profile_id="chinook-music", metric_ids=["customer_count"])
            context = ToolContext(request_id="offline-fast-call", session_id="fast-test", profile_id="chinook-music")
            with patch.dict(os.environ, {"AGENT_FAST_SQL": "on"}):
                outcome = await integration.sql_tool.run(task, context)
            self.assertEqual(outcome.status, "success")
            self.assertFalse(any("离线固定三问" in item for item in outcome.limitations))
            self.assertTrue(any("未调用模型" in item for item in outcome.limitations))
        finally:
            self.assertTrue(await integration.aclose(grace_seconds=3))


class FastSqlPolicyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("services.agent_api.app.agent.fast_path"), "missing fast_path module")

    def test_shape_gate_rejects_exclusion_and_complex_business_tasks(self):
        from services.agent_api.app.agent import fast_path
        self.assertTrue(hasattr(fast_path, "fast_sql_eligible"), "missing SQL shape eligibility")
        profile = ProfileRegistry.defaults().get("chinook-music")
        task = BusinessTask(original_question="2025Q3音频销售额是多少？", normalized_question="2025Q3音频销售额是多少？",
                            intent="fact_query", route="sql", business_metric_ids=["sales_amount"])
        self.assertTrue(fast_path.fast_sql_eligible(task, profile))
        for update in ({"excluded_entities": [{"type": "genre", "id": "Genre:3"}]},
                       {"required_outputs": ["ranking"]}, {"needs_calculation": True},
                       {"slots": {"unknown_filter": "VIP"}}, {"semantic_blockers": ["scope_unknown"]}):
            with self.subTest(update=update):
                self.assertFalse(fast_path.fast_sql_eligible(task.model_copy(update=update), profile))

    def test_alias_normalization_preserves_unknown_filter_words(self):
        from services.agent_api.app.agent import fast_path
        self.assertTrue(hasattr(fast_path, "fast_sql_question"), "missing approved alias normalization")
        profile = ProfileRegistry.defaults().get("chinook-music")
        self.assertEqual(fast_path.fast_sql_question("2025Q3美国VIP客户金属销售额是多少？", profile),
                         "2025Q3USAVIP客户Metal销售额是多少？")


if __name__ == "__main__":
    unittest.main()
