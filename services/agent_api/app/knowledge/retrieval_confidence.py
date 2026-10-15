"""Conservative confidence and fusion guards for hybrid retrieval.

The lexical channel stays authoritative.  Semantic recall may only reorder or
extend evidence when both channels agree and the planner's required sources are
covered; every other situation degrades to the untouched lexical result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

FUSION_MODES: tuple[str, ...] = ("lexical", "rank", "hybrid")
DEFAULT_FUSION_MODE = "lexical"

# A margin is only comparable inside one score space.  These thresholds are
# cosine-space values and must never be applied to RRF or IDF-weighted scores.
HIGH_MARGIN = 0.15
AGREEMENT_DEPTH = 3
MIN_AGREEMENT = 0.5
LOW_AGREEMENT = 0.34
RRF_K = 60

# Question shapes whose answer must not be produced from retrieval score alone.
COMPLEX_QUERY_KINDS = frozenset({"cross_source", "comparison", "calculation", "trend"})

FORMULA_HINTS = ("公式", "达成率", "增长率", "客单价", "成交均价", "除以", "怎么计算", "如何计算",
                 "差额", "环比", "同比", "分母")
COMPARISON_HINTS = ("比较", "对比", "相比", "差异", "变化", "哪个更", "谁更")
CROSS_SOURCE_HINTS = ("结合", "跨文档", "多个文档", "共同", "以及活动", "核对")


@dataclass(frozen=True)
class RetrievalAssessment:
    """Evidence-quality verdict plus the separate decision to involve a model."""

    level: str
    requires_model: bool
    coverage_complete: bool
    channel_agreement: float
    margin: float
    top1_supported: bool = True
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class FusionOutcome:
    mode: str
    docs: tuple[str, ...]
    reason: str | None = None
    assessment: RetrievalAssessment | None = None


def resolve_fusion_mode(raw: object) -> tuple[str, str | None]:
    """Normalize RAG_FUSION_MODE; unknown values degrade to lexical."""
    if raw is None:
        return DEFAULT_FUSION_MODE, None
    text = str(raw).strip().casefold()
    if not text:
        return DEFAULT_FUSION_MODE, None
    if text in FUSION_MODES:
        return text, None
    return DEFAULT_FUSION_MODE, "unsupported_fusion_mode"


def doc_sequence(docs: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate a ranked chunk/doc list while preserving rank order."""
    return tuple(dict.fromkeys(str(doc) for doc in docs if doc))


def channel_agreement(lexical_docs: Sequence[str], dense_docs: Sequence[str],
                      depth: int = AGREEMENT_DEPTH) -> float:
    """Overlap coefficient of the top-`depth` documents of each channel.

    Jaccard would punish an asymmetric recall depth (one confident semantic hit
    against three lexical ones), so the smaller channel is the denominator.
    """
    left = set(doc_sequence(lexical_docs)[:depth])
    right = set(doc_sequence(dense_docs)[:depth])
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def top1_supported(lexical_docs: Sequence[str], dense_docs: Sequence[str],
                   depth: int = AGREEMENT_DEPTH) -> bool:
    """The semantic channel's best hit must also be a lexical top-`depth` hit."""
    lexical = doc_sequence(lexical_docs)
    dense = doc_sequence(dense_docs)
    if not lexical or not dense:
        return False
    return dense[0] in set(lexical[:depth])


def infer_query_flags(question: str) -> tuple[bool, bool, bool]:
    """Return (has_formula, has_comparison, is_cross_source) from wording only."""
    text = str(question or "").casefold()
    has_formula = any(hint.casefold() in text for hint in FORMULA_HINTS)
    has_comparison = any(hint.casefold() in text for hint in COMPARISON_HINTS)
    is_cross_source = any(hint.casefold() in text for hint in CROSS_SOURCE_HINTS)
    return has_formula, has_comparison, is_cross_source


