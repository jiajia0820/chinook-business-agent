"""Hash-locked D12 snapshot and transparent lexical retrieval (no embeddings)."""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

from pydantic import Field

from ..contracts import ApiError, DocumentChunk, RetrievalRequest, RetrievalResponse
from ..profiles.models import BusinessProfile
from ..tools.models import InternalModel
from .markdown import MarkdownChunk, chunk_markdown


KNOWLEDGE_ROOT = Path(__file__).resolve().parents[4] / "data" / "knowledge"
D12_FILENAME = "D12_经营查询与数据使用FAQ.md"
D12_SHA256 = "939adb9abd7ed425a682edd356292ce70141a93abd1632ab6ca3b064d5105a7e"
ARCHIVE_SHA256 = "74748f209e042eab3bc446491e6b6ad7ccf5138e5dad93b79221dfa55541e27d"
SOURCE_PREFIX = "data/knowledge/"
STOP_PHRASES = ("请问", "请", "帮我", "解释一下", "说明一下", "是什么", "有什么区别", "什么区别", "怎么理解", "哪些", "如何", "多少", "的", "与", "和", "是", "吗", "呢")
RULE_TERMS = ("口径", "定义", "规则", "怎么计算", "公式")
RULE_EXPANSION = ("口径", "指标", "计算", "定义", "规则")


class KnowledgeDocument(InternalModel):
    doc_id: str
    path: str
    source_entry: str
    sha256: str
    size_bytes: int = Field(ge=1, le=1048576)
    title: str
    document_number: str
    version: str
    effective_date: str
    doc_type: str
    purpose: str


class KnowledgeManifest(InternalModel):
    snapshot_date: str
    source_archive: str
    archive_sha256: str
    profile_id: str
    documents: list[KnowledgeDocument] = Field(min_length=1, max_length=12)


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def lexical_terms(value: str) -> tuple[str, ...]:
    """Chinese contiguous bigrams + Latin/digit words, no language model."""
    terms = []
    for token in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", normalize(value)):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            terms.extend(token[i:i + 2] for i in range(len(token) - 1))
            if len(token) == 1:
                terms.append(token)
        else:
            terms.append(token)
    return tuple(dict.fromkeys(terms))


def contains_term(text: str, term: str) -> bool:
    if term.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None
    return term in text


@dataclass(frozen=True)
class IndexedChunk:
    record: MarkdownChunk
    body: str
    heading: str


def _error(request: RetrievalRequest, code: str, message: str) -> RetrievalResponse:
    return RetrievalResponse(retrieval_id=request.retrieval_id, profile_id=request.profile_id,
        status="failed", error=ApiError(code=code, message=message, retryable=False))


