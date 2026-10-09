from pathlib import Path
import unittest

from services.agent_api.app.contracts import RetrievalRequest
from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
from services.agent_api.app.profiles import ProfileRegistry


class KnowledgeIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = ProfileRegistry.defaults().get("chinook-music")
        cls.index = load_knowledge_index(KNOWLEDGE_ROOT, cls.profile, max_chars=800)

    def test_each_document_has_chunks_and_location(self):
        document_ids = {item.record.doc_id for item in self.index.chunks}
        self.assertEqual(document_ids, {f"D{i:02}" for i in range(1, 13)})
        for item in self.index.chunks:
            self.assertTrue(item.record.source_uri.startswith("data/knowledge/"))
            self.assertTrue(item.record.page or item.record.section or item.record.bbox)

    def test_retrieval_returns_document_identity_and_locator(self):
        response = self.index.retrieve(RetrievalRequest(
            retrieval_id="retrieval-test", profile_id="chinook-music",
            query="2025 Q3 Rock 销售额目标 50.00 美元",
            filters={"doc_id": "D06"}, top_k=3,
        ))
        self.assertEqual(response.status, "success")
        self.assertTrue(response.chunks)
        self.assertIn("D06", {chunk.doc_id for chunk in response.chunks})
        self.assertTrue(all(chunk.retrieval_id == "retrieval-test" for chunk in response.chunks))

    def test_unknown_query_is_success_with_no_evidence(self):
        response = self.index.retrieve(RetrievalRequest(
            retrieval_id="retrieval-empty", profile_id="chinook-music",
            query="火星移民政策", top_k=3,
        ))
        self.assertEqual((response.status, response.chunks), ("success", []))

    def test_alias_classification_prefers_d03_authority(self):
        response = self.index.retrieve(RetrievalRequest(
            retrieval_id="retrieval-alias-authority", profile_id="chinook-music",
            query="重金属写成 Heavy Metal 如何归类？",
            filters={"answer_type": "alias_mapping", "preferred_doc": "D03"}, top_k=3,
        ))
        self.assertEqual(response.status, "success")
        self.assertTrue(response.chunks)
        self.assertEqual(response.chunks[0].doc_id, "D03")

    def test_required_sources_are_covered_for_multidoc_retrieval(self):
        response = self.index.retrieve(RetrievalRequest(
            retrieval_id="retrieval-multidoc-coverage", profile_id="chinook-music",
            query="比较 2025 年第二季度和第三季度经营复盘核心结果",
            filters={"required_sources": ["D07", "D08"], "answer_type": "review_comparison"}, top_k=4,
        ))
        self.assertEqual(response.status, "success")
        self.assertEqual({chunk.doc_id for chunk in response.chunks}, {"D07", "D08"})

    def test_dense_retrieval_flag_can_fall_back_to_lexical(self):
        response = self.index.retrieve(RetrievalRequest(
            retrieval_id="retrieval-dense-fallback", profile_id="chinook-music",
            query="经营表现核心结果",
            filters={"dense_retrieval": False}, top_k=2,
        ))
        self.assertEqual(response.status, "success")


if __name__ == "__main__":
    unittest.main()
