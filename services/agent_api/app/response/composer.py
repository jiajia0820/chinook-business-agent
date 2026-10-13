"""Finite renderers backed by this-turn sources, not generated business claims."""

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_UP
import logging
import re
import unicodedata
import time

from ..contracts import ApiError, AskResponse, Intent
from ..evidence.binding import authorize_calculation
from ..evidence.models import CalculationEvidenceBundle
from ..evidence.business_workflows import WORKFLOWS, WorkflowGap, resolve_sources, conclude
from ..evidence.claim_comparison import ScalarClaim,compare_claims
from ..knowledge.d12 import D12_SHA256, SOURCE_PREFIX
from ..runtime import ToolContext, events_to_trace
from ..tools.local_calculator import FORMULAS, calculate_decimal
from ..tools.models import CalculationRequest
from .models import ComposeContext, ExecutionMode, normalize_answer_contract
from .document_answer import draft_answer, comparison, poster, model_messages, validate_model_answer
from .general_answer import general_model_messages, validate_general_answer
from .fast_renderer import fast_rag_enabled, local_answer, core_content_missing


logger = logging.getLogger("agent_api.response")


# Single-shot budget for the model-composed document answer: on expiry the
# composer silently falls back to an evidence extract, so it must cover real
# gateway latency. 60s matches the HTTP cap in sql_module.generation.
MODEL_ANSWER_SECONDS = 60


def rejection_reason(exc: Exception) -> str:
    """Short user-safe label for a dropped model answer.

    Validation failures raise ValueError with fixed literals, so their message
    is safe to surface; anything else (gateway/timeout) only exposes its type.
    """
    if isinstance(exc, ValueError) and str(exc):
        return str(exc)[:80]
    return type(exc).__name__


class EvidenceGap(ValueError):
    """No approved renderer or complete comparable evidence."""


class SourceMismatch(ValueError):
    """Untrusted/stale evidence must not be exposed as an accepted result."""


class EvidenceConflict(EvidenceGap):
    def __init__(self, description: str):
        self.description = description  # Constructed only from verified scalar records.


@dataclass(frozen=True)
class DocumentSnapshot:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str
    source_uri: str
    version: str
    page: int | None = None
    bbox: tuple[float, ...] | None = None


@dataclass(frozen=True)
class SourcePolicy:
    """Set by a trusted server factory, never taken from AskRequest."""
    execution_mode: ExecutionMode
    profile_id: str
    database_name: str | None = None
    documents: tuple[DocumentSnapshot, ...] = ()
    allow_business_calculation: bool = False
    model_mode: str = "offline"


BANNERS = {
    "fixture": "人工测试来源：旧 3B 工具均为固定结果替身，不是数据库、文档或实际计算器。",
    "offline_sql_integration": "来源：真实 Chinook 样例库 + C 固定离线三问；不是生产数据或通用 NL2SQL。",
    "offline_sql_d12_integration": "来源：真实 Chinook 样例库 + A 的 D12 V1.0 Markdown；仅有限问法，没有真实业务计算器。",
    "calculation_fixture": "人工测试来源：SQL 行、目标及公式文档是 fixture；仅 LocalCalculator 实际计算，不能当作真实销售或企业达标结论。",
}
LIVE_SQL_BANNER = "来源：真实 Chinook 样例库；SQL 经 C 只读安全校验执行。在线模式含大模型生成 SQL 候选及受控查询计划；本轮实际采用路径见 limitations/trace。"

# Match exactly C's accepted offline candidate shapes, not arbitrary SQL text.
SQL_SHAPES = {
    "客户数量是多少": ("SELECT COUNT(*) AS customer_count FROM main.Customer", {}, ["main.Customer"]),
    "美国客户数量是多少": ("SELECT COUNT(*) AS customer_count FROM main.Customer WHERE Country=:country", {"country": "USA"}, ["main.Customer"]),
    "有哪些音乐类型": ("SELECT GenreId, Name FROM main.Genre ORDER BY GenreId", {}, ["main.Genre"]),
}
SQL_NOTE = "展示 SQL 为 C 已接受候选；C 执行前会校验并规范化，不是逐字驱动语句。"
RAG_NOTE = "以上为知识库检索到的原文片段，未改写、未推断；请结合来源文档核对完整上下文。"


def question_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().rstrip("?？。")


def number(value: Decimal) -> str:
    return format(value, "f")


# 直接以自身列名出现在结果里的度量指标。
MEASURE_COLUMNS = frozenset({
    "customer_count", "sales_amount", "units_sold", "order_count", "purchasing_customers", "mixed_order_count",
})


def expected_result_evidence(metric_ids):
    """Map registered metrics to the result columns that must evidence them.

    A grouping metric such as genre_sales is answered by its measure column plus a
    dimension column, never by a column named after the metric itself.
    """
    expected, grouped = set(), False
    for metric in metric_ids:
        if metric == 'track_attributes':
            expected.update({'TrackId', 'Name', 'genre', 'media_type', 'unit_price'})
        elif metric in MEASURE_COLUMNS:
            expected.add(metric)
        elif metric.endswith("_sales") or metric == "playlist_track_count":
            if metric == 'playlist_track_count':
                expected.add('track_count')
            elif not set(metric_ids) & MEASURE_COLUMNS:
                expected.add('sales_amount')
            grouped = True
        else:
            expected.add(metric)
    return expected, grouped


