"""Manifest-locked lexical index for the complete D01-D12 snapshot."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

from ..contracts import ApiError, DocumentChunk, RetrievalRequest, RetrievalResponse
from ..profiles.models import BusinessProfile
from ..tools.models import InternalModel
from .documents import ChunkRecord, DocumentProcessingError, read_document


ARCHIVE_SHA256 = "74748f209e042eab3bc446491e6b6ad7ccf5138e5dad93b79221dfa55541e27d"
KNOWLEDGE_ROOT = Path(__file__).resolve().parents[4] / "data" / "knowledge"


class KnowledgeDocument(InternalModel):
    doc_id: str
    path: str
    source_entry: str
    sha256: str
    size_bytes: int
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
    documents: list[KnowledgeDocument]


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def lexical_terms(value: str) -> tuple[str, ...]:
    terms: list[str] = []
    for token in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", normalize(value)):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            terms.extend(token[i:i + 2] for i in range(len(token) - 1))
            if len(token) == 1:
                terms.append(token)
        else:
            terms.append(token)
    return tuple(dict.fromkeys(terms))


@dataclass(frozen=True)
class IndexedChunk:
    record: ChunkRecord
    normalized_text: str


def _manifest_json(path: Path) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate manifest key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("invalid manifest number")

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs,
                      parse_constant=reject_constant)


class KnowledgeIndex:
    def __init__(self, manifest: KnowledgeManifest, chunks: tuple[ChunkRecord, ...],
                 profile: BusinessProfile):
        if manifest.profile_id != profile.profile_id:
            raise ValueError("knowledge/profile mismatch")
        self.profile_id = manifest.profile_id
        self.manifest = manifest.model_copy(deep=True)
        self.profile = profile.model_copy(deep=True)
        self.documents = {doc.doc_id: doc for doc in manifest.documents}
        self.chunks = tuple(IndexedChunk(item, normalize(item.text)) for item in chunks)
        self._document_chunks = {
            doc_id: tuple(item for item in self.chunks if item.record.doc_id == doc_id)
            for doc_id in self.documents
        }
        if set(self.documents) != {f"D{i:02}" for i in range(1, 13)}:
            raise ValueError("knowledge manifest must contain D01-D12")
        if any(not self._document_chunks.get(doc_id) for doc_id in self.documents):
            raise ValueError("knowledge document has no chunks")
        self._idf, self._max_idf = self._build_idf()

    def _build_idf(self):
        """Inverse document frequency over chunks, so rare terms outweigh repeated common ones."""
        total = len(self.chunks)
        frequency: dict[str, int] = {}
        for item in self.chunks:
            for term in set(lexical_terms(item.normalized_text)):
                frequency[term] = frequency.get(term, 0) + 1
        idf = {term: math.log(1 + total / (1 + count)) for term, count in frequency.items()}
        return idf, math.log(2 + total)

    def _weight(self, term: str) -> float:
        return self._idf.get(term, self._max_idf)

    def retrieve(self, request: RetrievalRequest) -> RetrievalResponse:
        request = RetrievalRequest.model_validate(request.model_dump(mode="python"), strict=True)
        if request.profile_id != self.profile_id:
            return RetrievalResponse(
                retrieval_id=request.retrieval_id, profile_id=request.profile_id,
                status="failed", error=ApiError(code="PROFILE_NOT_FOUND", message="该知识索引不属于请求 profile。", retryable=False),
            )
        filters = request.filters
        if not set(filters) <= {"doc_id", "section", "business_metric_ids", "slots"}:
            return self._failure(request, "INVALID_REQUEST", "检索筛选条件不受支持。")
        doc_id = filters.get("doc_id")
        if doc_id is not None and doc_id not in self.documents:
            return self._failure(request, "INVALID_REQUEST", "文档筛选条件不受支持。")
        metric_ids = filters.get("business_metric_ids", [])
        if not isinstance(metric_ids, list) or any(metric not in {item.metric_id for item in self.profile.metric_definitions} for metric in metric_ids):
            return self._failure(request, "INVALID_REQUEST", "指标提示不属于当前 profile。")
        query_terms = set(lexical_terms(request.query))
        for metric_id in metric_ids:
            definition = self.profile.metric(metric_id)
            if definition:
                query_terms.update(lexical_terms(definition.name))
                query_terms.update(lexical_terms(definition.definition))
        if not query_terms:
            return RetrievalResponse(retrieval_id=request.retrieval_id, profile_id=self.profile_id, status="success", chunks=[])
        section = filters.get("section")
        ranked: list[tuple[float, IndexedChunk]] = []
        hints = {
            "指标": "D01", "销售分析": "D02", "分类": "D03", "商品范围": "D03",
            "目标": "D06", "达标": "D06", "增长率": "D01", "增长": "D01",
            "客户分层": "D04", "选品": "D05", "经营目标": "D06", "Q3经营目标": "D06",
            "Q2经营复盘": "D07", "Q3经营复盘": "D08", "品类运营": "D09",
            "活动方案": "D10", "报名截止": "D11", "海报": "D11", "FAQ": "D12",
            "默认音乐": "D12", "销售额口径": "D12",
        }
        hinted_doc = next((value for key, value in hints.items() if key.casefold() in request.query.casefold()), None)
        candidates = self.chunks if doc_id is None else self._document_chunks[doc_id]
        query_weight = sum(self._weight(term) for term in query_terms)
        for item in candidates:
            if section and not (item.record.section == section or (item.record.section or "").startswith(str(section) + " / ")):
                continue
            matched = [term for term in query_terms if term in item.normalized_text]
            if not matched:
                continue
            # 覆盖率按 IDF 权重计算：长问题会切出大量高频碎片词，
            # 用命中词数占比当门槛会让正常问法直接被筛空。
            if not hinted_doc and query_weight and sum(self._weight(term) for term in matched) / query_weight < 0.35:
                continue
            # 词频取对数，避免长段落靠重复高频词压过真正讲该主题的段落。
            score = sum(self._weight(term) * (1 + math.log(item.normalized_text.count(term))) for term in matched)
            if hinted_doc and item.record.doc_id == hinted_doc:
                # Configured whole-question vocabulary is a document-routing
                # signal. Keep lexical scoring for chunks inside that document,
                # but do not let a long recap win merely by repeating terms.
                score += 1_000_000
            if item.record.section and any(term in normalize(item.record.section) for term in query_terms):
                score += 3.0
            if any(term in normalize(item.record.title) for term in query_terms):
                score += 1.5
            ranked.append((float(score), item))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].record.doc_id, pair[1].record.chunk_id))
        chunks = [
            DocumentChunk(
                chunk_id=item.record.chunk_id, doc_id=item.record.doc_id,
                title=item.record.title, doc_type=item.record.doc_type,
                page=item.record.page, bbox=item.record.bbox,
                section=item.record.section, text=item.record.text,
                score=round(score, 6), source_uri=item.record.source_uri,
                retrieval_id=request.retrieval_id,
            )
            for score, item in ranked[:request.top_k]
        ]
        return RetrievalResponse(
            retrieval_id=request.retrieval_id, profile_id=self.profile_id,
            status="success", chunks=chunks,
        )

    @staticmethod
    def _failure(request: RetrievalRequest, code: str, message: str) -> RetrievalResponse:
        return RetrievalResponse(
            retrieval_id=request.retrieval_id, profile_id=request.profile_id,
            status="failed", error=ApiError(code=code, message=message, retryable=False),
        )


def load_knowledge_index(root: Path, profile: BusinessProfile, *, max_chars: int = 1200) -> KnowledgeIndex:
    root = root.resolve()
    manifest_path = (root / "source-manifest.json").resolve()
    manifest_path.relative_to(root)
    if not manifest_path.is_file() or manifest_path.stat().st_size > 128 * 1024:
        raise ValueError("manifest unavailable")
    manifest = KnowledgeManifest.model_validate(_manifest_json(manifest_path))
    if manifest.archive_sha256 != ARCHIVE_SHA256 or manifest.source_archive != "chinook-business-agent-main.zip":
        raise ValueError("knowledge archive mismatch")
    if manifest.profile_id != profile.profile_id:
        raise ValueError("knowledge profile mismatch")
    if len(manifest.documents) != 12 or len({doc.doc_id for doc in manifest.documents}) != 12:
        raise ValueError("knowledge manifest identity mismatch")
    chunks: list[ChunkRecord] = []
    for doc in manifest.documents:
        try:
            chunks.extend(read_document(root, doc.model_dump(mode="python"), max_chars=max_chars))
        except DocumentProcessingError:
            raise
    return KnowledgeIndex(manifest, tuple(chunks), profile)


def knowledge_snapshot_version(index: KnowledgeIndex) -> str:
    payload = "".join(f"{doc.doc_id}:{doc.sha256}:{doc.size_bytes}" for doc in index.manifest.documents)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
