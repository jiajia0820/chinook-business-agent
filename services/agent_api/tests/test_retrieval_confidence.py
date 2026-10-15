"""Conservative confidence rules for guarded hybrid retrieval (Task 4)."""

import unittest

from services.agent_api.app.knowledge.retrieval_confidence import (
    assess_retrieval,
    guarded_fusion,
    resolve_fusion_mode,
)


class AssessRetrievalTests(unittest.TestCase):
    def test_required_source_and_channel_agreement_are_high(self):
        result = assess_retrieval(
            query_kind="fact",
            lexical_docs=["D02", "D02", "D01"],
            dense_docs=["D02", "D02", "D01"],
            required_docs=["D02"],
            top_score=0.78,
            second_score=0.51,
            has_formula=False,
            has_comparison=False,
        )
        self.assertEqual(result.level, "high")
        self.assertFalse(result.requires_model)
        self.assertTrue(result.coverage_complete)

    def test_complex_query_is_never_no_call_only_by_score(self):
        result = assess_retrieval(
            query_kind="cross_source",
            lexical_docs=["D01", "D06"],
            dense_docs=["D01", "D06"],
            required_docs=["D01", "D06"],
            top_score=0.90,
            second_score=0.30,
            has_formula=True,
            has_comparison=True,
        )
        self.assertTrue(result.requires_model)

    def test_formula_alone_forces_model_even_with_perfect_channels(self):
        result = assess_retrieval(
            query_kind="fact",
            lexical_docs=["D06", "D06"],
            dense_docs=["D06", "D06"],
            required_docs=["D06"],
            top_score=0.95,
            second_score=0.20,
            has_formula=True,
            has_comparison=False,
        )
        self.assertTrue(result.requires_model)

    def test_unknown_required_source_is_never_high(self):
        result = assess_retrieval(
            query_kind="fact",
            lexical_docs=["D02", "D01"],
            dense_docs=["D02", "D01"],
            required_docs=[],
            top_score=0.88,
            second_score=0.40,
        )
        self.assertFalse(result.coverage_complete)
        self.assertNotEqual(result.level, "high")
        self.assertTrue(result.requires_model)

    def test_missing_required_source_is_low_coverage(self):
        result = assess_retrieval(
            query_kind="fact",
            lexical_docs=["D08"],
            dense_docs=["D08"],
            required_docs=["D02"],
            top_score=0.80,
            second_score=0.10,
        )
        self.assertFalse(result.coverage_complete)
        self.assertEqual(result.level, "low")

    def test_conflicting_channels_are_not_high(self):
        result = assess_retrieval(
            query_kind="fact",
            lexical_docs=["D02", "D03"],
            dense_docs=["D08", "D09"],
            required_docs=["D02"],
            top_score=0.82,
            second_score=0.80,
        )
        self.assertNotEqual(result.level, "high")
        self.assertLess(result.channel_agreement, 0.5)

    def test_margin_is_not_inflated_by_inverted_scores(self):
        result = assess_retrieval(
            query_kind="fact",
            lexical_docs=["D02", "D02"],
            dense_docs=["D02", "D02"],
            required_docs=["D02"],
            top_score=0.30,
            second_score=0.90,
        )
        self.assertLessEqual(result.margin, 0.0)
        self.assertNotEqual(result.level, "high")


