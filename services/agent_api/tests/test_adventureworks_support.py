import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from services.agent_api.app.core.config import Settings
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.services.backend_lifecycle import BackendLifecycle
from services.agent_api.app.integrations.c_offline import OfflineCSqlAdapter
from services.agent_api.app.runtime import ToolContext
from services.agent_api.app.tools.models import SqlTaskRequest

ROOT = Path(__file__).resolve().parents[3]


class AdventureWorksSupportTests(unittest.TestCase):
    def test_settings_accepts_adventureworks_as_an_enabled_profile(self):
        settings = Settings(enabled_profiles=("adventureworks",))
        self.assertEqual(settings.enabled_profiles, ("adventureworks",))

    def test_settings_rejects_multiple_active_database_profiles(self):
        with self.assertRaises(ValueError):
            Settings(enabled_profiles=("chinook-music", "adventureworks"))

    def test_public_adventureworks_profile_is_sql_only_and_maps_to_backend(self):
        profile = ProfileRegistry.defaults().get("adventureworks")
        self.assertEqual(profile.mode, "sql_only")
        self.assertEqual(profile.capabilities, ["sql"])
        self.assertEqual(profile.sql_backend.profile_id, "adventureworks")
        self.assertIsNone(profile.document_root)
        self.assertIn("sales_amount", profile.sql_backend.registered_metric_ids)

    def test_backend_profile_uses_exact_quoted_schema_names(self):
        payload = json.loads((ROOT / "profiles" / "adventureworks" / "profile.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["database"]["dialect"], "postgresql")
        self.assertEqual(payload["database"]["allowed_schemas"], ["Production", "Sales"])
        self.assertIn("Sales.SalesOrderHeader", payload["database"]["allowed_tables"])
        self.assertIn("Production.Product", payload["database"]["allowed_tables"])

    def test_database_url_is_read_only_from_environment(self):
        profile = ProfileRegistry.defaults().get("adventureworks")
        with patch.dict(os.environ, {"ADVENTUREWORKS_DATABASE_URL": "postgresql://user:password@localhost/adventureworks"}, clear=False):
            self.assertEqual(profile.sql_backend.profile_id, "adventureworks")

    def test_lifecycle_starts_when_adventureworks_is_the_enabled_profile(self):
        profile = ProfileRegistry.defaults().get("adventureworks")
        profiles = ProfileRegistry([profile])

        class FakeIntegration:
            def __init__(self):
                self.profiles = profiles
                self.closed = False

            def create_formal_graph_service(self):
                return object()

            async def aclose(self, *, grace_seconds=10):
                self.closed = True
                return True

        integration = FakeIntegration()

        async def factory(_settings):
            return integration

        async def run():
            app = SimpleNamespace(state=SimpleNamespace())
            owner = BackendLifecycle(app, Settings(enabled_profiles=("adventureworks",)), factory)
            await owner.start()
            self.assertEqual(app.state.backend_state, "ready")
            await owner.close()
            self.assertTrue(integration.closed)

        import asyncio
        asyncio.run(run())

    def test_sql_adapter_accepts_adventureworks_profile(self):
        profiles = ProfileRegistry([ProfileRegistry.defaults().get("adventureworks")])

        class FakeExecutor:
            async def run(self, fn, *, deadline):
                return fn()

        class FakeService:
            def answer_sql(self, payload, _access):
                return {
                    "request_id": payload["request_id"],
                    "profile_id": "adventureworks",
                    "status": "answered",
                    "sql_results": [{
                        "query_id": "sql-aw",
                        "profile_id": "adventureworks",
                        "status": "success",
                        "columns": ["sales_amount"],
                        "rows": [{"sales_amount": "123.45"}],
                        "row_count": 1,
                        "truncated": False,
                        "execution_ms": 1,
                        "source": {"type": "database", "name": "AdventureWorks PostgreSQL", "tables": ["Sales.SalesOrderHeader"]},
                        "error": None,
                    }],
                    "query_artifacts": [{
                        "query_id": "sql-aw",
                        "sql": "SELECT 123.45 AS sales_amount",
                        "params": {},
                        "selected_tables": ["Sales.SalesOrderHeader"],
                        "schema_version": "schema-aw",
                        "metric_refs": ["sales_amount@adventureworks-business-1"],
                        "attempt_count": 1,
                    }],
                    "metric_definitions": [{
                        "metric_id": "sales_amount",
                        "name": "销售额",
                        "definition": "按明细汇总",
                        "unit": "USD",
                        "source_refs": ["AW.CATALOG"],
                    }],
                    "limitations": [],
                    "clarification": None,
                    "trace": [],
                    "error": None,
                }

        adapter = OfflineCSqlAdapter(profiles, FakeService(), {}, FakeExecutor(), {})
        task = SqlTaskRequest(
            query_id="task-aw",
            profile_id="adventureworks",
            question="2023 年销售额是多少？",
            normalized_question="sales_amount 2023",
            metric_ids=["sales_amount"],
            resolved_slots={"start_date": "2023-01-01", "end_date": "2024-01-01"},
        )

        async def run():
            return await adapter.run(task, ToolContext(request_id="req-aw", session_id="s", profile_id="adventureworks"))

        import asyncio
        outcome = asyncio.run(run())
        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.profile_id, "adventureworks")


if __name__ == "__main__":
    unittest.main()
