"""Finite renderers backed by this-turn sources, not generated business claims."""

from dataclasses import dataclass
from decimal import Decimal
import unicodedata

from ..contracts import ApiError, AskResponse, Intent
from ..evidence.binding import authorize_calculation
from ..evidence.models import CalculationEvidenceBundle
from ..knowledge.d12 import D12_SHA256, SOURCE_PREFIX
from ..runtime import ToolContext, events_to_trace
from ..tools.local_calculator import FORMULAS, calculate_decimal
from .models import ComposeContext, ExecutionMode


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
LIVE_SQL_BANNER = "来源：真实 Chinook 样例库 + 大模型生成 SQL；候选经 C 只读安全校验后执行。"

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
    "customer_count", "sales_amount", "units_sold", "order_count", "purchasing_customers",
})


def expected_result_evidence(metric_ids):
    """Map registered metrics to the result columns that must evidence them.

    A grouping metric such as genre_sales is answered by its measure column plus a
    dimension column, never by a column named after the metric itself.
    """
    expected, grouped = set(), False
    for metric in metric_ids:
        if metric in MEASURE_COLUMNS:
            expected.add(metric)
        elif metric.endswith("_sales") or metric == "playlist_track_count":
            expected.add("sales_amount" if metric.endswith("_sales") else "playlist_track_count")
            grouped = True
        else:
            expected.add(metric)
    return expected, grouped


class FormalResponseComposer:
    def __init__(self, policy: SourcePolicy):
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
        self.execution_mode = policy.execution_mode
        self.source_banner = (
            LIVE_SQL_BANNER
            if policy.model_mode == "live" and self.execution_mode.startswith("offline_sql")
            else BANNERS[self.execution_mode]
        )

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
    def for_sql_knowledge(cls, profile, index):
        records = tuple(
            DocumentSnapshot(
                item.record.chunk_id, item.record.doc_id, item.record.title,
                item.record.section or "正文", item.record.text,
                item.record.source_uri, item.record.version,
            )
            for item in index.chunks
        )
        return cls(SourcePolicy("offline_sql_d12_integration", profile.profile_id,
            profile.sql_backend.display_name, records, True, profile.sql_backend.model_mode))

    @classmethod
    def for_calculation_fixture(cls, profile):
        return cls(SourcePolicy("calculation_fixture", profile.profile_id))

    def _tool_refs(self, context, tool):
        return {ref for event in context.events if event.tool == tool and event.event == "end" and event.status == "success" for ref in event.source_refs}

    def _verify_sources(self, context):
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
            if context.rag.profile_id != self.policy.profile_id:
                raise SourceMismatch()
            seen = set()
            for chunk in context.rag.chunks:
                if chunk.chunk_id in seen or chunk.retrieval_id != context.rag.retrieval_id or chunk.chunk_id not in self._tool_refs(context, "rag.retrieve") or chunk.chunk_id not in context.available_source_refs:
                    raise SourceMismatch()
                seen.add(chunk.chunk_id)
                if real:
                    snapshots = {item.chunk_id: item for item in self.policy.documents}
                    item = snapshots.get(chunk.chunk_id)
                    if item is None or (chunk.doc_id, chunk.title, chunk.section, chunk.text, chunk.source_uri) != (item.doc_id, item.title, item.section, item.text, item.source_uri):
                        raise SourceMismatch()
                elif self.execution_mode in {"fixture", "calculation_fixture"} and (chunk.doc_type != "fixture" or not chunk.doc_id.startswith("fixture-") or not chunk.source_uri.startswith("fixture://")):
                    raise SourceMismatch()
        if context.calculations and self.execution_mode not in {"fixture", "calculation_fixture"} and not self.policy.allow_business_calculation:
            raise SourceMismatch()  # Real business calculation is not installed.

    def _sql_answer(self, context):
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
            names = "；".join(f'{row["genreid"]}: {row["name"]}' for row in result.rows)
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

    def _generic_document_answer(self, context, limit: int = 3):
        """Render retrieved passages verbatim instead of a single arbitrary chunk."""
        documents = context.rag.chunks if context.rag else []
        if not documents:
            raise EvidenceGap()
        seen, blocks = set(), []
        for chunk in documents:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            where = f" / {chunk.section}" if chunk.section else ""
            blocks.append(f"【{chunk.doc_id}《{chunk.title}》{where}】\n{chunk.text}\n来源：{chunk.source_uri}")
        doc_ids = sorted({chunk.doc_id for chunk in documents})
        lead = f"从 {'、'.join(doc_ids)} 检索到 {len(blocks)} 段相关原文，按相关度展示前 {min(limit, len(blocks))} 段："
        return lead + "\n\n" + "\n\n".join(blocks[:limit]) + "\n" + RAG_NOTE

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
            "Name": "名称",
            "Title": "标题",
        }
        units = {
            "customer_count": "人",
            "sales_amount": "USD",
            "units_sold": "件",
            "order_count": "单",
            "purchasing_customers": "人",
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
            # 分组指标必须真的带出维度列，否则退化成一行合计，不能当作分组答案。
            if grouped and not returned_columns - expected:
                raise EvidenceGap()

            def render_row(row):
                return "；".join(
                    f"{labels.get(name, name)} {row[name]}{(' ' + units[name]) if name in units else ''}"
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
        return "查询结果：" + "\n".join(rendered_results) + SQL_NOTE

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
            rows.append(f"{labels[role]} {number(operand.value)} {operand.unit}，周期 [{period.start}, {period.end})，来源 {operand.source_ref}")
        function = {"difference": "目标差额", "attainment_rate": "达成率", "growth_rate": "增长率"}[request.function]
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
        return "\n".join([*rows, scope_text, f"命名函数 {request.function}；公式 {calc.formula}；公式来源 {request.formula_ref}（{bundle.formula.locator.version}）。",
            f"{result_prefix}{function}：{calc.result:.2f} {calc.unit}；Decimal 计算，ROUND_HALF_UP 保留两位小数。", f"计算来源：{calc.calculation_id}。{conclusion}"])

    def compose(self, context: ComposeContext) -> AskResponse:
        status, answer, error = context.status, None, context.error
        limits = [self.source_banner, *context.limitations, *(context.sql.limitations if context.sql else [])]
        sql_results = context.sql.sql_results if context.sql else []
        documents = context.rag.chunks if context.rag else []
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
                elif context.route == "sql":
                    answer = self._sql_answer(context)
                elif context.route == "cross_source" and self.policy.allow_business_calculation:
                    answer = self._calculation_answer(context)
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
            if context.task and {"purchasing_customers", "customer_count"} <= set(context.task.business_metric_ids):
                answer = "D12 未直接定义登记客户记录数，现有证据不能完整回答它与购买客户数的区别；需要 A 补充正式字段口径来源。"
            else:
                answer = "本轮证据、适用范围或计算绑定不完整，不能形成确定结论。"
        elif status == "error":
            answer = "本轮处理失败，未输出成功数值结论；请查看结构化 error。"
        if status != "answered":
            calculations = []
        answer = self.source_banner + "\n" + answer
        return AskResponse(request_id=context.request_id, session_id=context.session_id, profile_id=context.profile.profile_id,
            status=status, answer=answer, route=context.route, intent=Intent(name=context.task.intent if context.task else "reset_context" if context.reset_context else "unknown"),
            entities=context.task.entities if context.task else [], time_range=context.task.time_range if context.task else None,
            sql_results=sql_results, documents=documents, calculations=calculations, metric_definitions=definitions,
            limitations=list(dict.fromkeys(limits)), clarification=context.clarification,
            trace=trace, error=error if status == "error" else None)