class D12Index:
    """Immutable content snapshot; return new public chunk objects per call."""

    def __init__(self, manifest: KnowledgeManifest, chunks: tuple[MarkdownChunk, ...], profile: BusinessProfile):
        self.profile_id = manifest.profile_id
        self.document = manifest.documents[0].model_copy(deep=True)
        self.profile = profile.model_copy(deep=True)
        self.chunks = tuple(IndexedChunk(chunk, normalize(chunk.text), normalize(chunk.section)) for chunk in chunks)

    def _query_groups(self, query: str, metric_ids: list[str]):
        value = normalize(query)
        groups = []
        # Longest-first spans avoid treating 购买客户数 as a generic count.
        candidates = []
        for metric, terms in self.profile.question_rules.metric_terms.items():
            for term in terms:
                for match in re.finditer(re.escape(normalize(term)), value):
                    candidates.append((match.start(), match.end(), metric))
        spans, metrics = [], list(metric_ids)
        for start, end, metric in sorted(candidates, key=lambda item: -(item[1] - item[0])):
            if any(start < other_end and end > other_start for other_start, other_end in spans):
                continue
            spans.append((start, end))
            if metric not in metrics:
                metrics.append(metric)
        for metric in metrics:
            terms = self.profile.question_rules.metric_terms.get(metric)
            if not terms:
                raise ValueError("unknown metric")
            # Each named metric is required. A missing registration definition
            # cannot be replaced by the purchasing-customer definition in D12.
            groups.append(tuple(dict.fromkeys(normalize(term) for term in terms)))
        for start, end in sorted(spans, reverse=True):
            value = value[:start] + " " + value[end:]
        if any(term in value for term in RULE_TERMS):
            groups.append(RULE_EXPANSION)
            for term in sorted(RULE_TERMS, key=len, reverse=True):
                value = value.replace(term, " ")
        for term in sorted(STOP_PHRASES, key=len, reverse=True):
            value = value.replace(term, " ")
        # The remaining unknown content is NOT dropped: at least 60% must
        # match, and every remaining Latin/digit word must match exactly.
        residual = lexical_terms(value)
        return groups, residual

    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        request = RetrievalRequest.model_validate(request.model_dump(mode="python"), strict=True)
        if request.profile_id != self.profile_id:
            return _error(request, "PROFILE_NOT_FOUND", "该知识索引不属于请求 profile。")
        if len(request.query) > 4000:
            return _error(request, "INVALID_REQUEST", "检索问题超过本阶段长度上限。")
        try:
            filters = request.filters
            if not set(filters) <= {"doc_id", "section", "business_metric_ids", "slots"}:
                raise ValueError("unknown filter")
            doc_id, section = filters.get("doc_id"), filters.get("section")
            if doc_id is not None and (not isinstance(doc_id, str) or not doc_id.strip()):
                raise ValueError("invalid document filter")
            if section is not None and (not isinstance(section, str) or not section.strip()):
                raise ValueError("invalid section filter")
            metric_ids = filters.get("business_metric_ids", [])
            if not isinstance(metric_ids, list) or any(not isinstance(m, str) for m in metric_ids) or len(metric_ids) != len(set(metric_ids)):
                raise ValueError("invalid metric hints")
            slots = filters.get("slots", {})
            if not isinstance(slots, dict) or not set(slots) <= {"media_ids", "target_metric"}:
                raise ValueError("unverified period/scope filter")
            if "media_ids" in slots:
                media = slots["media_ids"]
                if not isinstance(media, list) or any(type(item) is not int for item in media) or media != self.profile.default_media_ids:
                    raise ValueError("unknown media hint")
            if "target_metric" in slots and slots["target_metric"] not in metric_ids:
                raise ValueError("metric hint mismatch")
            groups, residual = self._query_groups(request.query, metric_ids)
        except (ValueError, TypeError):
            return _error(request, "INVALID_REQUEST", "检索筛选或指标提示未受支持；未忽略条件，也未验证年份、品类或目标范围。")
        ranked = []
        for chunk in self.chunks:
            if doc_id is not None and doc_id != self.document.doc_id:
                continue
            if section is not None and not (chunk.record.section == section or chunk.record.section.startswith(section + " / ")):
                continue
            if not groups and not residual:
                continue
            # Whole metrics/intent concepts are mandatory, not loose OR hints.
            if any(not any(term in chunk.body for term in group) for group in groups):
                continue
            matched = [term for term in residual if contains_term(chunk.body, term)]
            if residual and len(matched) / len(residual) < 0.6:
                continue
            if any(term.isascii() and not contains_term(chunk.body, term) for term in residual):
                continue
            terms = list(matched)
            for group in groups:
                terms.append(max((term for term in group if term in chunk.body), key=lambda term: (term in chunk.heading, len(term))))
            # Raw lexical relevance, NOT probability/embedding similarity:
            # sum((1+log(1+tf)+3*heading_hit) * (1+log((N+1)/(df+1)))).
            score = sum((1 + math.log1p(chunk.body.count(term)) + 3 * (term in chunk.heading)) *
                (1 + math.log((len(self.chunks) + 1) / (sum(term in other.body for other in self.chunks) + 1))) for term in terms)
            ranked.append((score, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1].record.line_start, item[1].record.chunk_id))
        chunks = [DocumentChunk(chunk_id=chunk.record.chunk_id, doc_id=self.document.doc_id,
            title=self.document.title, doc_type="markdown", page=None, bbox=None,
            section=chunk.record.section, text=chunk.record.text, score=round(score, 6),
            source_uri=f"{SOURCE_PREFIX}{self.document.path}#L{chunk.record.line_start}-L{chunk.record.line_end}",
            retrieval_id=request.retrieval_id) for score, chunk in ranked[:request.top_k]]
        return RetrievalResponse(retrieval_id=request.retrieval_id, profile_id=request.profile_id, status="success", chunks=chunks)


def load_d12_index(root: Path, profile: BusinessProfile, *, max_chars: int = 1000) -> D12Index:
    root = root.resolve()
    manifest_path = (root / "source-manifest.json").resolve()
    manifest_path.relative_to(root)
    if manifest_path.stat().st_size > 65536:
        raise ValueError("oversized manifest")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate manifest key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("invalid manifest constant")

    manifest = KnowledgeManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_pairs, parse_constant=reject_constant))
    doc = next((item for item in manifest.documents if item.doc_id == "D12"), None)
    if doc is None:
        raise ValueError("D12 document missing")
    if manifest.archive_sha256 != ARCHIVE_SHA256 or manifest.source_archive != "chinook-business-agent-main.zip" or manifest.profile_id != profile.profile_id or profile.profile_id != "chinook-music":
        raise ValueError("knowledge version/profile mismatch")
    if doc.path != D12_FILENAME or doc.doc_id != "D12" or doc.sha256 != D12_SHA256 or doc.size_bytes != 10949 or doc.doc_type != "markdown" or doc.title != "经营查询与数据使用 FAQ" or doc.version != "V1.0" or doc.document_number != "CHN-FAQ-12" or doc.effective_date != "2025-07-01" or doc.source_entry != "chinook-business-agent-main/data/knowledge/" + D12_FILENAME:
        raise ValueError("knowledge metadata changed")
    path = (root / doc.path).resolve()
    path.relative_to(root)
    if not path.is_file() or path.stat().st_size != doc.size_bytes:
        raise ValueError("knowledge file missing or changed")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != doc.sha256:
        raise ValueError("knowledge hash changed")
    text = raw.decode("utf-8", errors="strict")
    chunks = chunk_markdown(text, doc_id=doc.doc_id, version_hash=doc.sha256, max_chars=max_chars)
    return D12Index(manifest, chunks, profile)
