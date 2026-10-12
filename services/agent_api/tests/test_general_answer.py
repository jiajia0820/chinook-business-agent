import unittest
import asyncio
import json

from services.agent_api.app.agent.state import BusinessTask
from services.agent_api.app.contracts import AskOptions, AskRequest
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.response.composer import FormalResponseComposer, SourcePolicy
from services.agent_api.app.response.general_answer import validate_general_answer
from services.agent_api.app.response.models import ComposeContext
from services.agent_api.app.runtime import ToolRuntime
from services.agent_api.app.services.graph_ask_service import GraphAskService

from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.task_policy import parse_c_v1


class GeneralAnswerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")

    def fresh_memory(self):
        return SessionMemory(
            profile_id=self.profile.profile_id,
            config_version=self.profile.config_version,
        )

    def test_general_knowledge_question_uses_general_route(self):
        parsed = parse_c_v1("有哪些非结构化数据集", self.profile, self.fresh_memory())

        self.assertEqual(parsed.decision, "ready")
        self.assertEqual(parsed.task.intent, "general_knowledge")
        self.assertEqual(parsed.task.route, "general")

    def test_general_definition_of_unsupported_business_metric_uses_general_route(self):
        parsed = parse_c_v1("利润是什么", self.profile, self.fresh_memory())

        self.assertEqual(parsed.decision, "ready")
        self.assertEqual(parsed.task.intent, "general_knowledge")
        self.assertEqual(parsed.task.route, "general")

    def test_business_fact_question_stays_on_sql_route(self):
        parsed = parse_c_v1("2025年第三季度音频销售额是多少", self.profile, self.fresh_memory())

        self.assertEqual(parsed.decision, "ready")
        self.assertEqual(parsed.task.intent, "fact_query")
        self.assertEqual(parsed.task.route, "sql")

    def test_legacy_profile_also_has_general_route(self):
        profile = ProfileRegistry.defaults().get("adventureworks")
        parsed = parse_question(
            "利润是什么",
            profile,
            SessionMemory(profile_id=profile.profile_id, config_version=profile.config_version),
        )

        self.assertEqual(parsed.decision, "ready")
        self.assertEqual(parsed.task.intent, "general_knowledge")
        self.assertEqual(parsed.task.route, "general")


class GeneralAnswerModelTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")

    def test_model_answer_requires_a_non_empty_answer(self):
        answer = validate_general_answer('{"answer":"文本、图片、音频和视频都可以是非结构化数据。"}')

        self.assertEqual(answer, "文本、图片、音频和视频都可以是非结构化数据。")

    def test_composer_uses_model_for_general_route(self):
        class FakeModel:
            def generate_json(self, messages, output_schema, deadline):
                self.messages = messages
                return json.dumps({"answer": "非结构化数据通常包括文本、图片、音频和视频。"}, ensure_ascii=False)

        class FakeExecutor:
            async def run(self, fn, *, deadline):
                return fn()

        model = FakeModel()
        composer = FormalResponseComposer(
            SourcePolicy(
                execution_mode="offline_sql_integration",
                profile_id=self.profile.profile_id,
                database_name=self.profile.sql_backend.display_name,
                model_mode="live",
            ),
            answer_model=model,
            executor=FakeExecutor(),
        )
        task = BusinessTask(
            original_question="有哪些非结构化数据集",
            normalized_question="有哪些非结构化数据集",
            intent="general_knowledge",
            route="general",
        )
        context = ComposeContext(
            request_id="req-general",
            session_id="session-general",
            profile=self.profile,
            status="answered",
            route="general",
            task=task,
            options=AskOptions(),
        )

        prepared = asyncio.run(composer.prepare(context))

        self.assertEqual(prepared.general_answer, "非结构化数据通常包括文本、图片、音频和视频。")

    def test_graph_marks_general_route_as_answered_without_tools(self):
        class FakeModel:
            def generate_json(self, messages, output_schema, deadline):
                return json.dumps({"answer": "非结构化数据通常包括文本、图片、音频和视频。"}, ensure_ascii=False)

        class FakeExecutor:
            async def run(self, fn, *, deadline):
                return fn()

        composer = FormalResponseComposer(
            SourcePolicy(
                execution_mode="offline_sql_integration",
                profile_id=self.profile.profile_id,
                database_name=self.profile.sql_backend.display_name,
                model_mode="live",
            ),
            answer_model=FakeModel(),
            executor=FakeExecutor(),
        )
        profiles = ProfileRegistry([self.profile])
        service = GraphAskService(
            profiles,
            ToolRuntime(profiles),
            response_composer=composer,
            execution_mode="offline_sql_integration",
        )

        response = asyncio.run(service.ask(
            AskRequest(question="有哪些非结构化数据集", profile_id=self.profile.profile_id),
            request_id="req-graph-general",
        ))

        self.assertEqual(response.status, "answered")
        self.assertEqual(response.route, "general")
        self.assertIn("非结构化数据通常包括", response.answer)



if __name__ == "__main__":
    unittest.main()
