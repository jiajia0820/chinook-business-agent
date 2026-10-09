"""HTTP-level tests for the minimal FastAPI service."""

from __future__ import annotations

import unittest

from httpx import ASGITransport, AsyncClient, Response

from services.agent_api.app.contracts import AskRequest, AskResponse
from services.agent_api.app.core.config import Settings
from services.agent_api.app.main import create_app


class SuccessfulAskService:
    async def ask(self, payload: AskRequest, *, request_id: str) -> AskResponse:
        return AskResponse(
            request_id=request_id,
            session_id=payload.session_id or "session-test",
            profile_id=payload.profile_id,
            status="answered",
            answer="测试回答",
            route="sql",
            intent={"name": "query", "confidence": 1.0},
            entities=[],
            time_range=None,
            sql_results=[],
            documents=[],
            calculations=[],
            metric_definitions=[],
            limitations=[],
            clarification=None,
            trace=[],
            error=None,
        )


class BrokenAskService:
    async def ask(self, payload: AskRequest, *, request_id: str) -> AskResponse:
        del payload, request_id
        raise RuntimeError("sensitive internal detail")


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def create_app(self, ask_service=None):
        return create_app(
            settings=Settings(cors_origins=("http://localhost:5173",), backend_mode="unavailable"),
            ask_service=ask_service,
        )

    async def request(
        self,
        app,
        method: str,
        path: str,
        *,
        raise_app_exceptions: bool = True,
        **kwargs,
    ) -> Response:
        transport = ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    async def test_health_endpoint(self) -> None:
        response = await self.request(self.create_app(), "GET", "/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "agent-api")
        # 健康检查必须暴露当前模型模式，避免再次静默退回离线替身而无人察觉。
        self.assertIn(payload["model_mode"], {"offline", "live", None})
        self.assertTrue(response.headers["X-Request-ID"].startswith("req-"))

    async def test_incoming_request_id_is_preserved(self) -> None:
        response = await self.request(
            self.create_app(),
            "GET",
            "/health",
            headers={"X-Request-ID": "req-client-001"},
        )

        self.assertEqual(response.headers["X-Request-ID"], "req-client-001")

    async def test_valid_ask_reports_service_not_configured(self) -> None:
        response = await self.request(
            self.create_app(),
            "POST",
            "/api/v1/ask",
            json={"question": "查询销售额", "profile_id": "chinook-ops"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "PROFILE_UNAVAILABLE")
        self.assertNotIn("traceback", response.text.lower())

    async def test_invalid_request_uses_structured_error(self) -> None:
        response = await self.request(
            self.create_app(),
            "POST",
            "/api/v1/ask",
            json={"question": "   ", "profile_id": "chinook-ops"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "INVALID_REQUEST")
        self.assertIsInstance(response.json()["details"], list)

    async def test_injected_service_returns_contract_response(self) -> None:
        response = await self.request(
            self.create_app(SuccessfulAskService()),
            "POST",
            "/api/v1/ask",
            headers={"X-Request-ID": "req-test-001"},
            json={"question": "查询销售额", "profile_id": "chinook-ops"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request_id"], "req-test-001")
        self.assertEqual(response.json()["status"], "answered")

    async def test_unexpected_exception_is_sanitized(self) -> None:
        with self.assertLogs("agent_api", level="ERROR"):
            response = await self.request(
                self.create_app(BrokenAskService()),
                "POST",
                "/api/v1/ask",
                raise_app_exceptions=False,
                json={"question": "查询销售额", "profile_id": "chinook-ops"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "INTERNAL_ERROR")
        self.assertNotIn("sensitive internal detail", response.text)

    async def test_cors_preflight_allows_vue_development_origin(self) -> None:
        response = await self.request(
            self.create_app(),
            "OPTIONS",
            "/api/v1/ask",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://localhost:5173",
        )


if __name__ == "__main__":
    unittest.main()
