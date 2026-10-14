import unittest

from services.agent_api.app.agent.state import BusinessTask
from services.agent_api.app.contracts import AskOptions, DocumentChunk, RetrievalResponse
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.response.composer import FormalResponseComposer, SourcePolicy
from services.agent_api.app.response.models import ComposeContext


class LayeredEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.composer = FormalResponseComposer(
            SourcePolicy(
                execution_mode="fixture",
                profile_id=self.profile.profile_id,
                documents=(),
                model_mode="offline",
            )
        )

    def context(self, chunks):
        task = BusinessTask(
            original_question="销售额口径是什么",
            normalized_question="销售额口径是什么",
            intent="document_rule",
            route="rag",
        )
        retrieval = RetrievalResponse(
            retrieval_id="retrieval-layered",
            profile_id=self.profile.profile_id,
            status="success",
            chunks=chunks,
        )
        return ComposeContext(
            request_id="req-layered",
            session_id="session-layered",
            profile=self.profile,
            status="insufficient_evidence",
            route="rag",
            task=task,
            options=AskOptions(),
            rag=retrieval,
            retrievals=(retrieval,),
            available_source_refs=tuple(item.chunk_id for item in chunks),
            limitations=("缺少必要文档 D01。",),
        )

    def test_document_chunk_is_preserved_as_partial_answer(self):
        chunk = DocumentChunk(
            chunk_id="fixture-layered-1",
            doc_id="D12",
            title="FAQ",
            doc_type="fixture",
            section="口径",
            text="销售额口径说明：销售额按明细单价乘以数量汇总，日期采用交易日期。",
            source_uri="fixture://D12/1",
        )

        response = self.composer.compose(self.context([chunk]))

        self.assertEqual(response.status, "insufficient_evidence")
        self.assertIn("已确认部分", response.answer)
        self.assertIn("销售额按明细单价乘以数量汇总", response.answer)
        self.assertTrue(any("缺少必要文档 D01" in item for item in response.limitations))

    def test_empty_document_result_keeps_evidence_gap_without_partial_answer(self):
        response = self.composer.compose(self.context([]))

        self.assertEqual(response.status, "insufficient_evidence")
        self.assertIn("不能形成确定结论", response.answer)
        self.assertNotIn("已确认部分", response.answer)


if __name__ == "__main__":
    unittest.main()