def assess_retrieval(*, query_kind: str, lexical_docs: Sequence[str], dense_docs: Sequence[str],
                     required_docs: Sequence[str], top_score: float, second_score: float,
                     has_formula: bool = False, has_comparison: bool = False) -> RetrievalAssessment:
    """Grade one retrieval attempt without ever trusting score alone."""
    lexical = doc_sequence(lexical_docs)
    dense = doc_sequence(dense_docs)
    required = doc_sequence(required_docs)
    agreement = channel_agreement(lexical, dense)
    covered = [doc for doc in required if doc in set(lexical) | set(dense)]
    coverage_complete = bool(required) and len(covered) == len(required)
    # Both scores must come from the same channel; an inverted pair means the
    # caller mixed spaces, so the margin is treated as non-existent.
    margin = float(top_score) - float(second_score)

    reasons: list[str] = []
    if not required:
        reasons.append("required_source_unknown")
    elif not coverage_complete:
        reasons.append("required_source_missing")
    supported = top1_supported(lexical, dense)
    if not supported:
        reasons.append("dense_top1_unsupported")
    if agreement < MIN_AGREEMENT:
        reasons.append("channel_conflict")
    if margin < HIGH_MARGIN:
        reasons.append("weak_margin")
    if has_formula:
        reasons.append("formula_question")
    if has_comparison:
        reasons.append("comparison_question")

    if not coverage_complete or agreement < LOW_AGREEMENT or not supported:
        level = "low"
    elif agreement >= MIN_AGREEMENT and margin >= HIGH_MARGIN:
        level = "high"
    else:
        level = "medium"

    requires_model = bool(
        has_formula
        or has_comparison
        or str(query_kind).strip().casefold() in COMPLEX_QUERY_KINDS
        or not coverage_complete
        or level == "low"
    )
    return RetrievalAssessment(
        level=level, requires_model=requires_model, coverage_complete=coverage_complete,
        channel_agreement=round(agreement, 6), margin=round(margin, 6),
        top1_supported=supported, reasons=tuple(reasons),
    )


def _reciprocal_rank_fusion(lexical: Sequence[str], dense: Sequence[str]) -> tuple[str, ...]:
    scores: dict[str, float] = {}
    for rank, doc in enumerate(lexical, 1):
        scores[doc] = scores.get(doc, 0.0) + 1.0 / (RRF_K + rank)
    for rank, doc in enumerate(dense, 1):
        scores[doc] = scores.get(doc, 0.0) + 1.0 / (RRF_K + rank)
    lexical_first = {doc: index for index, doc in enumerate(lexical)}
    # Ties keep the lexical rank so the authoritative channel still decides.
    return tuple(sorted(scores, key=lambda doc: (-scores[doc], lexical_first.get(doc, len(lexical)), doc)))


def guarded_fusion(*, lexical_docs: Sequence[str], dense_docs: Sequence[str],
                   lexical_scores: Sequence[float] | None = None,
                   dense_scores: Sequence[float] | None = None,
                   required_docs: Sequence[str] = (), query_kind: str = "fact",
                   has_formula: bool = False, has_comparison: bool = False) -> FusionOutcome:
    """Fuse two channels, or hand back the lexical ranking when unsafe."""
    lexical = doc_sequence(lexical_docs)
    dense = doc_sequence(dense_docs)
    required = doc_sequence(required_docs)
    scores = [float(value) for value in (dense_scores or ())]
    # Margin is measured in the dense cosine space only, never across channels.
    top_score = scores[0] if scores else 0.0
    second_score = scores[1] if len(scores) > 1 else 0.0
    assessment = assess_retrieval(
        query_kind=query_kind, lexical_docs=lexical, dense_docs=dense, required_docs=required,
        top_score=top_score, second_score=second_score,
        has_formula=has_formula, has_comparison=has_comparison,
    )

    if not dense:
        reason = "dense_unavailable"
    elif not required:
        reason = "required_source_unknown"
    elif not set(required) <= set(lexical):
        reason = "required_source_missing"
    elif assessment.channel_agreement < MIN_AGREEMENT or not assessment.top1_supported:
        reason = "channel_conflict"
    else:
        reason = None

    if reason is not None:
        return FusionOutcome(mode="lexical_fallback", docs=lexical, reason=reason, assessment=assessment)
    fused = _reciprocal_rank_fusion(lexical, dense)
    # Required sources keep their planner priority even after fusion.
    pinned = [doc for doc in required if doc in set(fused)]
    ordered = tuple([*pinned, *(doc for doc in fused if doc not in set(pinned))])
    return FusionOutcome(mode="hybrid", docs=ordered, reason=None, assessment=assessment)


__all__ = [
    "AGREEMENT_DEPTH", "COMPLEX_QUERY_KINDS", "DEFAULT_FUSION_MODE", "FUSION_MODES",
    "FusionOutcome", "HIGH_MARGIN", "MIN_AGREEMENT", "RetrievalAssessment",
    "assess_retrieval", "channel_agreement", "doc_sequence", "guarded_fusion",
    "infer_query_flags", "resolve_fusion_mode", "top1_supported",
]