class FormalResponseComposer:
    def __init__(self, policy: SourcePolicy, *, answer_model=None, executor=None):
        if policy.execution_mode not in BANNERS or not policy.profile_id:
            raise ValueError("unknown or unscoped source policy")
        if policy.execution_mode.startswith("offline_sql") and not policy.database_name:
            raise ValueError("real SQL needs a factory-confirmed database name")
        if policy.execution_mode == "offline_sql_d12_integration" and not policy.documents:
            raise ValueError("real D12 needs an already verified immutable catalog")
        if policy.execution_mode != "offline_sql_d12_integration" and policy.documents:
            raise ValueError("D12 catalog is not available in this source strategy")
        if policy.model_mode not in {"offline", "live"}:
            raise ValueError("unknown model mode")
        self.policy = policy
        self.answer_model = answer_model
        self.executor = executor
        self.execution_mode = policy.execution_mode
        self.source_banner = (
            LIVE_SQL_BANNER
            if policy.model_mode == "live" and self.execution_mode.startswith("offline_sql")
            else BANNERS[self.execution_mode]
        )
        if policy.allow_business_calculation:
            self.source_banner = (LIVE_SQL_BANNER if policy.model_mode == "live" else "来源：真实Chinook样例库；SQL为离线固定演示，不代表自由NL2SQL。") + " 已核验D01-D12资料；计算使用受控LocalCalculator。"

    @classmethod
    def for_sql(cls, profile):
        return cls(SourcePolicy("offline_sql_integration", profile.profile_id,
            profile.sql_backend.display_name, model_mode=profile.sql_backend.model_mode))

    @classmethod
    def for_sql_d12(cls, profile, index):
        if index.profile_id != profile.profile_id or index.document.sha256 != D12_SHA256 or index.document.version != "V1.0":
            raise ValueError("D12 factory snapshot mismatch")
        doc = index.document
        records = tuple(DocumentSnapshot(item.record.chunk_id, doc.doc_id, doc.title, item.record.section,
            item.record.text, f"{SOURCE_PREFIX}{doc.path}#L{item.record.line_start}-L{item.record.line_end}", doc.version)
            for item in index.chunks)
        return cls(SourcePolicy("offline_sql_d12_integration", profile.profile_id,
            profile.sql_backend.display_name, records, model_mode=profile.sql_backend.model_mode))

    @classmethod
    def for_sql_knowledge(cls, profile, index, *, answer_model=None, executor=None):
        records = tuple(
            DocumentSnapshot(
                item.record.chunk_id, item.record.doc_id, item.record.title,
                item.record.section or "正文", item.record.text,
                item.record.source_uri, item.record.version,
                item.record.page, tuple(item.record.bbox) if item.record.bbox else None,
            )
            for item in index.chunks
        )
        composer = cls(SourcePolicy("offline_sql_d12_integration", profile.profile_id,
            profile.sql_backend.display_name, records, True, profile.sql_backend.model_mode),
            answer_model=answer_model, executor=executor)
        return composer

    async def prepare(self, context: ComposeContext) -> ComposeContext:
        if context.status == "answered" and context.route == "general":
            limits = list(context.limitations)
            if self.answer_model is None or self.executor is None:
                return replace(context, status="insufficient_evidence", limitations=tuple([
                    *limits, "当前未配置通用回答模型，未调用业务数据库或业务文档代答。"
                ]))
            deadline = time.monotonic() + MODEL_ANSWER_SECONDS
            try:
                answer = await self.executor.run(
                    lambda: validate_general_answer(self.answer_model.generate_json(
                        general_model_messages(context.task.original_question), {"type": "object"}, deadline
                    )),
                    deadline=deadline,
                )
            except Exception as exc:
                note = f"通用回答模型输出未被接受（{rejection_reason(exc)}）。"
                logger.warning("%s", note)
                return replace(context, status="insufficient_evidence", limitations=tuple([*limits, note]))
            return replace(context, general_answer=answer, limitations=tuple(limits))
        if context.status != "answered" or context.route != "rag" or not self.policy.allow_business_calculation or not context.rag or not context.task:
            return context
        try:
            self._verify_sources(context)
        except SourceMismatch:
            return context
        question, documents = context.task.normalized_question, self._documents(context)
        answer = local_answer(question, documents) if fast_rag_enabled() else comparison(question, documents) or poster(question, documents)
        limits = list(context.limitations)
        if fast_rag_enabled():
            from ..knowledge.source_plan import rag_source_plan
            plan = rag_source_plan(question)
            missing_secondary = set(plan.secondary_docs + plan.context_docs) - {chunk.doc_id for chunk in documents}
            if missing_secondary:
                limits.append("补充解释来源尚缺：" + "、".join(sorted(missing_secondary)) + "；已保留主要资料支持的结论。")
        missing_core = core_content_missing(question, documents) if fast_rag_enabled() else []
        if answer is None and not missing_core and self.answer_model is not None and self.executor is not None:
            deadline = time.monotonic() + MODEL_ANSWER_SECONDS
            def generate():
                payload = self.answer_model.generate_json(model_messages(question, documents), {"type": "object"}, deadline)
                return validate_model_answer(payload, documents)
            try:
                answer = await self.executor.run(generate, deadline=deadline)
            except Exception as exc:
                # Cancellation is not swallowed. Invalid/late model output
                # falls back to an evidence-based extract, never raw pages.
                answer = None
                note = f"文档合成模型输出被拒（{rejection_reason(exc)}），已降级为证据摘录作答。"
                limits.append(note)
                logger.warning("%s", note)
        answer = answer or (local_answer(question, documents) if fast_rag_enabled() else draft_answer(question, documents))
        if answer is None:
            return replace(context, status="insufficient_evidence", parser_reason=None,
                           limitations=(*limits, "现有文档片段未覆盖问题所需事实。"))
        return replace(context, document_answer=answer, limitations=tuple(limits))

    @classmethod
    def for_calculation_fixture(cls, profile):
        return cls(SourcePolicy("calculation_fixture", profile.profile_id))

    def _tool_refs(self, context, tool):
        return {ref for event in context.events if event.tool == tool and event.event == "end" and event.status == "success" for ref in event.source_refs}

    def _verify_sources(self, context):
        if context.task and context.task.intent in WORKFLOWS and context.retrievals:
            return
        if context.profile.profile_id != self.policy.profile_id:
            raise SourceMismatch()
        if context.status == "answered" and (context.task is None or context.task.route != context.route):
            raise SourceMismatch()
        real = self.execution_mode.startswith("offline_sql")
        if context.sql:
            if context.sql.profile_id != self.policy.profile_id:
                raise SourceMismatch()
            for result in context.sql.sql_results:
                if result.profile_id != self.policy.profile_id:
                    raise SourceMismatch()
                if result.status == "success":
                    if result.query_id not in self._tool_refs(context, "sql.task") or result.query_id not in context.available_source_refs:
                        raise SourceMismatch()
                    if real and (result.source.type != "database" or result.source.name != self.policy.database_name or result.query_id.startswith("fixture-")):
                        raise SourceMismatch()
                    if self.execution_mode in {"fixture", "calculation_fixture"} and (result.source.type != "fixture" or not result.query_id.startswith("fixture-") or result.source.tables):
                        raise SourceMismatch()
        if context.rag:
            for retrieval in context.retrievals:
                self._verify_sources(replace(context, rag=retrieval, retrievals=()))
            if context.rag.profile_id != self.policy.profile_id:
                raise SourceMismatch()
            seen = {}
            for chunk in context.rag.chunks:
                previous = seen.get(chunk.chunk_id)
                if previous is not None:
                    # Retrieval fan-out may return the same authorized chunk
                    # more than once. Accept only equivalent duplicates so
                    # source authorization remains strict.
                    source_fields = ("chunk_id", "doc_id", "title", "section", "text", "source_uri", "page", "bbox", "doc_type")
                    if any(getattr(chunk, field) != getattr(previous, field) for field in source_fields):
                        raise SourceMismatch()
                    continue
                if chunk.chunk_id not in context.available_source_refs:
                    raise SourceMismatch()
                seen[chunk.chunk_id] = chunk
                if real:
                    snapshots = {item.chunk_id: item for item in self.policy.documents}
                    item = snapshots.get(chunk.chunk_id)
                    if item is None or (chunk.doc_id, chunk.title, chunk.section, chunk.text, chunk.source_uri) != (item.doc_id, item.title, item.section, item.text, item.source_uri):
                        raise SourceMismatch()
                    if (chunk.page, tuple(chunk.bbox) if chunk.bbox else None) != (item.page, item.bbox):
                        raise SourceMismatch()
                elif self.execution_mode in {"fixture", "calculation_fixture"} and (chunk.doc_type != "fixture" or not chunk.doc_id.startswith("fixture-") or not chunk.source_uri.startswith("fixture://")):
                    raise SourceMismatch()
        if context.calculations and self.execution_mode not in {"fixture", "calculation_fixture"} and not self.policy.allow_business_calculation:
            raise SourceMismatch()  # Real business calculation is not installed.

    def _sql_answer(self, context):
        if context.sql and any('SQL 快路径' in note for note in context.sql.limitations):
            return self._generic_sql_answer(context)
        task, sql = context.task, context.sql
        key = question_key(task.normalized_question)
        fixed_shape = False
        if key in SQL_SHAPES and sql and sql.status == "success":
            candidate, params, tables = SQL_SHAPES[key]
            fixed_shape = bool(sql.sql_results) and all(
                result.sql == candidate and result.params == params and result.source.tables == tables
                for result in sql.sql_results
            )
        if not fixed_shape:
            return self._generic_sql_answer(context)
        if task.needs_calculation or task.time_range or task.slots or context.rag:
            raise EvidenceGap()
        candidate, params, tables = SQL_SHAPES[key]
        expected_entities = [{"type": "country", "id": "Country:USA", "label": "美国"}] if key == "美国客户数量是多少" else []
        # Country labels are display-only; the ID and type fix the scope.
        if [(e.get("type"), e.get("id")) for e in task.entities] != [(e["type"], e["id"]) for e in expected_entities]:
            raise EvidenceGap()
        is_genres = key == "有哪些音乐类型"
        if task.intent != ("list_genres" if is_genres else "fact_query") or task.business_metric_ids != ([] if is_genres else ["customer_count"]):
            raise EvidenceGap()
        for result in sql.sql_results:
            if result.sql != candidate or result.params != params or result.source.tables != tables or result.row_count != len(result.rows) or result.row_count > context.options.max_rows or len(result.columns) != len(set(result.columns)) or any(set(row) != set(result.columns) for row in result.rows):
                raise EvidenceGap()
        if is_genres:
            if len(sql.sql_results) != 1:
                raise EvidenceGap()
            result = sql.sql_results[0]
            if result.columns != ["genreid", "name"] or not result.rows or any(type(row["genreid"]) is not int or row["genreid"] < 1 or type(row["name"]) is not str or not row["name"].strip() for row in result.rows):
                raise EvidenceGap()
            ids = [row["genreid"] for row in result.rows]
            if ids != sorted(set(ids)):
                raise EvidenceGap()
            lead = f"本轮返回 {result.row_count} 个 Genre 分类"
            lead += "（结果已截断，不能据此确定分类总数）" if result.truncated else "（该查询未截断）"
            names = "；".join(f'Genre:{row["genreid"]}: {row["name"]}' for row in result.rows)
            return f"{lead}：{names}。查询覆盖整个 Genre 表，含影视分类，不是仅音频风格清单。\n来源：SQL {result.query_id}。{SQL_NOTE}"
        values = []
        for result in sql.sql_results:
            if result.truncated or result.columns != ["customer_count"] or len(result.rows) != 1 or type(result.rows[0]["customer_count"]) is not int or result.rows[0]["customer_count"] < 0:
                raise EvidenceGap()
            values.append((result.query_id, result.rows[0]["customer_count"]))
        if not values:
            raise EvidenceGap()
        if len({value for _, value in values}) > 1:
            raise EvidenceConflict("同一已验证 Customer 查询范围出现冲突：" + "；".join(f"SQL {ref} = {value} 条" for ref, value in values) + "。未选择其中一个值，也未求平均。")
        scope = "Country=USA 的登记客户记录数" if params else "登记客户记录数"
        return f"Chinook 样例库中，{scope}为 {values[0][1]} 条。这里统计 Customer 表记录，不等同于筛选交易范围后的购买客户数。\n来源：" + "、".join(f"SQL {ref}" for ref, _ in values) + f"。{SQL_NOTE}"

    def _generic_document_answer(self, context, limit: int = 10):
        """Render retrieved passages verbatim instead of a single arbitrary chunk."""
        documents = self._documents(context)
        if not documents:
            raise EvidenceGap()
        if context.task and context.task.answer_obligations:
            from ..knowledge.facts import coverage
            facts = coverage(context.task, documents)
            if any(not item["complete"] for item in facts):
                raise EvidenceGap()
            references = list(dict.fromkeys(ref for item in facts for ref in item["source_refs"]))
            selected = [c for c in documents if c.chunk_id in references]
            if selected:
                lead = "已核验的资料事实（不代表数据库实测或运营动作已执行）："
                if context.task.assumptions:
                    lead += "\n用户前提尚未核验：" + "；".join(context.task.assumptions)
                return lead + "\n" + "\n\n".join(f"{c.text}\n来源：{c.doc_id} {c.source_uri}" for c in selected)
        if self.policy.allow_business_calculation:
            answer = context.document_answer or (local_answer(context.task.normalized_question, documents) if fast_rag_enabled() else draft_answer(context.task.normalized_question, documents))
            if answer is None:
                raise EvidenceGap()
            return answer
        seen, blocks = set(), []
        for chunk in documents:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            where = f" / {chunk.section}" if chunk.section else ""
            blocks.append(f"【{chunk.doc_id}《{chunk.title}》{where}】\n{chunk.text}\n来源：{chunk.source_uri}")
        doc_ids = sorted({chunk.doc_id for chunk in documents})
        # Preserve at least one visible passage from each required source.
        if context.retrievals:
            limit = len(blocks)
        lead = f"从 {'、'.join(doc_ids)} 检索到 {len(blocks)} 段相关原文，按相关度展示前 {min(limit, len(blocks))} 段："
        return lead + "\n\n" + "\n\n".join(blocks[:limit]) + "\n" + RAG_NOTE

    def _documents(self, context):
        responses = context.retrievals or ((context.rag,) if context.rag else ())
        documents, seen = [], set()
        for retrieval in responses:
            for chunk in retrieval.chunks:
                if chunk.chunk_id not in seen:
                    seen.add(chunk.chunk_id)
                    documents.append(chunk)
        return documents

    def _partial_document_answer(self, context):
        """Keep grounded document facts visible when coverage is incomplete."""
        if not context.rag or not context.rag.chunks or not context.task or context.task.intent in WORKFLOWS:
            return None
        documents = self._documents(context)
        answer = context.document_answer or (local_answer(context.task.original_question, documents) if fast_rag_enabled() else draft_answer(context.task.original_question, documents))
        if not answer:
            return None
        return "已确认部分：\n" + answer + "\n\n完整结论暂不能确认：本轮文档覆盖不完整，请补充或核对缺少的资料。"

    def _cross_answer(self, context):
        if "customer_tiers" in context.task.required_outputs:
            from ..agent.cross_templates import thresholds, completed
            docs = self._documents(context)
            bounds = thresholds(docs)
            if not bounds or not completed(context.task, context.sql, docs):
                raise EvidenceGap()
            a, b, ref = bounds
            rows = context.sql.sql_results[0].rows
            records = []
            for row in rows:
                amount = Decimal(str(row["sales_amount"]))
                tier = "A" if amount >= a else "B" if amount >= b else "C"
                records.append(f"客户 {row['CustomerId']}：所选品类金额 {row['selected_genre_amount']} USD；全部音频金额 {amount} USD；金额落层 {tier}层")
            selected = [str(r["CustomerId"]) for r in rows if Decimal(str(r["sales_amount"])) >= a]
            return "按D04金额阈值属于A层的客户：" + ("、".join(selected) or "无") + "。\n" + "\n".join(records) + f"\n分层依据：{ref}。购买品类用于筛选客户，落层按同季度全部音频金额；B层频次升级须客户支持岗确认，未自动认定升级。\n" + self._generic_document_answer(context)
        if "monthly_actuals" in context.task.required_outputs:
            # Independent reported figures never overwrite database actuals.
            import re
            rows = context.sql.sql_results[0].rows if context.sql else []
            conflicts = []
            for c in self._documents(context):
                if c.doc_id == "D08" and not context.task.entities and context.task.slots.get("media_ids") == [1, 2, 4, 5] and context.task.slots.get("year") == 2025 and context.task.slots.get("quarter") == 3:
                    for m, value in re.findall(r"([789])月(?:音频)?销售额\s*(\d+\.\d{2})美元", c.text):
                        observed = next((r["sales_amount"] for r in rows if r["month"].endswith("-0" + m)), None)
                        if observed is not None and Decimal(str(observed)) != Decimal(value):
                            conflicts.append(f"{m}月：数据库实际 {observed} USD；D08报告实际 {value} USD（{c.source_uri}）")
            if conflicts:
                raise EvidenceConflict("同期间/全音频金额存在来源冲突，需核对：\n" + "\n".join(dict.fromkeys(conflicts)) + "\n未平均、未覆盖任一来源；整题未标完整成功。")
        if context.task.needs_calculation:
            if not context.calculation_records or len(context.calculation_records) != len(context.calculations):
                return self._calculation_answer(context)
            from ..agent.state import BusinessTask
            from ..tools.models import CalculationRequest
            blocks, verdicts = [], []
            for calc, record in zip(context.calculations, context.calculation_records):
                child = BusinessTask.model_validate(record["task"])
                binding = CalculationRequest.model_validate(record["binding"])
                # Reuse every old numeric/source/authorization check per item.
                selected_rag = context.rag
                blocks.append(self._calculation_answer(replace(context, task=child, rag=selected_rag, calculations=(calc,), binding=binding, binding_audit=record["audit"], calculation_records=())))
                if binding.function == "attainment_rate":
                    actual, target = binding.operands["actual"].value, binding.operands["target"].value
                    verdicts.append(f"{child.business_metric_ids[0]}：{'达标' if actual >= target else '未达标'}（以未提前四舍五入的实际值判定）。")
            if context.task.intent == 'growth_rate' and any(chunk.doc_id == 'D02' for chunk in self._documents(context)):
                verdicts.append('D02解读：增长率用于比较本期与基期，目标达成率用于比较实际与目标，两种判断需要分开说明，不能用增长替代达标结论。')
            return "\n\n".join([*blocks, *verdicts, self._generic_document_answer(context)])
        if not context.sql or not self._documents(context):
            raise EvidenceGap()
        task = context.task.model_copy(deep=True)
        task.route, task.needs_calculation = "sql", False
        structured = self._generic_sql_answer(replace(context, task=task, route="sql", rag=None, retrievals=()))
        if "transaction_exists" in context.task.required_outputs:
            structured += "\n逐商品成交状态：" + "；".join(f"TrackId {r['TrackId']} {'有成交' if r['units_sold'] > 0 else '无成交'}" for r in context.sql.sql_results[0].rows)
        return structured + "\n\n资料规则/建议（不代表动作已执行）：\n" + self._generic_document_answer(context)

    def _generic_sql_answer(self, context):
        task, sql = context.task, context.sql
        if task.needs_calculation or context.rag or not sql or sql.status != "success" or not sql.sql_results:
            raise EvidenceGap()
        labels = {
            "customer_count": "客户数量",
            "sales_amount": "销售额",
            "units_sold": "销量",
            "order_count": "订单数",
            "purchasing_customers": "购买客户数",
            "genre": "品类",
            "billing_country": "账单国家",
            "TrackId": "商品 ID",
            "AlbumId": "专辑 ID",
            "ArtistId": "艺术家 ID",
            "MediaTypeId": "媒体类型 ID",
            "media_type": "媒体类型",
            "CustomerId": "客户编号",
            "customer": "客户",
            "unit_price": "标价",
            "Name": "名称",
            "Title": "标题",
        }
        units = {
            "customer_count": "人",
            "sales_amount": "USD",
            "units_sold": "件",
            "order_count": "单",
            "purchasing_customers": "人",
            "unit_price": "USD",
        }
        expected, grouped = expected_result_evidence(task.business_metric_ids)
        rendered_results = []
        for result in sql.sql_results:
            if (not result.sql or not result.source.tables or result.row_count != len(result.rows)
                    or not result.rows or len(result.columns) != len(set(result.columns))
                    or any(set(row) != set(result.columns) for row in result.rows)):
                raise EvidenceGap()
            returned_columns = set(result.columns)
            if expected and not expected <= returned_columns:
                raise EvidenceGap()
            coverage_columns = {
                "customer_ranking": {"customerid", "customer_id", "customer"},
                "support_distribution": {"supportrep", "supportrepid", "support_rep", "employeeid"},
                "album_details": {"album", "album_title", "albumid"},
                "artist_details": {"artist", "artist_name", "artistid"},
            }
            for obligation in task.required_outputs:
                if obligation in coverage_columns and not coverage_columns[obligation] & {column.casefold() for column in returned_columns}:
                    raise EvidenceGap()
            if any(row.get(name) is None for row in result.rows for name in expected):
                raise EvidenceGap()  # NULL is not a successful zero-valued observation.
            # 分组指标必须真的带出维度列，否则退化成一行合计，不能当作分组答案。
            if grouped and not returned_columns - expected:
                raise EvidenceGap()

            def render_row(row):
                def display(name):
                    value = row[name]
                    if units.get(name) == "USD" and type(value) in (int, float):
                        return format(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
                    return str(value)
                return "；".join(
                    f"{labels.get(name, name)} {display(name)}{(' ' + units[name]) if name in units else ''}"
                    for name in result.columns
                )

            if len(result.rows) == 1:
                rendered = render_row(result.rows[0])
            else:
                rendered = "\n" + "\n".join(
                    f"{index}. {render_row(row)}" for index, row in enumerate(result.rows, 1)
                )
            if result.truncated:
                rendered += f"\n结果按本轮上限截断，仅展示前 {result.row_count} 行。"
            rendered_results.append(f"{rendered}\n来源：SQL {result.query_id}。")
        scope = '；'.join(f"{entity['label']}（{entity['id']}）" for entity in task.entities)
        period = task.time_range.label if task.time_range else ''
        return f"查询口径：{period}；{scope or '当前确认范围'}。\n查询结果：" + "\n".join(rendered_results) + SQL_NOTE

    def _cross_source_answer(self, context):
        if context.task.needs_calculation or not context.rag or not context.rag.chunks:
            raise EvidenceGap()
        sql_answer = self._generic_sql_answer(replace(context, rag=None))
        notes = []
        monthly = {row['month']: Decimal(str(row['sales_amount']))
            for result in context.sql.sql_results for row in result.rows
            if isinstance(row.get('month'),str) and isinstance(row.get('sales_amount'),(int,float))}
        if monthly:
            from ..integrations.business_calculation import monthly_plan
            from ..evidence.binding import task_scope
            year = context.task.slots['year']
            plans = monthly_plan(context.rag.chunks, task_scope(context.task))
            if not plans:
                raise EvidenceGap()
            quarter=context.task.slots.get('quarter')
            if type(quarter) is not int or {month for month,_,_,_,_ in plans} != set(range((quarter-1)*3+1,quarter*3+1)):
                raise EvidenceGap()
            for _,_,chunk,_,_ in plans:
                stamp=re.search(r'(\d{4})Q([1-4])',chunk.title)
                if not stamp or (int(stamp[1]),int(stamp[2])) != (year,quarter):
                    raise EvidenceGap()
            notes.extend(f'{year}-{month:02} 计划 {value} USD；来源 {chunk.source_uri}' for month,value,chunk,_,_ in plans)
            for chunk in context.rag.chunks:
                if chunk.doc_id != 'D08':
                    continue
                for match in re.finditer(r'(?m)^(\d{4})年(\d{1,2})月\n(\d+\.\d+)',chunk.text):
                    key = f'{match[1]}-{int(match[2]):02}'
                    claim = Decimal(match[3])
                    scope=task_scope(context.task)
                    sql_entity='all_audio' if not scope.genre_ids and not scope.country_ids else str((scope.genre_ids,scope.country_ids))
                    left=ScalarClaim('sales_amount',sql_entity,key,'USD',tuple(scope.media_ids),'actual',monthly.get(key,Decimal(0)),'SQL')
                    right=ScalarClaim('sales_amount','all_audio',key,'USD',tuple(context.profile.default_media_ids),'actual',claim,chunk.source_uri)
                    comparison=compare_claims(left,right)
                    if key in monthly and comparison['status']=='conflict':
                        notes.append(f'来源冲突：{key}，D08文档写 {claim} USD，数据库为 {monthly[key]} USD。'
                            f"月份及音频范围一致，差异 {comparison['difference']} USD；数据库用于本轮成交事实，保留文档原值供核对，不能静默覆盖。文档季度合计需同步核对。来源 {chunk.source_uri}")
                    elif key in monthly and comparison['status']=='not_comparable':
                        notes.append(f"D08 全部音频月度数值与本轮范围不一致（{','.join(comparison['differences'])}），不能据此认定数值冲突。来源 {chunk.source_uri}")
        lead=''
        if context.task.slots.get('query_intent',{}).get('dimension')=='genre':
            result=context.sql.sql_results[0]
            top=result.rows[0]
            if top.get('genre') and type(top.get('sales_amount')) in (int,float):
                lead=f"{context.task.time_range.label} 销售额最高的音频品类为 {top['genre']}，销售额 {top['sales_amount']:.2f} USD，事实依据：SQL {result.query_id}。\n"
        guidance=[]
        for chunk in context.rag.chunks:
            if chunk.doc_id=='D09' and '专题陈列' in chunk.text and '专辑' in chunk.text:
                guidance.append(f"运营建议（文档建议，未证明增长原因）：{chunk.text}\n建议来源：{chunk.source_uri}")
                break
        return lead+sql_answer+'\n'+'\n'.join(notes+guidance)+'\n'+self._generic_document_answer(context)

    def _business_answer(self,context):
        try:
            sources=resolve_sources(context.task,context.rag,context.profile)
            audit=conclude(sources,context.sql)
            if audit!=context.business_audit:
                raise WorkflowGap('本轮结论与当前来源、查询及判定过程不一致。')
        except WorkflowGap as exc:
            raise EvidenceConflict(str(exc)) from exc
        period=context.task.time_range.label
        decisions=audit['decisions']
        if sources['intent']=='customer_segmentation':
            eligible=[d for d in decisions if d['eligible']]
            lead=f"{period} 购买过 {context.task.entities[0]['label']} 的客户中，属于 A 层重点维护的客户共 {len(eligible)} 人。"
            lines=[f"CustomerId {d['CustomerId']}（{d['customer']}）：当季全部音频消费额 {d['amount']} USD，达到 D04 的 ≥{sources['threshold']} USD 阈值，判定为 A 层。金额来源：{d['sql_ref']}；购买人群来源：{d['cohort_ref']}。" for d in eligible]
            if not eligible: lines.append(audit.get('empty_reason') or '查询及比较成功，没有客户达到 A 层阈值。')
            lines.append(f"购买 {context.task.entities[0]['label']} 只决定入选人群；分层使用同季度全部音频消费额，不是该品类金额。已核对 {len(decisions)} 位去重客户，完整中间结果见 sql_results。")
            lines.append(f"规则来源：D04 {sources['rule_uri']}（阈值）；{sources['scope_uri']}（计算口径）；规则适用季度 {period}，引用 {sources['period_ref']}。")
        else:
            sold=sum(d['sold'] for d in decisions)
            lead=f"{period} 用户编号范围内符合 D05 候选及 D11 音频主页面条件的商品共 {len(decisions)} 个，其中 {sold} 个有成交。"
            lines=[f"TrackId {d['TrackId']}，{d['Name']}：{'有成交' if d['sold'] else '零成交（查询成功）'}，销售额 {d['amount']} USD。成交事实来源：{d['sql_ref']}；候选引用 {d['candidate_ref']}；主页面条件引用 {d['poster_ref']}。" for d in decisions]
            if not decisions: lines.append(audit['empty_reason']+'未查询商品成交，不能据此断言区间内全部商品零成交。')
            lines.append(f"候选来源：D05 {sources['candidate_uri']}；主页面条件来源：D11 {sources['poster_uri']}。集合由文档候选、海报条件与用户编号范围取交集，未将范围内所有整数当作候选。")
        if audit.get('query_id'): lines.append('数据库来源：SQL '+audit['query_id']+'。'+SQL_NOTE)
        lines.append('这些事实只证明历史购买/成交，不证明推荐被接受或活动造成增长。')
        return lead+'\n'+'\n'.join(lines)

    def _d12_answer(self, context):
        task, rag = context.task, context.rag
        if task.intent != "document_rule" or task.needs_calculation or context.sql or not rag or rag.status != "success" or task.time_range or task.entities or any(key not in {"media_ids", "target_metric"} for key in task.slots) or "media_ids" in task.slots and task.slots["media_ids"] != [1, 2, 4, 5]:
            raise EvidenceGap()
        key = question_key(task.normalized_question)
        if key in {"销售额口径是什么", "销售额定义是什么", "销售额怎么计算", "销售额规则是什么"} and task.business_metric_ids == ["sales_amount"]:
            heading, quote = "四 指标怎么理解", "销售额按SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity)计算，单位为美元。"
        elif key == "默认音乐范围是什么" and not task.business_metric_ids:
            heading, quote = "二 默认的音乐范围", "没有特别说明时，“音乐”“音频”“音乐销售额”默认只统计音频MediaTypeId 1、2、4、5。MediaTypeId 3是视频，单独统计。"
        elif key == "购买客户数定义是什么" and task.business_metric_ids == ["purchasing_customers"]:
            heading, quote = "四 指标怎么理解", "购买客户数按CustomerId去重。"
        else:
            raise EvidenceGap()
        matches = [chunk for chunk in rag.chunks if chunk.section.endswith(" / " + heading) and quote in chunk.text]
        if not matches:
            raise EvidenceGap()
        chunk = matches[0]
        version = next(item.version for item in self.policy.documents if item.chunk_id == chunk.chunk_id)
        return f"D12 原文：“{quote}”\n来源：{chunk.doc_id} {version}；{chunk.section}；{chunk.chunk_id}；{chunk.source_uri}。\n仅解释该条文档规则，不代表已查询销售或购买客户数，也不形成目标达成结论。"

    def _calculation_answer(self, context):
        if context.calculation_records:
            if len(context.calculation_records) != len(context.calculations):
                raise EvidenceGap()
            from ..agent.state import BusinessTask
            parts = []
            for record, calc in zip(context.calculation_records, context.calculations):
                binding = CalculationRequest.model_validate(record['binding'])
                if binding.calculation_id != calc.calculation_id:
                    raise EvidenceGap()
                parts.append(self._calculation_answer(replace(context,
                    task=BusinessTask.model_validate(record['task']),
                    calculations=(calc,), binding=binding, binding_audit=record['audit'],
                    calculation_records=(), calculation_audits=())))
            if context.task.intent == 'growth_rate' and any(chunk.doc_id == 'D02' for chunk in self._documents(context)):
                parts.append('D02解读：增长率用于比较本期与基期，目标达成率用于比较实际与目标，两种判断需要分开说明，不能用增长替代达标结论。')
            return '\n\n'.join([*parts, *self._monthly_source_conflicts(context)])
        if context.calculation_audits:
            if len(context.calculation_audits)!=len(context.calculations):
                raise EvidenceGap()
            parts=[]
            for item,calc in zip(context.calculation_audits,context.calculations):
                if item['calculation_id']!=calc.calculation_id:
                    raise EvidenceGap()
                from ..agent.state import BusinessTask
                parts.append(self._calculation_answer(replace(context,task=BusinessTask.model_validate(item['task']),
                    calculations=(calc,),binding=CalculationRequest.model_validate(item['binding']),binding_audit=item['audit'],calculation_audits=())))
            return '\n\n'.join(parts)
        if not context.task.needs_calculation or len(context.calculations) != 1 or context.binding is None or context.binding_audit is None:
            raise EvidenceGap()
        calc = context.calculations[0]
        try:
            bundle = CalculationEvidenceBundle.model_validate(context.binding_audit)
            authorized = authorize_calculation(bundle, task=context.task, sql=context.sql, rag=context.rag,
                profile=context.profile, context=ToolContext(request_id=context.request_id, session_id=context.session_id,
                    profile_id=context.profile.profile_id, available_source_refs=context.available_source_refs),
                calculation_id=calc.calculation_id,
                expected_mode="verified_business" if self.policy.allow_business_calculation else "fixture")
            request = authorized.request
            expected_inputs = list(dict.fromkeys([request.formula_ref, *(item.source_ref for item in request.operands.values())]))
            if request != context.binding or calc.calculation_id not in self._tool_refs(context, "calculator.calculate") or calc.formula != FORMULAS[request.function] or calc.inputs != expected_inputs or calc.unit != request.result_unit or type(calc.result) not in (int, float) or Decimal(str(calc.result)) != calculate_decimal(request):
                raise EvidenceGap()
        except Exception:
            raise EvidenceGap() from None
        verified_business = self.policy.allow_business_calculation
        labels = (
            {"actual": "实际值", "target": "目标值", "current": "本期值", "previous": "基期值"}
            if verified_business else
            {"actual": "人工实际值", "target": "人工目标", "current": "人工本期值", "previous": "人工基期值"}
        )
        rows = []
        for role, operand in request.operands.items():
            period = bundle.operands[role].period
            rows.append(f"{labels.get(role,{'sales':'销售额','orders':'订单数'}.get(role,role))} {number(operand.value)} {operand.unit}，周期 [{period.start}, {period.end})，来源 {operand.source_ref}")
        function = {"difference": "目标差额", "attainment_rate": "达成率", "growth_rate": "增长率",'average_order_value':'客单价','sum':'月度计划合计'}[request.function]
        scope = bundle.formula.scope
        entity_labels = {
            item.get("id"): item.get("label")
            for item in context.task.entities
            if item.get("id") and item.get("label")
        }
        genres = [
            f"{entity_labels.get(value, value)}（{value}）" for value in scope.genre_ids
        ]
        scope_text = f"指标 {bundle.formula.metric_id}；媒体 {scope.media_ids}；品类 {genres or '全部'}；地区 {scope.country_ids or '全部'}"
        result_prefix = "计算" if verified_business else "人工测试"
        conclusion = (
            "结果由本轮已验证 SQL、文档目标与公式绑定后计算。"
            if verified_business else
            "这仅验证算术与绑定，不能当作真实销售、达标或增长结论。"
        )
        conclusion += (" 未达标。" if calc.result < 100 else " 达标。") if request.function == "attainment_rate" else ""
        lead = f"{context.task.time_range.label if context.task.time_range else '已确认期间'}：{function} {calc.result:.2f} {calc.unit}。"
        if request.function == "attainment_rate":
            lead += "未达标。" if calc.result < 100 else "已达标。"
        contextual = []
        if getattr(bundle, "review", None):
            contextual.append("复盘解释来源：" + "；".join(f"{item.locator.doc_id} {item.locator.chunk_id}" for item in bundle.review))
        if getattr(bundle, "operation", None):
            contextual.append("运营解释来源：" + "；".join(f"{item.locator.doc_id} {item.locator.chunk_id}：{item.text}" for item in bundle.operation))
        missing_roles = list(getattr(bundle, "missing_roles", []))
        if missing_roles:
            contextual.append("缺少解释角色：" + "、".join(dict.fromkeys(missing_roles)) + "；已保留可验证计算结果。")
        return "\n".join([lead, *rows, scope_text, f"命名函数 {request.function}；公式 {calc.formula}；公式来源 {request.formula_ref}（{bundle.formula.locator.version}）。",
            f"{result_prefix}{function}：{calc.result:.2f}{'' if calc.unit == '%' else ' '}{calc.unit}；Decimal 计算，ROUND_HALF_UP 保留两位小数。", *contextual, f"计算来源：{calc.calculation_id}。{conclusion}"])

    def _monthly_source_conflicts(self, context):
        """Compare reported facts without replacing authorized SQL operands."""
        if not context.task or 'monthly_actuals' not in context.task.required_outputs or not context.sql:
            return []
        from ..evidence.binding import task_scope
        from ..integrations.business_calculation import target_period_matches
        scope = task_scope(context.task)
        if scope.genre_ids or scope.country_ids or scope.media_ids != context.profile.default_media_ids:
            return []
        monthly = {row['month']: Decimal(str(row['sales_amount']))
                   for result in context.sql.sql_results for row in result.rows
                   if isinstance(row.get('month'), str) and type(row.get('sales_amount')) in (int, float)}
        notes = []
        for chunk in self._documents(context):
            if chunk.doc_id != 'D08' or not target_period_matches(chunk, context.task):
                continue
            claims = [(f'{m[1]}-{int(m[2]):02}', Decimal(m[3]))
                      for m in re.finditer(r'(?m)^(\d{4})年(\d{1,2})月\s*\n\s*(\d+\.\d+)', chunk.text)]
            claims += [(f"{context.task.slots['year']}-{int(m[1]):02}", Decimal(m[2]))
                       for m in re.finditer(r'([1-9]|1[0-2])月(?:音频)?销售额\s*(\d+\.\d{2})美元', chunk.text)]
            for key, claim in claims:
                if key in monthly and claim != monthly[key]:
                    notes.append(f'来源冲突：{key}，数据库实际 {monthly[key]} USD，D08报告实际 {claim} USD。'
                                 f'两者为同期间全部音频销售额；保留文档原值供核对，未平均或覆盖数据库计算输入。来源：{chunk.source_uri}。')
        if notes:
            notes.append('数据库本轮季度合计 ' + str(sum(monthly.values())) + ' USD；月度来源冲突须连同报告季度合计核对。')
        return list(dict.fromkeys(notes))

    def compose(self, context: ComposeContext) -> AskResponse:
        status, answer, error = context.status, None, context.error
        banner = self.source_banner
        if self.policy.model_mode == 'live':
            if context.route == "general":
                banner = '来源：通用大模型知识回答；本轮未调用本地业务数据库或业务文档。'
            elif context.sql and context.sql.sql_results:
                if any('SQL 快路径' in text for text in context.sql.limitations):
                    banner = '来源：真实 Chinook 样例库；已注册参数化 SQL 经 C 只读安全校验执行，本轮未调用模型。'
                elif any('受控业务查询' in text for text in context.sql.limitations):
                    banner='来源：真实 Chinook 样例库 + 文档证据驱动的服务端受控业务查询；本业务步骤未调用模型。'
                elif any('受控查询' in text for text in context.sql.limitations):
                    banner = '来源：真实 Chinook 样例库 + 受控查询计划；模型失败或候选语义不完整时已修复，详见限制与追踪。'
            elif context.rag and context.rag.chunks:
                banner = '来源：经快照验证的业务知识库文档，详见本轮引用。'
            else:
                banner = '来源：当前业务配置与能力边界；本轮未形成数据库查询结论。'
        inherited = [text for text in context.limitations if text != self.source_banner]
        limits = [banner, *inherited, *(context.sql.limitations if context.sql else [])]
        sql_results = context.sql.sql_results if context.sql else []
        documents = self._documents(context)
        calculations = list(context.calculations)
        definitions = context.sql.metric_definitions if context.sql else []
        try:
            # Validate even when show_trace=false; hidden foreign events are unsafe too.
            trace = events_to_trace(context.events, request_id=context.request_id, session_id=context.session_id,
                profile_id=context.profile.profile_id, show_trace=context.options.show_trace)
        except ValueError:
            trace, status = [], "error"
            error = ApiError(code="INTERNAL_ERROR", message="本轮事件范围不一致，未输出混合来源答案。", retryable=False)
            sql_results, documents, calculations, definitions = [], [], [], []
        else:
            try:
                self._verify_sources(context)
            except SourceMismatch:
                if status == "answered":
                    status = "insufficient_evidence"
                    answer = "本轮来源未通过服务端来源策略或当前引用校验，未输出确定结论。"
                limits.append("来源策略或本轮引用不一致，已隐藏未获接受的结构化来源，未形成业务结论。")
                sql_results, documents, calculations, definitions = [], [], [], []
        if status == "answered":
            try:
                if self.execution_mode == "fixture":
                    answer = "旧 3B 固定结果替身完成；仅供历史流程验证，未形成真实业务结论。"
                elif self.execution_mode == "calculation_fixture":
                    answer = self._calculation_answer(context)
                elif context.task and context.task.intent in WORKFLOWS:
                    answer=self._business_answer(context)
                elif context.task and context.task.needs_calculation:
                    answer = self._calculation_answer(context)
                    if context.route == 'cross_source':
                        answer += '\n' + self._generic_document_answer(context)
                elif context.route == "general":
                    if not context.general_answer:
                        raise EvidenceGap()
                    answer = context.general_answer
                elif context.route == "sql":
                    answer = self._sql_answer(context)
                elif context.route == "cross_source" and self.policy.allow_business_calculation:
                    answer = self._cross_answer(context) if context.task and context.task.answer_obligations else self._cross_source_answer(context)
                elif context.route == "rag" and self.execution_mode == "offline_sql_d12_integration":
                    if not documents:
                        raise EvidenceGap()
                    if context.task.intent == "document_rule" and all(item.doc_id == "D12" for item in documents):
                        try:
                            answer = self._d12_answer(context)
                        except EvidenceConflict:
                            raise
                        except EvidenceGap:
                            # 专用文案只覆盖已确认的固定问法；其余 D12 命中按原文渲染，
                            # 不能因为文案没有配置就把已经检索到的证据判成不足。
                            answer = self._generic_document_answer(context)
                    else:
                        answer = self._generic_document_answer(context)
                else:
                    raise EvidenceGap()
            except EvidenceConflict as conflict:
                status, answer = "insufficient_evidence", conflict.description
                limits.append("已有同口径数值冲突，需要核对查询来源；未自动选值或合并。")
            except EvidenceGap:
                status = "insufficient_evidence"
                calculations = []  # An invalid binding/result must not remain a public success.
                limits.append("本轮结果未满足已确认的有限渲染/绑定规则；文档命中或有数值不等于完整语义证据。")
        if status == "clarification_required":
            answer = context.parser_reason or context.clarification.question
        elif status == "unsupported":
            answer = context.parser_reason or "当前工具明确不支持这项任务，未生成业务结论。"
        elif status == "insufficient_evidence" and answer is None:
            if context.route == "rag":
                answer = self._partial_document_answer(context)
                if answer is None:
                    answer = "本轮证据、适用范围或计算绑定不完整，不能形成确定结论。"
            elif context.route == "general":
                answer = "当前未能生成通用回答，请检查大模型配置或稍后重试。"
            elif context.task and context.task.intent in WORKFLOWS:
                answer=context.parser_reason or '缺少必要规则、候选或数据库查询，业务链路尚未完成。'
            elif context.task and {"purchasing_customers", "customer_count"} <= set(context.task.business_metric_ids):
                answer = "D12 未直接定义登记客户记录数，现有证据不能完整回答它与购买客户数的区别；需要 A 补充正式字段口径来源。"
            else:
                answer = "本轮证据、适用范围或计算绑定不完整，不能形成确定结论。"
        elif status == "error":
            answer = "本轮处理失败，未输出成功数值结论；请查看结构化 error。"
        if status != "answered":
            calculations = []
        if context.route == "rag" and self.policy.allow_business_calculation:
            banner = "来源：真实 D01–D12 业务文档；下方可查看原文和定位。"
        elif self.policy.model_mode == "live" and not context.sql:
            banner = "来源：本轮业务资料检索；本轮未调用大模型生成SQL。"
            limits = [item for item in limits if item != self.source_banner]
        answer = banner + "\n" + normalize_answer_contract(answer)
        return AskResponse(request_id=context.request_id, session_id=context.session_id, profile_id=context.profile.profile_id,
            status=status, answer=answer, route=context.route, intent=Intent(name=context.task.intent if context.task else "reset_context" if context.reset_context else "unknown"),
            entities=context.task.entities if context.task else [], time_range=context.task.time_range if context.task else None,
            sql_results=sql_results, documents=documents, calculations=calculations, metric_definitions=definitions,
            limitations=list(dict.fromkeys(limits)), clarification=context.clarification,
            trace=trace, error=error if status == "error" else None)
