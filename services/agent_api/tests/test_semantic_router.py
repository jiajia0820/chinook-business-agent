import json
import asyncio
import unittest

from services.agent_api.app.agent.semantic_router import parse_route_proposal
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.agent.task_policy import parse_c_v1
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.response.composer import FormalResponseComposer, SourcePolicy
from services.agent_api.app.runtime import ToolRuntime
from services.agent_api.app.services.graph_ask_service import GraphAskService


class SemanticRouterContractTests(unittest.TestCase):
    def test_accepts_only_high_confidence_registered_question_type(self):
        proposal = parse_route_proposal(json.dumps({
            "question_type": "general_knowledge",
            "needs_tool": False,
            "confidence": 0.91,
        }))

        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.question_type, "general_knowledge")
        self.assertFalse(proposal.needs_tool)

    def test_rejects_low_confidence_or_unknown_route(self):
        self.assertIsNone(parse_route_proposal(json.dumps({
            "question_type": "general_knowledge", "needs_tool": False, "confidence": 0.79,
        })))
        self.assertIsNone(parse_route_proposal(json.dumps({
            "question_type": "web_search", "needs_tool": True, "confidence": 0.99,
        })))

    def test_rejects_extra_fields_and_invalid_json(self):
        self.assertIsNone(parse_route_proposal(json.dumps({
            "question_type": "general_knowledge", "needs_tool": False, "confidence": 0.91,
            "tool": "database",
        })))
        self.assertIsNone(parse_route_proposal("not-json"))

    def test_ambiguous_turn_can_be_routed_to_general_answer_without_tools(self):
        profile = ProfileRegistry.defaults().get("chinook-music")
        initial = parse_c_v1(
            "帮我解释一下这个问题",
            profile,
            SessionMemory(profile_id=profile.profile_id, config_version=profile.config_version),
        )
        self.assertEqual(initial.decision, "clarification")

        class FakeModel:
            def generate_json(self, messages, output_schema, deadline):
                if "低权限问题分类器" in messages[0]["content"]:
                    return json.dumps({
                        "question_type": "general_knowledge",
                        "needs_tool": False,
                        "confidence": 0.94,
                    })
                return json.dumps({"answer": "这是一个通用解释。"}, ensure_ascii=False)

        class FakeExecutor:
            async def run(self, fn, *, deadline):
                return fn()

        composer = FormalResponseComposer(
            SourcePolicy(
                execution_mode="offline_sql_integration",
                profile_id=profile.profile_id,
                database_name=profile.sql_backend.display_name,
                model_mode="live",
            ),
            answer_model=FakeModel(),
            executor=FakeExecutor(),
        )
        profiles = ProfileRegistry([profile])
        service = GraphAskService(
            profiles,
            ToolRuntime(profiles),
            response_composer=composer,
            execution_mode="offline_sql_integration",
        )

        response = asyncio.run(service.ask(
            AskRequest(question="帮我解释一下这个问题", profile_id=profile.profile_id),
            request_id="req-semantic-general",
        ))

        self.assertEqual(response.status, "answered")
        self.assertEqual(response.route, "general")
        self.assertIn("这是一个通用解释", response.answer)
        self.assertEqual(response.sql_results, [])
        self.assertEqual(response.documents, [])

    def test_model_router_is_skipped_for_an_explicit_business_question(self):
        profile = ProfileRegistry.defaults().get("chinook-music")

        class FakeModel:
            route_calls = 0

            def generate_json(self, messages, output_schema, deadline):
                self.route_calls += 1
                return json.dumps({
                    "question_type": "general_knowledge",
                    "needs_tool": False,
                    "confidence": 0.99,
                })

        class FakeExecutor:
            async def run(self, fn, *, deadline):
                return fn()

        model = FakeModel()
        composer = FormalResponseComposer(
            SourcePolicy(
                execution_mode="offline_sql_integration",
                profile_id=profile.profile_id,
                database_name=profile.sql_backend.display_name,
                model_mode="live",
            ),
            answer_model=model,
            executor=FakeExecutor(),
        )
        profiles = ProfileRegistry([profile])
        service = GraphAskService(
            profiles,
            ToolRuntime(profiles),
            response_composer=composer,
            execution_mode="offline_sql_integration",
        )

        asyncio.run(service.ask(
            AskRequest(question="2025年第三季度音频销售额是多少", profile_id=profile.profile_id),
            request_id="req-semantic-explicit",
        ))

        self.assertEqual(model.route_calls, 0)

    def test_failed_router_keeps_original_clarification(self):
        profile = ProfileRegistry.defaults().get("chinook-music")

        class FailingModel:
            def generate_json(self, messages, output_schema, deadline):
                raise RuntimeError("router unavailable")

        class FakeExecutor:
            async def run(self, fn, *, deadline):
                return fn()

        composer = FormalResponseComposer(
            SourcePolicy(
                execution_mode="offline_sql_integration",
                profile_id=profile.profile_id,
                database_name=profile.sql_backend.display_name,
                model_mode="live",
            ),
            answer_model=FailingModel(),
            executor=FakeExecutor(),
        )
        profiles = ProfileRegistry([profile])
        service = GraphAskService(
            profiles,
            ToolRuntime(profiles),
            response_composer=composer,
            execution_mode="offline_sql_integration",
        )

        response = asyncio.run(service.ask(
            AskRequest(question="帮我解释一下这个问题", profile_id=profile.profile_id),
            request_id="req-semantic-failed",
        ))

        self.assertEqual(response.status, "clarification_required")
        self.assertEqual(response.route, "clarification")


if __name__ == "__main__":
    unittest.main()
