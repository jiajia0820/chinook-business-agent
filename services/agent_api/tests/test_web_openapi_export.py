"""4A contract export only; no backend or public API changes."""

from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from services.agent_api.app.core.config import Settings
from services.agent_api.app.main import create_app
from services.agent_api.examples.export_web_openapi import main


class WebOpenApiExportTests(unittest.TestCase):
    def export(self):
        output = io.StringIO()
        with redirect_stdout(output):
            main()
        return json.loads(output.getvalue())

    def test_export_matches_current_public_openapi(self):
        self.assertEqual(self.export(), create_app(settings=Settings(backend_mode="unavailable")).openapi())

    def test_export_does_not_initialize_offline_resources(self):
        with patch("services.agent_api.app.main.create_default_backend", side_effect=AssertionError("unexpected startup")):
            self.assertEqual(self.export()["openapi"], "3.1.0")

    def test_export_does_not_read_model_config(self):
        with patch("services.agent_api.app.core.model_config.ModelConfig.from_env", side_effect=AssertionError("unexpected model read")):
            self.assertIn("AskResponse", self.export()["components"]["schemas"])

    def test_public_schema_has_no_server_or_binding_fields(self):
        properties = self.export()["components"]["schemas"]["AskRequest"]["properties"]
        self.assertEqual(set(properties), {"question", "profile_id", "session_id", "user_role", "options"})

    def test_error_and_business_response_shapes_remain_distinct(self):
        responses = self.export()["paths"]["/api/v1/ask"]["post"]["responses"]
        self.assertEqual(responses["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/AskResponse")
        for status in ("422", "500", "503"):
            self.assertEqual(responses[status]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/ApiError")
