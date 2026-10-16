from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.agent_api.app.core.config import Settings
from services.agent_api.app.services.backend_lifecycle import BackendLifecycle, create_default_backend
from services.agent_api.app.profiles import ProfileRegistry


class _FakeIntegration:
    def __init__(self, profiles):
        self.profiles = profiles
        self.closed = False

    def create_formal_graph_service(self, **_kwargs):
        return object()

    async def aclose(self, *, grace_seconds=10):
        self.closed = True
        return True


class RuntimeModeTests(unittest.TestCase):
    def test_run_mode_is_canonical_and_legacy_model_mode_is_compatible(self):
        self.assertEqual(Settings(run_mode="live").run_mode, "live")
        self.assertEqual(Settings(run_mode="live").model_mode, "live")
        self.assertEqual(Settings(run_mode="demo").model_mode, "offline")
        self.assertEqual(Settings(model_mode="offline").run_mode, "demo")

    def test_from_env_prefers_run_mode_and_defaults_to_chinook(self):
        with patch.dict(os.environ, {"AGENT_RUN_MODE": "demo", "AGENT_MODEL_MODE": "live"}, clear=False):
            settings = Settings.from_env()
        self.assertEqual(settings.run_mode, "demo")
        self.assertEqual(settings.model_mode, "offline")
        self.assertEqual(settings.enabled_profiles, ("chinook-music",))
        self.assertEqual(settings.runtime_profile_ids, ("chinook-music",))

    def test_from_env_rejects_invalid_legacy_mode(self):
        with patch.dict(os.environ, {"AGENT_RUN_MODE": "", "AGENT_MODEL_MODE": "mystery"}, clear=False):
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_default_backend_never_initializes_adventureworks(self):
        calls: list[str] = []

        async def chinook_factory(**kwargs):
            calls.append("chinook")
            selected = kwargs["profiles"]
            return _FakeIntegration(selected)

        async def adventureworks_factory(**_kwargs):
            calls.append("adventureworks")
            raise AssertionError("AdventureWorks must not enter the preliminary backend")

        settings = Settings(enabled_profiles=("chinook-music", "adventureworks"))
        with patch("services.agent_api.app.services.backend_lifecycle.create_canonical_knowledge_integration", chinook_factory), \
             patch("services.agent_api.app.services.backend_lifecycle.create_canonical_sql_integration", adventureworks_factory):
            backend = asyncio.run(create_default_backend(settings))

        self.assertEqual(calls, ["chinook"])
        self.assertEqual(backend.profiles.ids(), ("chinook-music",))

    def test_lifecycle_catalog_marks_only_chinook_ready(self):
        defaults = ProfileRegistry.defaults()
        selected = defaults.select(("chinook-music",))
        integration = _FakeIntegration(selected)

        async def factory(_settings):
            return integration

        async def run():
            app = SimpleNamespace(state=SimpleNamespace(backend_close_complete=None))
            owner = BackendLifecycle(app, Settings(), factory)
            await owner.start()
            catalog = {item["profile_id"]: item for item in app.state.profile_catalog}
            ordered_ids = [item["profile_id"] for item in app.state.profile_catalog]
            await owner.close()
            return catalog, ordered_ids

        catalog, ordered_ids = asyncio.run(run())
        self.assertEqual(ordered_ids, ["chinook-music", "adventureworks"])
        self.assertEqual(catalog["chinook-music"]["status"], "ready")
        self.assertEqual(catalog["adventureworks"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
