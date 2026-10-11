"""Real C SQL + A D12 instance, used by the offline API lifespan; no Calculator."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..contracts import ApiError, RetrievalRequest, RetrievalResponse
from ..core.errors import ApplicationError
from ..knowledge.d12 import KNOWLEDGE_ROOT, D12Index, load_d12_index
from ..knowledge.index import KnowledgeIndex, load_knowledge_index
from ..evidence.models import CalculationEvidenceBundle, DocumentLocator, DocumentScalarEvidence, EvidencePeriod, EvidenceScope, FormulaEvidence, SqlScalarEvidence
from ..evidence.binding import result_hash, task_period, task_scope, comparison_period
from ..tools.references import sql_cell_ref
import hashlib
import re
from ..profiles import ProfileRegistry
from ..runtime import ToolContext, ToolSpec
from .c_offline import OfflineSqlIntegration, create_canonical_sql_integration, create_offline_sql_integration


class D12RagAdapter:
    def __init__(self, index: D12Index, executor):
        self.index, self.executor = index, executor

    async def run(self, request: RetrievalRequest, context: ToolContext) -> RetrievalResponse:
        if request.profile_id != context.profile_id:
            return RetrievalResponse(retrieval_id=request.retrieval_id, profile_id=context.profile_id,
                status="failed", error=ApiError(code="INVALID_REQUEST", message="检索调用范围不一致。", retryable=False))
        return await self.executor.run(lambda: self.index.retrieve(request), deadline=context.deadline)


class KnowledgeRagAdapter:
    def __init__(self, index: KnowledgeIndex, executor):
        self.index, self.executor = index, executor

    async def run(self, request: RetrievalRequest, context: ToolContext) -> RetrievalResponse:
        if request.profile_id != context.profile_id:
            return RetrievalResponse(
                retrieval_id=request.retrieval_id, profile_id=context.profile_id,
                status="failed", error=ApiError(code="INVALID_REQUEST", message="检索调用范围不一致。", retryable=False),
            )
        return await self.executor.run(lambda: self.index.retrieve(request), deadline=context.deadline)


def _business_evidence_resolver(index: KnowledgeIndex):
    snapshots = {item.record.chunk_id: item.record for item in index.chunks}
    def resolve(task, sql, rag, calculation_id, context):
        if not task.needs_calculation or not sql or not rag or not sql.sql_results or not rag.chunks:
            return None
        for chunk in rag.chunks:
            original = snapshots.get(chunk.chunk_id)
            if original is None or (chunk.doc_id, chunk.text, chunk.source_uri) != (original.doc_id, original.text, original.source_uri):
                return None  # Verify immutable source before issuing a permit.
        function = {"target_attainment": "attainment_rate", "target_difference": "difference", "growth_rate": "growth_rate"}.get(task.intent)
        if function is None:
            return None
        metric_id = task.business_metric_ids[0] if task.business_metric_ids else "sales_amount"
        period = task_period(task)
        scope = task_scope(task)
        prior = comparison_period(task) if function == "growth_rate" else None
        documents = {chunk.chunk_id: chunk for chunk in rag.chunks}
        metric = index.profile.metric(metric_id)
        if metric is None or metric_id not in {"sales_amount", "units_sold"}:
            return None
        unit = metric.unit
        target, numeric = None, None
        if function != "growth_rate":
            # A limited approved D06 row resolver: identity, period, scope and
            # units must match before taking a numeric span. No hardcoded value.
            monthly = task.slots.get("month")
            valid_period = (period.start == "2025-07-01" and period.end == "2025-10-01") or (task.slots.get("year") == 2025 and monthly in {7, 8, 9})
            if not valid_period or scope.media_ids != [1, 2, 4, 5] or scope.country_ids:
                return None
            if len(scope.genre_ids) == 1:
                row_pattern = r"Genre\s*:\s*" + re.escape(str(scope.genre_ids[0]).split(":")[-1]) + r"\s+(\d+\.\d{2})\s+(\d+)\s"
            elif not scope.genre_ids:
                row_pattern = r"全部音频\s+全部音频分类\s+(\d+\.\d{2})\s+(\d+)\s"
            else:
                return None
            found = []
            for chunk in rag.chunks:
                if chunk.doc_id == "D06" and "销售额目标 美元" in chunk.text and "销量目标 件" in chunk.text:
                    for match in re.finditer(row_pattern, chunk.text):
                        found.append((chunk, match))
            if not found and not scope.genre_ids:
                # The all-audio table row can fall into a separate PDF chunk.
                # Use only the explicit, unit-bearing whole-audio statement in
                # a currently retrieved immutable D06 chunk; never concatenate
                # batches or infer a target from sums / example numbers.
                paragraph = r"本季度全音频销售额目标确定为\s*(\d+\.\d{2})\s*美元\s*[，,]\s*销量目标\s*(\d+)\s*件"
                for chunk in rag.chunks:
                    if chunk.doc_id == "D06":
                        found.extend((chunk, match) for match in re.finditer(paragraph, chunk.text))
            if monthly and metric_id == "sales_amount":
                label = "全部音频" if not scope.genre_ids else next((a.label for a in index.profile.aliases if a.entity_id == scope.genre_ids[0]), None) if len(scope.genre_ids) == 1 else None
                found = []
                if not label:
                    return None
                for chunk in rag.chunks:
                    if chunk.doc_id == "D06" and "月度分解" in chunk.text and "7月金额 美元" in chunk.text:
                        pattern = re.escape(label) + r"\s+(\d+\.\d{2})\s+(\d+\.\d{2})\s+(\d+\.\d{2})"
                        found.extend((chunk, match) for match in re.finditer(pattern, chunk.text))
            # Overlapping context chunks may repeat the same source fact; only
            # identical values collapse, conflicting values never do.
            dedup = {}
            for chunk, match in found:
                dedup.setdefault(match.group(), (chunk, match))
            found = list(dedup.values())
            if len(found) != 1:
                return None
            target, row = found[0]
            group = monthly - 6 if monthly else 1 if metric_id == "sales_amount" else 2
            target_value = Decimal(row.group(group))
            numeric_start, numeric_end = row.span(group)
        if function == "growth_rate":
            formula_text = next((chunk for chunk in rag.chunks if chunk.doc_id == "D01" and ("period_over_period_growth" in chunk.text or "增长" in chunk.text)), None)
            if formula_text is None:
                return None
            locator = DocumentLocator(chunk_id=formula_text.chunk_id, doc_id=formula_text.doc_id, version="V1.0",
                content_sha256=hashlib.sha256(formula_text.text.encode("utf-8")).hexdigest(), source_uri=formula_text.source_uri,
                start=0, end=min(len(formula_text.text), 1200))
        else:
            if target is None:
                return None
            locator = DocumentLocator(chunk_id=target.chunk_id, doc_id=target.doc_id, version="V1.0",
                content_sha256=hashlib.sha256(target.text.encode("utf-8")).hexdigest(), source_uri=target.source_uri,
                start=numeric_start, end=numeric_end)
            label = "销售额差额" if function == "difference" else "销售额达成率"
            formula_text = next((chunk for chunk in rag.chunks if chunk.doc_id in {"D01", "D06"} and label in chunk.text and (metric_id == "sales_amount" or "销量目标按同样办法单独比较" in chunk.text)), None)
            if formula_text is None:
                return None
        formula_locator = DocumentLocator(chunk_id=formula_text.chunk_id, doc_id=formula_text.doc_id, version="V1.0",
            content_sha256=hashlib.sha256(formula_text.text.encode("utf-8")).hexdigest(), source_uri=formula_text.source_uri,
            start=0, end=max(1, len(formula_text.text)))
        formula = FormulaEvidence(function=function, metric_id=metric_id, period=period, scope=scope,
            input_unit=unit, result_unit=unit if function == "difference" else "%",
            confirmed=True, locator=formula_locator)
        result = sql.sql_results[0]
        if function == "growth_rate":
            if len(sql.sql_results) >= 2:
                current, previous = sql.sql_results[0], sql.sql_results[1]
                current_index = previous_index = 0
            elif len(sql.sql_results) == 1 and len(sql.sql_results[0].rows) >= 2:
                current, previous = sql.sql_results[0], sql.sql_results[0]
                current_index, previous_index = 0, 1
            else:
                return None
            operands = {
                "current": SqlScalarEvidence(metric_id=metric_id, period=period, scope=scope, unit=unit, value=Decimal(str(current.rows[current_index][metric_id])).quantize(Decimal("0.000001")), confirmed=True, query_id=current.query_id, row_index=current_index, column=metric_id, result_sha256=result_hash(current)),
                "previous": SqlScalarEvidence(metric_id=metric_id, period=prior, scope=scope, unit=unit, value=previous.rows[previous_index][metric_id], confirmed=True, query_id=previous.query_id, row_index=previous_index, column=metric_id, result_sha256=result_hash(previous)),
            }
        else:
            row_index = next((i for i, row in enumerate(result.rows) if row.get("month") == f"2025-{monthly:02}"), None) if monthly else 0 if len(result.rows) == 1 else None
            if row_index is None or result.rows[row_index].get(metric_id) is None:
                return None
            actual = Decimal(str(result.rows[row_index][metric_id])).quantize(Decimal("0.000001"))
            operands = {"actual": SqlScalarEvidence(metric_id=metric_id, period=period, scope=scope, unit=unit, value=actual, confirmed=True, query_id=result.query_id, row_index=row_index, column=metric_id, result_sha256=result_hash(result)),
                "target": DocumentScalarEvidence(metric_id=metric_id, period=period, scope=scope, unit=unit, value=target_value, confirmed=True, locator=locator)}
        return CalculationEvidenceBundle(request_id=context.request_id, session_id=context.session_id, profile_id=context.profile_id,
            calculation_id=calculation_id, source_mode="verified_business", function=function,
            operands=operands, formula=formula)
    return resolve


@dataclass
class OfflineKnowledgeIntegration:
    sql_integration: OfflineSqlIntegration
    rag_tool: D12RagAdapter
    knowledge_kind: str = "d12"

    @property
    def profiles(self):
        return self.sql_integration.profiles

    @property
    def runtime(self):
        return self.sql_integration.runtime

    @property
    def sql_tool(self):
        return self.sql_integration.sql_tool

    def create_graph_service(self):
        from ..evidence.models import UnavailableEvidenceResolver
        from ..services.graph_ask_service import GraphAskService
        return GraphAskService(self.profiles, self.runtime, evidence_resolver=UnavailableEvidenceResolver(), execution_mode="offline_sql_d12_integration")

    def create_formal_graph_service(self):
        """Compose against the same in-memory hash-verified D12 snapshot."""
        from ..evidence.models import UnavailableEvidenceResolver
        from ..response import FormalResponseComposer
        from ..services.graph_ask_service import GraphAskService
        composer = (
            FormalResponseComposer.for_sql_d12(self.profiles.get("chinook-music"), self.rag_tool.index)
            if self.knowledge_kind == "d12"
            else FormalResponseComposer.for_sql_knowledge(self.profiles.get("chinook-music"), self.rag_tool.index)
        )
        return GraphAskService(self.profiles, self.runtime, evidence_resolver=_business_evidence_resolver(self.rag_tool.index) if self.knowledge_kind == "knowledge" else UnavailableEvidenceResolver(),
            response_composer=composer, execution_mode="offline_sql_d12_integration")

    async def aclose(self, *, grace_seconds: float = 10) -> bool:
        return await self.sql_integration.aclose(grace_seconds=grace_seconds)


async def create_offline_knowledge_integration(*, profiles: ProfileRegistry | None = None,
    knowledge_root: Path | None = None, vendor_root: Path | None = None,
    trusted_tables: frozenset[str] | None = None, max_workers: int = 2,
    sql_timeout_ms: int = 60000, rag_timeout_ms: int = 5000, max_chars: int = 1000, model_mode: str = "offline") -> OfflineKnowledgeIntegration:
    """Factory parameters are trusted server settings, never client paths."""
    sql = None
    try:
        root = (knowledge_root or KNOWLEDGE_ROOT).resolve()
        if profiles is None:
            profile = ProfileRegistry.stage1().get("chinook-music")
            profile.sql_backend.model_mode = model_mode
            profile.capabilities = ["sql", "rag"]
            profile.document_root = "data/knowledge"
            profile.config_version += "+3c2-sql-d12"
            profile.limitations.extend([
                "3C-2 仅检索 A 的 D12 V1.0 原文；D12 未直接定义登记客户记录数，未解析 D06 等完整目标资料。",
                "文档词项命中不等于完整语义答案、目标适用范围或公式绑定；本步不进行达标/增长计算。",
                "此实例仅装配 SQL + RAG，不具备计算器或多格式解析。",
            ])
            for source in profile.sources:
                if source.ref_id == "D12":
                    source.status = "snapshot_verified"
                    source.description = "A 原包 D12 V1.0 字节/hash 已核对；B 切片与检索，不代表 A 正式 catalog 已补齐。"
            profiles = ProfileRegistry([profile])
        profile = profiles.get("chinook-music")
        if profile.mode != "hybrid" or profile.capabilities != ["sql", "rag"] or profile.document_root != "data/knowledge":
            raise ValueError("unexpected knowledge instance configuration")
        sql = await create_offline_sql_integration(profiles=profiles,
            trusted_tables=trusted_tables, max_workers=max_workers, timeout_ms=sql_timeout_ms, model_mode=model_mode)
        index = await sql.sql_tool.executor.run(lambda: load_d12_index(root, profile, max_chars=max_chars))
        adapter = D12RagAdapter(index, sql.sql_tool.executor)
        sql.runtime.register(ToolSpec(name="rag.retrieve", capability="rag",
            input_model=RetrievalRequest, output_model=RetrievalResponse, handler=adapter.run, timeout_ms=rag_timeout_ms))
        return OfflineKnowledgeIntegration(sql, adapter)
    except BaseException as exc:
        if sql is not None:
            await sql.aclose()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)) or not isinstance(exc, Exception):
            raise
        raise ApplicationError(status_code=503, error=ApiError(code="PROFILE_UNAVAILABLE",
            message="真实 SQL + D12 实例初始化失败，未回退到文档或 SQL 替身。", retryable=False,
            details={"reason": "DEPENDENCY_MISSING" if isinstance(exc, ImportError) else "CONFIG_OR_ARTIFACT_INVALID"})) from None


async def create_canonical_knowledge_integration(*, profiles: ProfileRegistry | None = None,
    knowledge_root: Path | None = None, trusted_tables: frozenset[str] | None = None,
    max_workers: int = 2, sql_timeout_ms: int = 60000, rag_timeout_ms: int = 5000,
    max_chars: int = 1200, model_mode: str = "offline") -> OfflineKnowledgeIntegration:
    sql = None
    try:
        root = (knowledge_root or Path(__file__).resolve().parents[4] / "data" / "knowledge").resolve()
        if profiles is None:
            profile = ProfileRegistry.defaults().get("chinook-music")
            profile.sql_backend.model_mode = model_mode
            profile.capabilities = ["sql", "rag", "calculate"]
            profiles = ProfileRegistry([profile])
        profile = profiles.get("chinook-music")
        sql = await create_canonical_sql_integration(profiles=profiles, trusted_tables=trusted_tables,
            max_workers=max_workers, timeout_ms=sql_timeout_ms, model_mode=model_mode)
        index = await sql.sql_tool.executor.run(lambda: load_knowledge_index(root, profile, max_chars=max_chars))
        adapter = KnowledgeRagAdapter(index, sql.sql_tool.executor)
        sql.runtime.register(ToolSpec(name="rag.retrieve", capability="rag",
            input_model=RetrievalRequest, output_model=RetrievalResponse, handler=adapter.run,
            timeout_ms=rag_timeout_ms))
        from ..tools.local_calculator import create_local_calculator_spec
        sql.runtime.register(create_local_calculator_spec(timeout_ms=5000))
        return OfflineKnowledgeIntegration(sql, adapter, knowledge_kind="knowledge")
    except BaseException as exc:
        if sql is not None:
            await sql.aclose()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)) or not isinstance(exc, Exception):
            raise
        raise ApplicationError(status_code=503, error=ApiError(code="PROFILE_UNAVAILABLE",
            message="全量 SQL + D01-D12 知识实例初始化失败。", retryable=False,
            details={"reason": "DEPENDENCY_MISSING" if isinstance(exc, ImportError) else "CONFIG_OR_ARTIFACT_INVALID"})) from None
