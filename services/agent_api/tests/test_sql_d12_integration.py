import asyncio
import hashlib
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from services.agent_api.app.contracts import AskRequest, DocumentChunk, RetrievalRequest, RetrievalResponse
from services.agent_api.app.core.errors import ApplicationError
from services.agent_api.app.integrations.c_offline import DATABASE_SHA256, VENDOR_ROOT
from services.agent_api.app.integrations.sql_d12 import create_offline_knowledge_integration
from services.agent_api.app.knowledge.d12 import D12_FILENAME, D12_SHA256, KNOWLEDGE_ROOT
from services.agent_api.app.main import create_app
from services.agent_api.app.core.config import Settings
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.runtime import ToolContext, ToolRuntime, ToolSpec


class SqlD12IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.integration = await create_offline_knowledge_integration()
        self.service = self.integration.create_graph_service()

    async def asyncTearDown(self):
        self.assertTrue(await self.integration.aclose(grace_seconds=3))
        self.assertEqual(hashlib.sha256((KNOWLEDGE_ROOT / D12_FILENAME).read_bytes()).hexdigest(), D12_SHA256)
        self.assertEqual(hashlib.sha256((VENDOR_ROOT / "data/chinook/Chinook.db").read_bytes()).hexdigest(), DATABASE_SHA256)

    async def ask(self, question, request_id="req-real-d12", **kwargs):
        return await self.service.ask(AskRequest(question=question, profile_id="chinook-music", session_id="session-real-d12", **kwargs), request_id=request_id)

    async def retrieve(self, query="销售额口径是什么？", **kwargs):
        payload = RetrievalRequest(retrieval_id="retrieval-direct", profile_id="chinook-music", query=query, **kwargs)
        return await self.integration.runtime.run("rag.retrieve", payload, ToolContext(request_id="r", session_id="s", profile_id="chinook-music"))

    async def test_sales_rules_use_real_d12_only_no_sql(self):
        with patch.object(self.integration.sql_tool._service, "answer_sql", side_effect=AssertionError("unexpected SQL")):
            response = await self.ask("销售额口径是什么？", options={"top_k": 1})
        self.assertEqual((response.status, response.route), ("answered", "rag"))
        self.assertEqual(response.sql_results, [])
        self.assertEqual(response.calculations, [])
        self.assertEqual(len(response.documents), 1)
        self.assertIn("四 指标怎么理解", response.documents[0].section)
        self.assertIn("SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity)", response.documents[0].text)
        self.assertEqual([step.tool for step in response.trace], ["rag.retrieve"])
        self.assertEqual(response.trace[0].source_refs, [response.documents[0].chunk_id])
        self.assertNotIn("受控替身", response.answer)
        self.assertIn("尚未接入正式", response.answer)

    async def test_default_audio_and_purchase_aliases_query_actual_source(self):
        for question, section in [("默认音乐范围是什么？", "二 默认的音乐范围"), ("默认音频范围是什么？", "二 默认的音乐范围"), ("购买客户数是什么意思？", "四 指标怎么理解")]:
            with self.subTest(question=question):
                response = await self.ask(question, options={"top_k": 1})
                self.assertEqual(response.status, "answered")
                self.assertIn(section, response.documents[0].section)
                self.assertEqual(response.route, "rag")

    async def test_registration_comparison_is_insufficient_evidence_not_fake_quote(self):
        response = await self.ask("购买客户数与登记客户记录数有什么区别？")
        self.assertEqual((response.status, response.route), ("insufficient_evidence", "rag"))
        self.assertEqual(response.documents, [])
        self.assertIsNone(response.error)
        self.assertTrue(any("未直接定义登记客户记录数" in limitation for limitation in response.limitations))
        self.assertEqual(response.trace[0].status, "success")

    async def test_missing_rule_returns_insufficient_evidence_with_empty_chunks(self):
        response = await self.ask("火星移民政策规则是什么？")
        self.assertEqual(response.status, "insufficient_evidence")
        self.assertEqual(response.documents, [])
        self.assertEqual(response.sql_results, [])

    async def test_unknown_topic_followup_does_not_reuse_previous_document_wording(self):
        first = await self.ask("销售额口径是什么？")
        self.assertEqual(first.status, "answered")
        response = await self.ask("那火星移民政策规则是什么？", request_id="req-next-topic")
        self.assertEqual(response.status, "insufficient_evidence")
        self.assertEqual(response.documents, [])

    async def test_true_sql_three_questions_unchanged_and_no_docs(self):
        for question, count in [("客户数量是多少？", 59), ("美国客户数量是多少？", 13)]:
            with self.subTest(question=question):
                response = await self.ask(question)
                self.assertEqual(response.status, "answered")
                self.assertEqual(response.sql_results[0].rows, [{"customer_count": count}])
                self.assertEqual(response.documents, [])
                self.assertEqual([step.tool for step in response.trace], ["sql.task"])
        response = await self.ask("有哪些音乐类型？")
        self.assertEqual(response.sql_results[0].row_count, 25)
        self.assertEqual(response.documents, [])

    async def test_offline_sales_cross_source_failure_stops_before_rag(self):
        with patch.object(self.integration.rag_tool.index, "retrieve", side_effect=AssertionError("unexpected RAG")):
            response = await self.ask("2025 Q3 Rock 销售额是多少，并解释口径？")
        self.assertEqual(response.status, "unsupported")
        self.assertEqual(response.documents, [])
        self.assertEqual(response.sql_results, [])
        self.assertEqual([step.tool for step in response.trace], ["sql.task"])

    async def test_sql_rag_caps_only_not_calculate_or_global_profile_mutation(self):
        profile = self.integration.profiles.get("chinook-music")
        self.assertEqual((profile.mode, profile.capabilities, profile.document_root), ("hybrid", ["sql", "rag"], "data/knowledge"))
        self.assertEqual(profile.sql_backend.profile_id, "chinook")
        self.assertEqual(ProfileRegistry.defaults().get("chinook-music").capabilities, ["sql", "rag", "calculate"])
        response = await self.ask("2025 Q3 Rock 达标了吗？")
        self.assertEqual(response.status, "unsupported")
        self.assertEqual(response.trace, [])
        self.assertEqual(response.calculations, [])

    async def test_missing_slots_do_not_invoke_either_tool(self):
        for question in ["第三季度 Rock 销售额是多少？", "摇滚相关销售额是多少？", "2025 Q3 加拿大销售额是多少？"]:
            with self.subTest(question=question):
                response = await self.ask(question)
                self.assertEqual(response.status, "clarification_required")
                self.assertEqual(response.trace, [])
                self.assertEqual(response.documents, [])

    async def test_trace_hidden_does_not_hide_provenance_or_stop_execution(self):
        response = await self.ask("销售额口径是什么？", options={"show_trace": False, "top_k": 1})
        self.assertEqual(response.trace, [])
        self.assertEqual(len(response.documents), 1)
        self.assertTrue(response.documents[0].retrieval_id)
        self.assertIsNone(response.documents[0].page)
        self.assertIsNone(response.documents[0].bbox)

    async def test_direct_runtime_top_k_and_retrieval_identity(self):
        outcome = await self.retrieve(top_k=2)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(len(outcome.data.chunks), 2)
        self.assertTrue(all(c.retrieval_id == "retrieval-direct" for c in outcome.data.chunks))
        self.assertEqual(list(outcome.source_refs), [c.chunk_id for c in outcome.data.chunks])

    async def test_unknown_or_temporal_filters_are_structured_failures(self):
        outcome = await self.retrieve(filters={"slots": {"year": 2024}})
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.error.code, "INVALID_REQUEST")
        response = await self.ask("2024年销售额口径是什么？")
        self.assertEqual(response.status, "error")
        self.assertEqual(response.error.code, "INVALID_REQUEST")
        self.assertEqual(response.documents, [])

    async def test_no_http_or_real_model_environment_used(self):
        from third_party.chinook_c.backend.sql_module.generation import HTTPJSONModel
        with patch.object(HTTPJSONModel, "from_env", side_effect=AssertionError("model access")), patch("urllib.request.urlopen", side_effect=AssertionError("HTTP access")):
            response = await self.ask("销售额口径是什么？")
            self.assertEqual(response.status, "answered")
            response = await self.ask("美国客户数量是多少？")
            self.assertEqual(response.sql_results[0].rows[0]["customer_count"], 13)

    async def test_8_sessions_have_independent_retrieval_ids_events_and_evidence(self):
        responses = await asyncio.gather(*(self.service.ask(AskRequest(question="销售额口径是什么？", profile_id="chinook-music", session_id=f"session-{i}", options={"top_k": 1}), request_id=f"req-{i}") for i in range(8)))
        self.assertEqual(len({r.documents[0].retrieval_id for r in responses}), 8)
        self.assertEqual(len({r.documents[0].chunk_id for r in responses}), 1)
        for i, response in enumerate(responses):
            self.assertEqual(response.request_id, f"req-{i}")
            self.assertEqual(response.session_id, f"session-{i}")
            self.assertEqual(response.trace[0].source_refs, [response.documents[0].chunk_id])

    async def test_same_session_next_sql_clears_old_rag(self):
        first = await self.ask("销售额口径是什么？")
        self.assertTrue(first.documents)
        second = await self.ask("客户数量是多少？", request_id="req-next")
        self.assertEqual(second.documents, [])
        self.assertEqual(second.trace[0].tool, "sql.task")

    async def test_source_not_reread_each_turn(self):
        with patch.object(Path, "read_bytes", side_effect=AssertionError("document reparsed")), patch.object(Path, "read_text", side_effect=AssertionError("document reparsed")):
            response = await self.ask("销售额口径是什么？", options={"top_k": 1})
        self.assertEqual(response.status, "answered")

    async def test_closed_executor_no_fixture_fallback(self):
        await self.integration.aclose()
        response = await self.ask("销售额口径是什么？")
        self.assertEqual(response.status, "error")
        self.assertEqual(response.error.code, "PROFILE_UNAVAILABLE")
        self.assertEqual(response.documents, [])


