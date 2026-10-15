"""End-to-end guards: hybrid retrieval must never weaken the lexical contract."""

import os
from unittest import mock
import unittest

from services.agent_api.app.contracts import RetrievalRequest
from services.agent_api.app.knowledge.facts import fact_matches
from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index
from services.agent_api.app.profiles import ProfileRegistry


SEGMENTATION_QUERY = "给顾客分三档时，各档的消费金额界线分别是多少？"


def _dense_scores_favoring(index, preferred_docs, high=0.92, low=0.04):
    """Build a fake cosine map so one side of the channel dominates."""
    scores = {}
    step = 0.0
    for item in index.chunks:
        if item.record.doc_id in preferred_docs:
            scores[item.record.chunk_id] = round(high - step, 6)
            step += 0.0005
        else:
            scores[item.record.chunk_id] = low
    return scores


class GuardedHybridDay1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = ProfileRegistry.defaults().get("chinook-music")
        cls.index = load_knowledge_index(KNOWLEDGE_ROOT, cls.profile, max_chars=800)

    def setUp(self):
        # Never inherit a developer's local experiment switch.
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("RAG_FUSION_MODE", None)
        os.environ.pop("RAG_ENABLE_DENSE", None)

    def retrieve(self, *, mode=None, dense=None, filters=None, query=SEGMENTATION_QUERY, top_k=4):
        if mode is not None:
            os.environ["RAG_FUSION_MODE"] = mode
        request = RetrievalRequest(
            retrieval_id="retrieval-guarded", profile_id="chinook-music", query=query,
            filters=filters if filters is not None else {"required_sources": ["D04"], "preferred_doc": "D04"},
            top_k=top_k,
        )
        if dense is None:
            return self.index.retrieve(request)
        with mock.patch.object(type(self.index), "_dense_scores", return_value=dense):
            return self.index.retrieve(request)

    def test_default_mode_stays_lexical(self):
        baseline = self.retrieve(mode="lexical")
        default = self.retrieve()
        self.assertEqual(default.status, "success")
        self.assertEqual([c.chunk_id for c in default.chunks], [c.chunk_id for c in baseline.chunks])
        self.assertEqual(default.diagnostics.get("retrieval_mode"), "lexical")

    def test_unsupported_mode_degrades_to_lexical(self):
        baseline = self.retrieve(mode="lexical")
        response = self.retrieve(mode="aggressive", dense=_dense_scores_favoring(self.index, {"D09"}))
        self.assertEqual([c.chunk_id for c in response.chunks], [c.chunk_id for c in baseline.chunks])
        self.assertEqual(response.diagnostics.get("retrieval_mode"), "lexical")
        self.assertEqual(response.diagnostics.get("fallback_reason"), "unsupported_fusion_mode")

    def test_conflicting_dense_channel_falls_back_to_lexical(self):
        baseline = self.retrieve(mode="lexical")
        response = self.retrieve(mode="hybrid", dense=_dense_scores_favoring(self.index, {"D09", "D12"}))
        self.assertEqual(response.status, "success")
        self.assertEqual([c.chunk_id for c in response.chunks], [c.chunk_id for c in baseline.chunks])
        self.assertEqual(response.diagnostics.get("retrieval_mode"), "lexical_fallback")
        self.assertEqual(response.diagnostics.get("fallback_reason"), "channel_conflict")
        self.assertEqual(response.diagnostics.get("retrieval_confidence"), "low")

    def test_unavailable_dense_channel_falls_back_to_lexical(self):
        baseline = self.retrieve(mode="hybrid", dense={})
        self.assertEqual(baseline.status, "success")
        self.assertEqual(baseline.diagnostics.get("retrieval_mode"), "lexical_fallback")
        self.assertEqual(baseline.diagnostics.get("fallback_reason"), "dense_unavailable")
        lexical = self.retrieve(mode="lexical")
        self.assertEqual([c.chunk_id for c in baseline.chunks], [c.chunk_id for c in lexical.chunks])

    def test_agreeing_channels_may_fuse_and_keep_required_coverage(self):
        response = self.retrieve(mode="hybrid", dense=_dense_scores_favoring(self.index, {"D04"}))
        self.assertEqual(response.status, "success")
        self.assertEqual(response.diagnostics.get("retrieval_mode"), "hybrid")
        self.assertIsNone(response.diagnostics.get("fallback_reason"))
        self.assertIn("D04", {chunk.doc_id for chunk in response.chunks})

    def test_semantic_recall_is_gated_by_required_sources(self):
        lexical = self.retrieve(mode="lexical")
        response = self.retrieve(mode="hybrid", dense=_dense_scores_favoring(self.index, {"D04"}))
        known = {chunk.chunk_id for chunk in lexical.chunks}
        for chunk in response.chunks:
            if chunk.chunk_id not in known:
                self.assertIn(chunk.doc_id, {"D04"})

    def test_fact_role_survives_hybrid_recall(self):
        filters = {"fact_role": "customer_thresholds", "doc_id": "D04", "required_sources": ["D04"]}
        response = self.retrieve(mode="hybrid", dense=_dense_scores_favoring(self.index, {"D04"}),
                                 filters=filters, query="客户分层阈值 A层 B层 C层 金额界线")
        self.assertEqual(response.status, "success")
        self.assertTrue(any(fact_matches("customer_thresholds", chunk.text) for chunk in response.chunks))

    def test_multidoc_required_sources_stay_exact_under_hybrid(self):
        filters = {"required_sources": ["D07", "D08"], "answer_type": "review_comparison"}
        query = "比较 2025 年第二季度和第三季度经营复盘核心结果"
        response = self.retrieve(mode="hybrid", dense=_dense_scores_favoring(self.index, {"D07", "D08"}),
                                 filters=filters, query=query)
        self.assertEqual(response.status, "success")
        self.assertEqual({chunk.doc_id for chunk in response.chunks}, {"D07", "D08"})

    def test_rank_mode_is_also_guarded(self):
        baseline = self.retrieve(mode="lexical")
        response = self.retrieve(mode="rank", dense=_dense_scores_favoring(self.index, {"D09", "D12"}))
        self.assertEqual([c.chunk_id for c in response.chunks], [c.chunk_id for c in baseline.chunks])
        self.assertEqual(response.diagnostics.get("retrieval_mode"), "lexical_fallback")

    def test_legacy_dense_flag_is_untouched_by_default(self):
        os.environ["RAG_ENABLE_DENSE"] = "0"
        response = self.retrieve()
        self.assertEqual(response.diagnostics.get("retrieval_mode"), "lexical")
        self.assertEqual(response.diagnostics.get("requested_fusion_mode"), "lexical")


if __name__ == "__main__":
    unittest.main()