class GuardedFusionTests(unittest.TestCase):
    def test_conflicting_channels_fall_back_to_lexical(self):
        result = guarded_fusion(
            lexical_docs=["D02", "D02", "D03"],
            dense_docs=["D08", "D08", "D09"],
            lexical_scores=[0.80, 0.70, 0.60],
            dense_scores=[0.82, 0.81, 0.80],
            required_docs=["D02"],
        )
        self.assertEqual(result.mode, "lexical_fallback")
        self.assertEqual(result.docs[0], "D02")
        self.assertEqual(result.reason, "channel_conflict")

    def test_missing_dense_channel_falls_back_to_lexical(self):
        result = guarded_fusion(
            lexical_docs=["D04", "D04"],
            dense_docs=[],
            lexical_scores=[0.9, 0.5],
            dense_scores=[],
            required_docs=["D04"],
        )
        self.assertEqual(result.mode, "lexical_fallback")
        self.assertEqual(result.reason, "dense_unavailable")
        self.assertEqual(tuple(result.docs), ("D04",))

    def test_uncovered_required_source_falls_back_to_lexical(self):
        result = guarded_fusion(
            lexical_docs=["D08", "D09"],
            dense_docs=["D08", "D09"],
            lexical_scores=[0.8, 0.7],
            dense_scores=[0.8, 0.7],
            required_docs=["D02"],
        )
        self.assertEqual(result.mode, "lexical_fallback")
        self.assertEqual(result.reason, "required_source_missing")

    def test_unknown_required_source_falls_back_to_lexical(self):
        result = guarded_fusion(
            lexical_docs=["D08", "D08"],
            dense_docs=["D08", "D08"],
            lexical_scores=[0.8, 0.7],
            dense_scores=[0.8, 0.7],
            required_docs=[],
        )
        self.assertEqual(result.mode, "lexical_fallback")
        self.assertEqual(result.reason, "required_source_unknown")

    def test_agreeing_channels_fuse_without_dropping_required_source(self):
        result = guarded_fusion(
            lexical_docs=["D02", "D01", "D02"],
            dense_docs=["D01", "D02", "D12"],
            lexical_scores=[0.80, 0.60, 0.55],
            dense_scores=[0.71, 0.70, 0.40],
            required_docs=["D02"],
        )
        self.assertEqual(result.mode, "hybrid")
        self.assertIn("D02", result.docs)
        self.assertIn("D01", result.docs)
        self.assertIsNone(result.reason)

    def test_fusion_never_invents_documents_from_one_channel(self):
        result = guarded_fusion(
            lexical_docs=["D02", "D02", "D01"],
            dense_docs=["D02", "D02", "D01"],
            lexical_scores=[0.8, 0.7, 0.6],
            dense_scores=[0.8, 0.7, 0.6],
            required_docs=["D02"],
        )
        self.assertTrue(set(result.docs) <= {"D02", "D01"})

    def test_dense_top_hit_outside_lexical_top_is_conflict(self):
        # Overlap alone would pass here; the unsupported semantic top-1 must not.
        result = guarded_fusion(
            lexical_docs=["D02", "D01", "D03", "D08"],
            dense_docs=["D08", "D02"],
            lexical_scores=[0.8, 0.7, 0.6, 0.5],
            dense_scores=[0.80, 0.40],
            required_docs=["D02"],
        )
        self.assertEqual(result.mode, "lexical_fallback")
        self.assertEqual(result.reason, "channel_conflict")
        self.assertFalse(result.assessment.top1_supported)

    def test_single_confident_semantic_hit_can_corroborate_lexical(self):
        result = guarded_fusion(
            lexical_docs=["D04", "D06", "D02"],
            dense_docs=["D04"],
            lexical_scores=[0.9, 0.4, 0.3],
            dense_scores=[0.88],
            required_docs=["D04"],
        )
        self.assertEqual(result.mode, "hybrid")
        self.assertTrue(result.assessment.top1_supported)


class ResolveFusionModeTests(unittest.TestCase):
    def test_default_is_lexical(self):
        self.assertEqual(resolve_fusion_mode(None), ("lexical", None))
        self.assertEqual(resolve_fusion_mode(""), ("lexical", None))

    def test_known_modes_pass_through(self):
        self.assertEqual(resolve_fusion_mode("hybrid"), ("hybrid", None))
        self.assertEqual(resolve_fusion_mode("RANK"), ("rank", None))

    def test_unknown_mode_degrades_to_lexical_with_reason(self):
        mode, reason = resolve_fusion_mode("aggressive")
        self.assertEqual(mode, "lexical")
        self.assertEqual(reason, "unsupported_fusion_mode")


if __name__ == "__main__":
    unittest.main()
