import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.core.errors import ApplicationError
from services.agent_api.app.integrations.c_offline import DATABASE_SHA256, VENDOR_ROOT, create_offline_sql_integration, verify_vendor
from services.agent_api.app.main import create_app
from services.agent_api.app.core.config import Settings
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.runtime import ToolContext
from services.agent_api.app.tools.models import SqlTaskRequest


def database_hash():
    with (VENDOR_ROOT / "data/chinook/Chinook.db").open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class COfflineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.integration = await create_offline_sql_integration()
        self.service = self.integration.create_graph_service()

    async def asyncTearDown(self):
        self.assertTrue(await self.integration.aclose(grace_seconds=3))
        self.assertEqual(database_hash(), DATABASE_SHA256)

    async def ask(self, question, request_id="req-real", **kwargs):
        return await self.service.ask(AskRequest(question=question, profile_id="chinook-music", session_id="session-real", **kwargs), request_id=request_id)

    def task(self, question, **kwargs):
        return SqlTaskRequest(query_id="task-real", profile_id="chinook-music", question=question, **kwargs)

    def context(self, request_id="req-real"):
        return ToolContext(request_id=request_id, session_id="session-real", profile_id="chinook-music")

    async def test_customer_count_real_database_and_source(self):
        response = await self.ask("客户数量是多少？")
        self.assertEqual(response.status, "answered")
        self.assertEqual(response.sql_results[0].rows, [{"customer_count": 59}])
        self.assertEqual(response.sql_results[0].source.type, "database")
        self.assertEqual(response.sql_results[0].source.tables, ["main.Customer"])
        self.assertEqual(response.sql_results[0].profile_id, "chinook-music")
        self.assertEqual(response.sql_results[0].params, {})
        self.assertEqual(response.metric_definitions[0].metric_id, "customer_count")
        self.assertNotIn("受控替身", response.answer)
        self.assertTrue(any("真实 Chinook" in item for item in response.limitations))

    async def test_usa_count_uses_named_parameter(self):
        response = await self.ask("美国客户数量是多少？")
        result = response.sql_results[0]
        self.assertEqual(result.rows, [{"customer_count": 13}])
        self.assertEqual(result.params, {"country": "USA"})
        self.assertIn(":country", result.sql)
        self.assertEqual(response.entities[0]["id"], "Country:USA")

    async def test_genres_real_database_dynamic_columns(self):
        response = await self.ask("有哪些音乐类型？")
        self.assertEqual(response.intent.name, "list_genres")
        result = response.sql_results[0]
        self.assertEqual(result.row_count, 25)
        self.assertEqual(result.columns, ["genreid", "name"])
        self.assertEqual(result.rows[0], {"genreid": 1, "name": "Rock"})
        self.assertEqual(result.source.tables, ["main.Genre"])
        self.assertEqual(response.metric_definitions, [])
        self.assertTrue(any("含影视分类" in item for item in response.limitations))

    async def test_genres_truncated_at_requested_max_rows(self):
        response = await self.ask("有哪些音乐类型？", options={"max_rows": 2})
        self.assertEqual(response.sql_results[0].row_count, 2)
        self.assertTrue(response.sql_results[0].truncated)
        self.assertEqual(len(response.sql_results[0].rows), 2)

    async def test_explicit_aliases_map_without_changing_entity(self):
        for question, value in [("客户总数是多少？", 59), ("登记客户数是多少", 59), ("美国客户总数是多少？", 13), ("来自美国的客户有多少？", 13)]:
            with self.subTest(question=question):
                response = await self.ask(question)
                self.assertEqual(response.sql_results[0].rows[0]["customer_count"], value)
        response = await self.ask("音乐类型有哪些？")
        self.assertEqual(response.sql_results[0].row_count, 25)

    async def test_unknown_filter_does_not_map_to_global_count(self):
        response = await self.ask("加拿大客户数量是多少？")
        self.assertEqual(response.status, "clarification_required")
        self.assertEqual(response.sql_results, [])
        self.assertEqual(response.trace, [])
        response = await self.ask("2025 年美国客户数量是多少？")
        self.assertEqual(response.status, "unsupported")
        self.assertEqual(response.sql_results, [])

    async def test_offline_sales_unsupported_has_no_fake_success(self):
        response = await self.ask("2025 Q3 Rock 销售额是多少？")
        self.assertEqual((response.status, response.route), ("unsupported", "unsupported"))
        self.assertIsNone(response.error)
        self.assertEqual(response.sql_results, [])
        self.assertEqual(response.trace[0].status, "unsupported")
        self.assertNotIn("达标", response.answer)

    async def test_rag_and_calculate_not_advertised_in_3c1_instance(self):
        self.assertEqual(self.integration.profiles.get("chinook-music").mode, "hybrid")
        self.assertEqual(self.integration.profiles.get("chinook-music").capabilities, ["sql"])
        for question in ["销售额口径是什么？", "2025 Q3 Rock 达标了吗？"]:
            with self.subTest(question=question):
                response = await self.ask(question)
                self.assertEqual(response.status, "unsupported")
                self.assertEqual(response.trace, [])

    async def test_show_trace_false_preserves_real_execution(self):
        response = await self.ask("客户数量是多少？", options={"show_trace": False})
        self.assertEqual(response.trace, [])
        self.assertEqual(response.sql_results[0].rows[0]["customer_count"], 59)

    async def test_c_internal_trace_and_raw_diagnostics_stay_internal(self):
        outcome = await self.integration.runtime.run("sql.task", self.task("客户数量是多少？", metric_ids=["customer_count"]), self.context())
        self.assertTrue(outcome.succeeded)
        self.assertEqual([step.tool for step in outcome.data.trace], ["sql.generate", "sql.query"])
        self.assertEqual(outcome.data.diagnostics["integration"]["database_sha256"], DATABASE_SHA256)
        self.assertIn("c_draft", outcome.data.diagnostics)
        response = await self.ask("客户数量是多少？")
        self.assertNotIn("diagnostics", response.model_dump_json())
        self.assertNotIn("c_draft", response.model_dump_json())

    async def test_invalid_metric_blocked_before_c_call(self):
        with patch.object(self.integration.sql_tool._service, "answer_sql", side_effect=AssertionError("must not execute")) as call:
            outcome = await self.integration.runtime.run("sql.task", self.task("客户数量是多少？", metric_ids=["sales_amount"]), self.context())
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.error.code, "INVALID_REQUEST")
        call.assert_not_called()

    async def test_invalid_slot_blocked_before_c_call(self):
        outcome = await self.integration.runtime.run("sql.task", self.task("客户数量是多少？", resolved_slots={"year": 2025}), self.context())
        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.error.code, "INVALID_REQUEST")

    async def test_wrong_request_scope_blocked(self):
        context = self.context().model_copy(update={"profile_id": "unknown"})
        outcome = await self.integration.runtime.run("sql.task", self.task("客户数量是多少？"), context)
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.error.code, "PROFILE_NOT_FOUND")

    async def test_no_http_model_or_credential_environment_used(self):
        from third_party.chinook_c.backend.sql_module.generation import HTTPJSONModel
        with patch.object(HTTPJSONModel, "from_env", side_effect=AssertionError("forbidden live model")), patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("forbidden HTTP")), patch.dict("os.environ", {"C_SQL_MODEL_API_KEY": "fixture-not-a-secret", "C_SQL_MODEL_ENDPOINT": "https://invalid.example", "C_SQL_MODEL_NAME": "not-used"}):
            response = await self.ask("客户数量是多少？")
            self.assertEqual(response.status, "answered")
            self.assertNotIn("fixture-not-a-secret", response.model_dump_json())

    async def test_read_only_rejection_from_actual_c_validator(self):
        from third_party.chinook_c.backend.sql_module.generation import DemoJSONModel
        for sql in ["UPDATE main.Customer SET Country='USA'", "DROP TABLE main.Customer", "SELECT load_extension('forbidden')", "SELECT COUNT(*) FROM main.Customer; SELECT 1"]:
            with self.subTest(sql=sql), patch.object(DemoJSONModel, "generate_json", return_value=json.dumps({"sql": sql, "params": {}})):
                response = await self.ask("客户数量是多少？")
            self.assertEqual(response.status, "error")
            self.assertEqual(response.sql_results[0].status, "rejected")
            self.assertEqual(response.sql_results[0].error.code, "SQL_READ_ONLY_VIOLATION")
            self.assertEqual(response.sql_results[0].rows, [])
            self.assertIsNone(response.answer)
        self.assertEqual(database_hash(), DATABASE_SHA256)

    async def test_restricted_server_access_not_overridden_by_user_role(self):
        restricted = await create_offline_sql_integration(trusted_tables=frozenset({"main.Customer"}))
        try:
            service = restricted.create_graph_service()
            result = await service.ask(AskRequest(question="有哪些音乐类型？", profile_id="chinook-music", user_role="admin"), request_id="req-no-privilege")
            self.assertEqual(result.status, "error")
            self.assertEqual(result.sql_results[0].error.code, "SQL_READ_ONLY_VIOLATION")
            self.assertEqual(result.sql_results[0].rows, [])
            result = await service.ask(AskRequest(question="客户数量是多少？", profile_id="chinook-music", user_role="anything"), request_id="req-allowed")
            self.assertEqual(result.sql_results[0].rows[0]["customer_count"], 59)
        finally:
            self.assertTrue(await restricted.aclose())

    async def test_database_side_sql_deadline_is_enforced(self):
        service = self.integration.sql_tool._service
        access = self.integration.sql_tool._access
        sql = "SELECT COUNT(*) AS n FROM main.Customer a CROSS JOIN main.Customer b CROSS JOIN main.Customer c CROSS JOIN main.Customer d CROSS JOIN main.Customer e CROSS JOIN main.Customer f"
        result = await self.integration.sql_tool.executor.run(lambda: service.executor.execute_sql({"query_id": "sql-budget", "profile_id": "chinook", "sql": sql, "params": {}, "timeout_ms": 1}, access))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "SQL_EXECUTION_FAILED")
        self.assertEqual(result["error"]["details"]["reason"], "TIMEOUT")
        self.assertEqual(result["rows"], [])

    async def test_real_queries_can_run_concurrently_without_shared_connections(self):
        responses = await asyncio.gather(*(self.service.ask(AskRequest(question="美国客户数量是多少？" if index % 2 else "客户数量是多少？", profile_id="chinook-music", session_id=f"session-real-{index}"), request_id=f"req-real-{index}") for index in range(8)))
        self.assertEqual(len({response.sql_results[0].query_id for response in responses}), 8)
        for index, response in enumerate(responses):
            self.assertEqual(response.sql_results[0].rows[0]["customer_count"], 13 if index % 2 else 59)
            self.assertEqual(response.request_id, f"req-real-{index}")


class COfflineStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_b_configuration_is_sanitized_startup_error(self):
        with patch.object(ProfileRegistry, "defaults", side_effect=ValueError("private configuration content")):
            with self.assertRaises(ApplicationError) as failure:
                await create_offline_sql_integration()
        self.assertEqual(failure.exception.error.code, "PROFILE_UNAVAILABLE")
        self.assertNotIn("private configuration", failure.exception.error.model_dump_json())

    async def test_invalid_factory_options_fail_safely(self):
        for options in [{"max_workers": 0}, {"timeout_ms": 0}]:
            with self.subTest(options=options), self.assertRaises(ApplicationError) as failure:
                await create_offline_sql_integration(**options)
            self.assertEqual(failure.exception.error.code, "PROFILE_UNAVAILABLE")

    async def test_missing_artifacts_fail_safely_without_creating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ApplicationError) as failure:
                await create_offline_sql_integration(vendor_root=Path(directory))
            self.assertEqual(failure.exception.status_code, 503)
            self.assertEqual(failure.exception.error.code, "PROFILE_UNAVAILABLE")
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_c_init_exception_not_exposed(self):
        with patch("services.agent_api.app.integrations.c_offline._load_backend", side_effect=RuntimeError("private-connection-string")):
            with self.assertRaises(ApplicationError) as failure:
                await create_offline_sql_integration()
        self.assertNotIn("private-connection", failure.exception.error.model_dump_json())

    async def test_missing_dependency_is_not_fixture_fallback(self):
        with patch("services.agent_api.app.integrations.c_offline._load_backend", side_effect=ImportError("fixture missing dependency")):
            with self.assertRaises(ApplicationError) as failure:
                await create_offline_sql_integration()
        self.assertEqual(failure.exception.error.details, {"reason": "DEPENDENCY_MISSING"})

    async def test_live_profile_not_accidentally_loaded(self):
        profile = ProfileRegistry.defaults().get("chinook-music")
        profile.sql_backend.model_mode = "live"
        with self.assertRaises(ApplicationError):
            await create_offline_sql_integration(profiles=ProfileRegistry([profile]))

    def test_manifest_matches_exact_handoff_and_excludes_gold(self):
        manifest = verify_vendor(VENDOR_ROOT)
        self.assertEqual(len(manifest.files), 19)
        self.assertTrue(all(not file.path.startswith(("eval/", "data/knowledge/")) for file in manifest.files))
        self.assertEqual(database_hash(), DATABASE_SHA256)

    def test_hash_change_detected_only_in_isolated_test_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "c-copy"
            shutil.copytree(VENDOR_ROOT, root)
            service = root / "backend/sql_module/service.py"
            service.write_bytes(service.read_bytes() + b"\n# test-only corruption\n")
            with self.assertRaises(ValueError):
                verify_vendor(root)

    def test_profile_aliases_are_exact_not_substring_matches(self):
        profile = ProfileRegistry.defaults().get("chinook-music")
        memory = SessionMemory(profile_id=profile.profile_id, config_version=profile.config_version)
        parsed = parse_question("加拿大客户数量是多少？", profile, memory)
        self.assertNotIn("exact_offline_alias", parsed.rules)
        parsed = parse_question("音乐类型有哪些？", profile, memory)
        self.assertEqual(parsed.task.normalized_question, "有哪些音乐类型？")
        self.assertEqual(parsed.task.business_metric_ids, [])

    def test_explicit_disabled_api_not_assembled(self):
        from fastapi.testclient import TestClient
        with TestClient(create_app(settings=Settings(backend_mode="unavailable"))) as client:
            response = client.post("/api/v1/ask", json={"question": "客户数量是多少？", "profile_id": "chinook-music"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "PROFILE_UNAVAILABLE")

    def test_manifest_omitting_safety_module_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "c-copy"
            shutil.copytree(VENDOR_ROOT, root)
            path = root / "artifact-manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["files"] = [record for record in manifest["files"] if record["path"] != "backend/sql_module/validation.py"]
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                verify_vendor(root)

    def test_driver_rejects_writes_even_with_query_only_disabled_on_test_copy(self):
        # Only an isolated temporary database copy is used for this probe.
        from sqlalchemy.engine import URL
        from sqlalchemy.exc import DBAPIError
        from third_party.chinook_c.backend.sql_module.adapters.sqlite import sqlite_engine
        from third_party.chinook_c.backend.sql_module.profiles import Registry
        profile = Registry.load(VENDOR_ROOT / "profiles").profiles["chinook"]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "readonly-probe.db"
            shutil.copyfile(VENDOR_ROOT / "data/chinook/Chinook.db", database)
            engine = sqlite_engine(URL.create("sqlite", database=str(database)), profile)
            try:
                with engine.connect() as connection:
                    self.assertEqual(connection.exec_driver_sql("PRAGMA query_only").scalar_one(), 1)
                    connection.exec_driver_sql("PRAGMA query_only=OFF")
                    with self.assertRaises(DBAPIError):
                        connection.exec_driver_sql("CREATE TABLE read_only_probe (value INTEGER)")
            finally:
                engine.dispose()
            with database.open("rb") as stream:
                self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), DATABASE_SHA256)


class COfflineDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_keeps_actual_c_worker_slot_and_discards_late_query(self):
        integration = await create_offline_sql_integration(max_workers=1)
        entered, release, later_entered = threading.Event(), threading.Event(), threading.Event()
        original = integration.sql_tool._service.answer_sql
        completed_raw_ids = []
        def delayed(request, access):
            if request["request_id"] == "req-old":
                entered.set()
                release.wait(3)
            else:
                later_entered.set()
            result = original(request, access)
            completed_raw_ids.append(result["request_id"])
            return result
        try:
            with patch.object(integration.sql_tool._service, "answer_sql", side_effect=delayed):
                old = asyncio.create_task(integration.runtime.run("sql.task", SqlTaskRequest(query_id="task-old", profile_id="chinook-music", question="客户数量是多少？", timeout_ms=80), ToolContext(request_id="req-old", profile_id="chinook-music", session_id="session-late")))
                async with asyncio.timeout(2):
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                old_outcome = await old
                self.assertEqual(old_outcome.status, "timeout")
                self.assertEqual(old_outcome.error.code, "TOOL_TIMEOUT")
                self.assertIsNone(old_outcome.data)
                self.assertEqual(integration.sql_tool.executor.in_flight, 1)
                new = asyncio.create_task(integration.runtime.run("sql.task", SqlTaskRequest(query_id="task-new", profile_id="chinook-music", question="美国客户数量是多少？"), ToolContext(request_id="req-new", profile_id="chinook-music", session_id="session-late")))
                await asyncio.sleep(0.02)
                self.assertFalse(later_entered.is_set())
                release.set()
                new_outcome = await asyncio.wait_for(new, 3)
                self.assertEqual(new_outcome.status, "success")
                self.assertEqual(new_outcome.data.query_id, "task-new")
                self.assertEqual(new_outcome.data.sql_results[0].rows, [{"customer_count": 13}])
                self.assertEqual({event.request_id for event in new_outcome.events}, {"req-new"})
                self.assertEqual(old_outcome.events[-1].status, "timeout")
                self.assertEqual(completed_raw_ids, ["req-old", "req-new"])
                self.assertIsNone(old_outcome.data)
        finally:
            release.set()
            self.assertTrue(await integration.aclose(grace_seconds=3))

    async def test_closed_adapter_is_error_not_fake_answer(self):
        integration = await create_offline_sql_integration()
        await integration.aclose()
        outcome = await integration.runtime.run("sql.task", SqlTaskRequest(query_id="task-closed", profile_id="chinook-music", question="客户数量是多少？"), ToolContext(request_id="req-closed", session_id="session-closed", profile_id="chinook-music"))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error.code, "PROFILE_UNAVAILABLE")
        self.assertIsNone(outcome.data)
