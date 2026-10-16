import asyncio
import unittest

from services.agent_api.app.contracts import AskRequest, AskResponse
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.services.profile_router import ProfileRouterAskService, CompositeBackend


def response(profile_id: str, request_id: str) -> AskResponse:
    return AskResponse(
        request_id=request_id,
        session_id="session-1",
        profile_id=profile_id,
        status="answered",
        answer=f"answer:{profile_id}",
        route="sql",
        intent={"name": "query"},
        time_range=None,
        clarification=None,
        error=None,
    )


class FakeAskService:
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.calls = []

    async def ask(self, payload: AskRequest, *, request_id: str) -> AskResponse:
        self.calls.append((payload.profile_id, request_id))
        return response(self.profile_id, request_id)


class FakeIntegration:
    def __init__(self, profile_id: str, service: FakeAskService):
        self.profile_id = profile_id
        self.service = service
        self.closed = False

    def create_formal_graph_service(self):
        return self.service

    async def aclose(self, *, grace_seconds: float = 10) -> bool:
        self.closed = True
        return True


class ProfileRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_each_request_to_matching_profile_service(self):
        chinook = FakeAskService("chinook-music")
        adventureworks = FakeAskService("adventureworks")
        router = ProfileRouterAskService({
            "chinook-music": chinook,
            "adventureworks": adventureworks,
        })

        first = await router.ask(AskRequest(question="客户数量是多少？", profile_id="chinook-music"), request_id="req-c")
        second = await router.ask(AskRequest(question="订单数是多少？", profile_id="adventureworks"), request_id="req-a")

        self.assertEqual(first.answer, "answer:chinook-music")
        self.assertEqual(second.answer, "answer:adventureworks")
        self.assertEqual(chinook.calls, [("chinook-music", "req-c")])
        self.assertEqual(adventureworks.calls, [("adventureworks", "req-a")])

    async def test_unknown_profile_returns_profile_not_found_without_calling_a_backend(self):
        router = ProfileRouterAskService({"chinook-music": FakeAskService("chinook-music")})

        result = await router.ask(AskRequest(question="查询", profile_id="missing"), request_id="req-missing")

        self.assertEqual(result.status, "error")
        self.assertEqual(result.route, "unsupported")
        self.assertEqual(result.error.code, "PROFILE_NOT_FOUND")

    async def test_catalog_only_profile_returns_unavailable_without_calling_a_backend(self):
        router = ProfileRouterAskService(
            {"chinook-music": FakeAskService("chinook-music")},
            unavailable_profile_ids={"adventureworks"},
        )

        result = await router.ask(AskRequest(question="订单数是多少？", profile_id="adventureworks"), request_id="req-aw")

        self.assertEqual(result.status, "error")
        self.assertEqual(result.error.code, "PROFILE_UNAVAILABLE")
        self.assertEqual(result.profile_id, "adventureworks")

    async def test_composite_backend_closes_all_integrations(self):
        profiles = ProfileRegistry.defaults().select(("chinook-music", "adventureworks"))
        chinook = FakeIntegration("chinook-music", FakeAskService("chinook-music"))
        adventureworks = FakeIntegration("adventureworks", FakeAskService("adventureworks"))
        backend = CompositeBackend(profiles, {
            "chinook-music": chinook,
            "adventureworks": adventureworks,
        })

        self.assertEqual(backend.create_formal_graph_service().__class__.__name__, "ProfileRouterAskService")
        self.assertTrue(await backend.aclose(grace_seconds=1))
        self.assertTrue(chinook.closed)
        self.assertTrue(adventureworks.closed)


if __name__ == "__main__":
    unittest.main()