class D12StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_knowledge_root_safe_503_no_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "absent"
            with self.assertRaises(ApplicationError) as caught:
                await create_offline_knowledge_integration(knowledge_root=root)
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(caught.exception.error.code, "PROFILE_UNAVAILABLE")
            self.assertFalse(root.exists())
            self.assertNotIn(directory, caught.exception.error.model_dump_json())

    async def test_corrupt_knowledge_no_sql_or_rag_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "knowledge"
            shutil.copytree(KNOWLEDGE_ROOT, root)
            (root / D12_FILENAME).write_bytes(b"changed")
            with self.assertRaises(ApplicationError):
                await create_offline_knowledge_integration(knowledge_root=root)

    async def test_index_failure_closes_allocated_executor(self):
        from services.agent_api.app.adapters.bounded_executor import BoundedExecutor
        calls = []
        original = BoundedExecutor.aclose

        async def observed(executor, **kwargs):
            calls.append(executor)
            return await original(executor, **kwargs)

        with patch("services.agent_api.app.integrations.sql_d12.load_d12_index", side_effect=ValueError("secret from document path")), patch.object(BoundedExecutor, "aclose", observed):
            with self.assertRaises(ApplicationError) as caught:
                await create_offline_knowledge_integration()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].in_flight, 0)
        self.assertNotIn("secret", caught.exception.error.model_dump_json())

    async def test_invalid_factory_options_safe_failure(self):
        for kwargs in [{"max_workers": True}, {"rag_timeout_ms": 0}, {"max_chars": 99}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ApplicationError):
                await create_offline_knowledge_integration(**kwargs)

    async def test_custom_profile_cannot_advertise_uninstalled_calculator(self):
        with self.assertRaises(ApplicationError):
            await create_offline_knowledge_integration(profiles=ProfileRegistry.defaults())

    async def test_api_remains_unavailable(self):
        with TestClient(create_app(settings=Settings(backend_mode="unavailable"))) as client:
            response = client.post("/api/v1/ask", json={"question": "销售额口径是什么？", "profile_id": "chinook-music"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "PROFILE_UNAVAILABLE")

    async def test_defaults_loading_failure_sanitized_before_pool_creation(self):
        with patch.object(ProfileRegistry, "defaults", side_effect=ValueError("secret config path")):
            with self.assertRaises(ApplicationError) as caught:
                await create_offline_knowledge_integration()
        self.assertNotIn("secret", caught.exception.error.model_dump_json())


class D12DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_timed_out_rag_holds_capacity_and_late_chunks_do_not_enter_new_request(self):
        integration = await create_offline_knowledge_integration(max_workers=1, rag_timeout_ms=80)
        started, release, next_started = threading.Event(), threading.Event(), threading.Event()
        original = integration.rag_tool.index.retrieve

        def delayed(request):
            if request.query.startswith("销售额"):
                started.set()
                release.wait(timeout=3)
            else:
                next_started.set()
            return original(request)

        async def wait_flag(event):
            end = time.monotonic() + 2
            while not event.is_set() and time.monotonic() < end:
                await asyncio.sleep(0.005)
            self.assertTrue(event.is_set())

        try:
            service = integration.create_graph_service()
            with patch.object(integration.rag_tool.index, "retrieve", delayed):
                old = asyncio.create_task(service.ask(AskRequest(question="销售额口径是什么？", profile_id="chinook-music", session_id="old"), request_id="old"))
                await wait_flag(started)
                result = await old
                self.assertEqual(result.status, "error")
                self.assertEqual(result.error.code, "TOOL_TIMEOUT")
                self.assertEqual(result.documents, [])
                self.assertEqual(integration.sql_tool.executor.in_flight, 1)
                new = asyncio.create_task(service.ask(AskRequest(question="默认音乐范围是什么？", profile_id="chinook-music", session_id="new"), request_id="new"))
                await asyncio.sleep(0.015)
                self.assertFalse(next_started.is_set())
                release.set()
                response = await new
                self.assertEqual(response.status, "answered")
                self.assertTrue(all("二 默认的音乐范围" in c.section for c in response.documents))
                self.assertEqual(response.request_id, "new")
                self.assertEqual(response.trace[0].source_refs, [c.chunk_id for c in response.documents])
        finally:
            release.set()
            self.assertTrue(await integration.aclose(grace_seconds=3))

    async def test_expired_rag_budget_does_not_submit_work(self):
        integration = await create_offline_knowledge_integration()
        try:
            with patch.object(integration.rag_tool.index, "retrieve", side_effect=AssertionError("unexpected submission")):
                result = await integration.runtime.run("rag.retrieve", RetrievalRequest(retrieval_id="r", profile_id="chinook-music", query="销售额口径"),
                    ToolContext(request_id="r", session_id="s", profile_id="chinook-music", deadline=time.monotonic() - 1))
            self.assertEqual(result.status, "timeout")
            self.assertEqual(integration.sql_tool.executor.in_flight, 0)
        finally:
            self.assertTrue(await integration.aclose())


class RetrievalRuntimeLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_handler_cannot_exceed_requested_top_k(self):
        profiles = ProfileRegistry.defaults()
        runtime = ToolRuntime(profiles)

        async def bad(request, context):
            return RetrievalResponse(retrieval_id=request.retrieval_id, profile_id=request.profile_id, status="success", chunks=[DocumentChunk(chunk_id=f"test-{i}", doc_id="fixture", title="test only", doc_type="fixture", text="test only", source_uri="fixture://test") for i in range(2)])

        runtime.register(ToolSpec("rag.retrieve", "rag", RetrievalRequest, RetrievalResponse, bad))
        result = await runtime.run("rag.retrieve", RetrievalRequest(retrieval_id="r", profile_id="chinook-music", query="test", top_k=1), ToolContext(request_id="r", session_id="s", profile_id="chinook-music"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "INVALID_TOOL_OUTPUT")
