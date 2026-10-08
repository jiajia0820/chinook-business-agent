"""Real C SQL + A D12 instance, used by the offline API lifespan; no Calculator."""

from dataclasses import dataclass
from pathlib import Path

from ..contracts import ApiError, RetrievalRequest, RetrievalResponse
from ..core.errors import ApplicationError
from ..knowledge.d12 import KNOWLEDGE_ROOT, D12Index, load_d12_index
from ..profiles import ProfileRegistry
from ..runtime import ToolContext, ToolSpec
from .c_offline import OfflineSqlIntegration, create_offline_sql_integration


class D12RagAdapter:
    def __init__(self, index: D12Index, executor):
        self.index, self.executor = index, executor

    async def run(self, request: RetrievalRequest, context: ToolContext) -> RetrievalResponse:
        if request.profile_id != context.profile_id:
            return RetrievalResponse(retrieval_id=request.retrieval_id, profile_id=context.profile_id,
                status="failed", error=ApiError(code="INVALID_REQUEST", message="检索调用范围不一致。", retryable=False))
        return await self.executor.run(lambda: self.index.retrieve(request), deadline=context.deadline)


@dataclass
class OfflineKnowledgeIntegration:
    sql_integration: OfflineSqlIntegration
    rag_tool: D12RagAdapter

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
        composer = FormalResponseComposer.for_sql_d12(self.profiles.get("chinook-music"), self.rag_tool.index)
        return GraphAskService(self.profiles, self.runtime, evidence_resolver=UnavailableEvidenceResolver(),
            response_composer=composer, execution_mode="offline_sql_d12_integration")

    async def aclose(self, *, grace_seconds: float = 10) -> bool:
        return await self.sql_integration.aclose(grace_seconds=grace_seconds)


async def create_offline_knowledge_integration(*, profiles: ProfileRegistry | None = None,
    knowledge_root: Path | None = None, vendor_root: Path | None = None,
    trusted_tables: frozenset[str] | None = None, max_workers: int = 2,
    sql_timeout_ms: int = 60000, rag_timeout_ms: int = 5000, max_chars: int = 1000) -> OfflineKnowledgeIntegration:
    """Factory parameters are trusted server settings, never client paths."""
    sql = None
    try:
        root = (knowledge_root or KNOWLEDGE_ROOT).resolve()
        if profiles is None:
            profile = ProfileRegistry.defaults().get("chinook-music")
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
        sql = await create_offline_sql_integration(profiles=profiles, vendor_root=vendor_root,
            trusted_tables=trusted_tables, max_workers=max_workers, timeout_ms=sql_timeout_ms)
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
