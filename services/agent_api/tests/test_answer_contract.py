import unittest

from services.agent_api.app.agent.state import BusinessTask, SessionMemory
from services.agent_api.app.agent.task_policy import parse_c_v1
from services.agent_api.app.contracts import AskOptions, DocumentChunk, RetrievalResponse
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.response.composer import FormalResponseComposer, SourcePolicy
from services.agent_api.app.response.models import ComposeContext


class AnswerContractTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")

    def test_unsupported_metric_reason_uses_user_facing_chinese_label(self):
        parsed = parse_c_v1("第三季度利润是多少？", self.profile, SessionMemory(
            profile_id=self.profile.profile_id, config_version=self.profile.config_version,
        ))

        self.assertEqual(parsed.decision, "unsupported")
        self.assertIn("利润", parsed.reason)
        self.assertNotIn("profit", parsed.reason)

    def test_genre_ids_in_sql_list_answer_use_canonical_prefix(self):
        result = type("Result", (), {
            "sql": "SELECT GenreId, Name FROM main.Genre ORDER BY GenreId",
            "params": {}, "source": type("Source", (), {"tables": ["main.Genre"]})(),
            "row_count": 2, "rows": [{"genreid": 1, "name": "Rock"}, {"genreid": 5, "name": "Rock And Roll"}],
            "columns": ["genreid", "name"], "truncated": False,
            "query_id": "sql-contract-1",
        })()
        sql = type("Sql", (), {"status": "success", "sql_results": [result], "limitations": [],
                                "metric_definitions": []})()
        task = BusinessTask(original_question="有哪些音乐类型", normalized_question="有哪些音乐类型",
                            intent="list_genres", route="sql")
        context = ComposeContext(request_id="req", session_id="session", profile=self.profile,
                                 status="answered", route="sql", task=task, options=AskOptions(), sql=sql)
        composer = FormalResponseComposer(SourcePolicy("fixture", self.profile.profile_id))

        # This is deliberately RED until the answer renderer emits Genre:<id>.
        answer = composer._sql_answer(context)
        self.assertIn("Genre:1", answer)
        self.assertIn("Genre:5", answer)

    def test_duplicate_evidence_chunk_is_merged_without_disabling_source_checks(self):
        chunk = DocumentChunk(chunk_id="fixture-D03-1", doc_id="fixture-D03", title="fixture",
                              doc_type="fixture", text="Genre:1", source_uri="fixture://D03")
        rag = RetrievalResponse(retrieval_id="retrieval-1", profile_id=self.profile.profile_id,
                                status="success", chunks=[chunk, chunk.model_copy(update={"retrieval_id": "retrieval-2", "score": 0.2})])
        task = BusinessTask(original_question="分类规则", normalized_question="分类规则",
                            intent="document_rule", route="rag")
        context = ComposeContext(request_id="req", session_id="session", profile=self.profile,
                                 status="answered", route="rag", task=task, options=AskOptions(), rag=rag,
                                 available_source_refs=(chunk.chunk_id,))
        composer = FormalResponseComposer(SourcePolicy("fixture", self.profile.profile_id))

        # Duplicate chunks from merged retrievals are deterministic duplicates,
        # while source/profile authorization must still run for each identity.
        composer._verify_sources(context)
        self.assertEqual([item.chunk_id for item in composer._documents(context)], [chunk.chunk_id])


if __name__ == "__main__":
    unittest.main()
