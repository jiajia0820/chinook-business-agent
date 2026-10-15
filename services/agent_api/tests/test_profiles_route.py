import unittest

from httpx import ASGITransport, AsyncClient

from services.agent_api.app.contracts import AskRequest, AskResponse
from services.agent_api.app.core.config import Settings
from services.agent_api.app.main import create_app


class ProfileCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_profiles_route_returns_configured_profiles_in_order(self):
        app = create_app(settings=Settings(backend_mode="unavailable"))
        app.state.profile_catalog = [
            {"profile_id": "chinook-music", "display_name": "Chinook + 非结构化知识库", "capabilities": ["sql", "rag", "calculate"], "status": "ready"},
            {"profile_id": "adventureworks", "display_name": "AdventureWorks PostgreSQL", "capabilities": ["sql"], "status": "unavailable"},
        ]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/profiles")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["profile_id"] for item in response.json()], ["chinook-music", "adventureworks"])
        self.assertEqual(response.json()[1]["status"], "unavailable")

    async def test_profiles_route_returns_empty_catalog_when_no_backend_is_configured(self):
        app = create_app(settings=Settings(backend_mode="unavailable"))
        app.state.profile_catalog = []

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/profiles")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])


if __name__ == "__main__":
    unittest.main()
