import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from backend.sql_module.generation import HTTPJSONModel
from services.agent_api.app.core.config import Settings
from services.agent_api.app.core.model_config import ModelConfig, ModelConfigurationError
from services.agent_api.app.core.errors import ApplicationError
from services.agent_api.app.integrations.sql_d12 import create_offline_knowledge_integration


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload


class _Opener:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.request = payload, error, None

    def open(self, request, timeout):
        self.request = request
        if self.error:
            raise self.error
        return _Response(self.payload)


class LlmProviderTests(unittest.TestCase):
    def test_base_url_is_normalized_once(self):
        with patch.dict(os.environ, {
            "LLM_BASE_URL": "https://provider.example/v1/",
            "LLM_MODEL": "qwen-plus",
            "LLM_API_KEY": "secret-key",
        }, clear=False):
            config = ModelConfig.from_env()
        self.assertEqual(config.endpoint, "https://provider.example/v1/chat/completions")
        self.assertEqual(config.model_name, "qwen-plus")
        self.assertNotIn("secret-key", repr(config))

    def test_full_chat_endpoint_is_preserved(self):
        with patch.dict(os.environ, {
            "LLM_BASE_URL": "https://provider.example/v1/chat/completions",
            "LLM_MODEL": "deepseek-chat",
            "LLM_API_KEY": "secret-key",
        }, clear=False):
            config = ModelConfig.from_env()
        self.assertEqual(config.endpoint, "https://provider.example/v1/chat/completions")

    def test_missing_config_is_structured(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "", "LLM_MODEL": "", "LLM_API_KEY": ""}, clear=False):
            with self.assertRaises(ModelConfigurationError) as caught:
                ModelConfig.from_env()
        self.assertEqual(caught.exception.reason, "CONFIG_MISSING")
        self.assertNotIn("secret", str(caught.exception))

    def test_json_request_contains_model_and_json_response_format(self):
        opener = _Opener(b'{"choices":[{"message":{"content":"{\\"sql\\":\\"SELECT 1\\",\\"params\\":{}}"}}]}')
        model = HTTPJSONModel("https://provider.example/v1/chat/completions", "qwen-plus", "secret-key", opener=opener)
        content = model.generate_json([{"role": "user", "content": "question"}], {}, __import__("time").monotonic() + 5)
        self.assertIn('"sql"', content)
        self.assertEqual(opener.request.full_url, "https://provider.example/v1/chat/completions")
        body = opener.request.data.decode("utf-8")
        self.assertIn('"model": "qwen-plus"', body)
        self.assertIn('"response_format": {"type": "json_object"}', body)
        self.assertEqual(opener.request.headers["Authorization"], "Bearer secret-key")

    def test_http_failure_does_not_leak_api_key(self):
        opener = _Opener(error=HTTPError("https://provider.example/v1/chat/completions", 401, "bad", {}, None))
        model = HTTPJSONModel("https://provider.example/v1/chat/completions", "qwen-plus", "secret-key", opener=opener)
        with self.assertRaises(Exception) as caught:
            model.generate_json([], {}, __import__("time").monotonic() + 5)
        self.assertNotIn("secret-key", str(caught.exception))

    def test_settings_selects_live_mode(self):
        settings = Settings(model_mode="live")
        self.assertEqual(settings.model_mode, "live")


class LiveFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_mode_missing_config_never_falls_back_to_demo(self):
        with patch.dict(os.environ, {
            "LLM_BASE_URL": "", "LLM_MODEL": "", "LLM_API_KEY": "",
        }, clear=False):
            with self.assertRaises(ApplicationError) as caught:
                await create_offline_knowledge_integration(model_mode="live")
        self.assertEqual(caught.exception.error.code, "PROFILE_UNAVAILABLE")
        self.assertEqual(caught.exception.error.details["reason"], "CONFIG_OR_ARTIFACT_INVALID")


if __name__ == "__main__":
    unittest.main()
