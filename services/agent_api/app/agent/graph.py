"""One LangGraph owns routing; runtime owns bounded, validated tool calls."""

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
import json
import os
import re
import uuid
import time
import asyncio
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from ..contracts import ApiError, AskRequest, AskResponse, Calculation, DocumentChunk, Intent, RetrievalRequest, RetrievalResponse
from ..evidence.binding import authorize_calculation, tampered_chunks
from ..evidence.models import EvidenceResolver
from ..evidence.business_workflows import WORKFLOWS, WorkflowGap, resolve_sources, conclude
from ..profiles import ProfileRegistry
from ..runtime import ToolContext, ToolEvent, ToolRuntime, events_to_trace
from ..response.models import ComposeContext, ResponseComposer
from ..tools.models import SQL_OBLIGATION_KEYS, BusinessContextItem, CalculationRequest, SqlTaskOutcome, SqlTaskRequest
from ..tools.references import sql_cell_ref
from .parsing import parse_question
from .semantic_router import parse_route_proposal, semantic_route_messages, task_from_proposal
from .state import AgentState, BusinessTask, ParsedTurn, SessionMemory, logical_copy


CalculationBinder = Callable[[BusinessTask, SqlTaskOutcome | None, RetrievalResponse | None, str], CalculationRequest | None]
SEMANTIC_ROUTE_SECONDS = 8


def _rag_plan(question: str, intent: str) -> dict:
    """Bounded source requirements, independent of SQL implementation details."""
    explicit_docs = list(dict.fromkeys(re.findall(r"D(?:0[1-9]|1[0-2])", question, flags=re.I)))
    sources = [value.upper() for value in explicit_docs]
    requirements: list[str] = []
    query_terms: list[str] = []
    from ..knowledge.source_plan import rag_source_plan
    source_plan = rag_source_plan(question, explicit_docs=explicit_docs)
    # When a question names documents explicitly, keep that bounded contract;
    # otherwise let the role planner supply authoritative sources.
    if not explicit_docs:
        sources.extend(source_plan.required_docs)
    requirements.extend({
        "metric_definition": "formula",
        "media_scope": "media_scope",
        "classification_boundary": "classification_boundary",
        "selection_rule": "selection_rule",
        "activity_plan": "activity_plan",
        "mixed_order_media_rule": "mixed_order_media_rule",
        "target_value": "target",
        "quarter_review": "review",
        "operation_rule": "operation_rule",
        "customer_segmentation": "customer_segmentation",
    }.get(role) for role in source_plan.required_roles)
    requirements = [item for item in requirements if item]
    query_terms.extend(source_plan.query_terms)
    if intent in {"target_difference", "target_attainment", "document_calculation"}:
        sources.extend(["D01", "D06", "D08"])
        requirements.extend(["actual", "target", "formula"])
    if intent == "growth_rate":
        sources.extend(["D01", "D07", "D08"])
        requirements.extend(["current", "previous", "formula"])
    if "复盘" in question and any(term in question for term in ("二季度", "第二季度", "Q2")):
        sources.append("D07")
        query_terms.extend(["第二季度", "Q2", "经营复盘", "销售额", "订单数", "购买客户数"])
    if "复盘" in question and any(term in question for term in ("三季度", "第三季度", "Q3")):
        sources.append("D08")
        query_terms.extend(["第三季度", "Q3", "经营复盘", "销售额", "订单数", "购买客户数"])
    if "二季度" in question or "第二季度" in question or "Q2" in question:
        if any(term in question for term in ("核心结果", "经营", "销售额")):
            sources.append("D07")
            query_terms.extend(["第二季度", "Q2", "经营复盘", "销售额", "订单数", "购买客户数"])
    if "运营" in question and any(term in question for term in ("手册", "动作", "建议")):
        sources.append("D09")
        requirements.append("operation_rule")
        if "销售额" in question or "目标" in question:
            sources.append("D08")
            requirements.append("actual")
    # Metric-definition questions can mention several common metric names.
    # When the question asks for the denominator or the distinction between
    # 客单价 and 成交均价, D02 is the authoritative analysis-method source.
    if any(term in question for term in ("客单价", "成交均价", "分母")):
        sources.append("D02")
        requirements.append("formula")
    # A category handbook is an explicit operation-rule source even when the
    # question does not contain the word “运营” (for example, when it asks
    # what the target table and handbook each tell the operator).
    if "品类手册" in question or ("手册" in question and any(item in question for item in ("Rock", "Metal", "Latin", "摇滚", "金属", "拉丁"))):
        sources.append("D09")
        requirements.append("operation_rule")
    if any(term in question for term in ("销售额最高", "最高的品类")):
        sources.append("D08")
        requirements.append("actual")
    if "客户分层" in question:
        sources.append("D04")
    if any(term in question for term in ("A层", "重点维护")):
        sources.append("D04")
    if "选品" in question:
        sources.append("D05")
    if "TrackId" in question:
        sources.append("D05")
    if any(term in question for term in ("MediaType", "媒体格式", "音乐商品", "范围差异")):
        sources.extend(["D01", "D03"])
        requirements.append("media_scope")
    if "目标" in question:
        sources.append("D06")
    if any(term in question for term in ("完成了吗", "目标完成", "达标")):
        sources.append("D08")
    if "未达" in question:
        sources.append("D08")
    if "每月" in question and "实际" in question:
        sources.extend(["D06", "D08"])
    if "复盘" in question and "变化" in question:
        sources.extend(["D07", "D08"])
    if "活动方案" in question:
        sources.append("D10")
    if "海报" in question:
        sources.extend(["D10", "D11"])
    return {"required_sources": list(dict.fromkeys(sources)), "evidence_requirements": list(dict.fromkeys(requirements)), "query_terms": list(dict.fromkeys(query_terms))}


def _snapshot(outcome):
    data = outcome.data.model_dump(mode="json", exclude={"diagnostics"}) if outcome.data is not None else None
    return {"status": outcome.status, "data": data, "error": outcome.error.model_dump(mode="json") if outcome.error else None}


def _sql_snapshot(outcome):
    """_snapshot 丢掉 diagnostics；执行路径和义务覆盖结论必须留在状态里供 compose 兜底。"""
    snapshot = _snapshot(outcome)
    diagnostics = outcome.data.diagnostics if outcome.data is not None else None
    snapshot["execution_path"] = (diagnostics or {}).get("execution_path", "none")
    snapshot["query_obligations_covered"] = (diagnostics or {}).get("query_obligations_covered")
    return snapshot


def _models(state):
    sql = state.get("sql_outcome")
    rag = state.get("retrieval_outcome")
    return (
        SqlTaskOutcome.model_validate(sql["data"]) if sql and sql["data"] else None,
        RetrievalResponse.model_validate(rag["data"]) if rag and rag["data"] else None,
    )


def _binding_matches(request, sql, rag):
    """Check exact scalar provenance; document semantic selection is injected.

    Matching a numeric literal is NOT a formula/period/metric correctness proof.
    Retained only for the independent legacy 3B fixture. New LocalCalculator
    calls require a permit from explicit 3C-3 semantics validation instead.
    """
    cells = {}
    if sql:
        for result in sql.sql_results:
            for index, row in enumerate(result.rows):
                for column, value in row.items():
                    if type(value) in (int, float):
                        cells[sql_cell_ref(result.query_id, index, column)] = Decimal(str(value))
    docs = {chunk.chunk_id: chunk for chunk in rag.chunks} if rag else {}
    if request.formula_ref not in docs:
        return False
    for operand in request.operands.values():
        if operand.source_ref in cells:
            if operand.value != cells[operand.source_ref]:
                return False
        elif operand.source_ref in docs:
            tokens = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", docs[operand.source_ref].text)
            try:
                if not any(Decimal(token) == operand.value for token in tokens):
                    return False
            except InvalidOperation:
                return False
        else:
            return False
    return True


def build_agent_graph(profiles: ProfileRegistry, runtime: ToolRuntime, *, binder: CalculationBinder | None = None, evidence_resolver: EvidenceResolver | None = None, response_composer: ResponseComposer | None = None, checkpointer: InMemorySaver | None = None, execution_mode: Literal["fixture", "offline_sql_integration", "offline_sql_d12_integration", "calculation_fixture"] = "fixture"):
    """Optional formal composition; historical checkpoint factories stay explicit."""
    if execution_mode not in ("fixture", "offline_sql_integration", "offline_sql_d12_integration", "calculation_fixture"):
        raise ValueError("unknown server execution mode")
    if binder is not None and (execution_mode != "fixture" or evidence_resolver is not None):
        raise ValueError("legacy numeric binder is only for the independent 3B fixture")
    if execution_mode == "calculation_fixture" and evidence_resolver is None:
        raise ValueError("3C-3 calculator requires explicit evidence resolver")
    if response_composer is not None and response_composer.execution_mode != execution_mode:
        raise ValueError("composer source strategy must match the server execution mode")
    compose_node = "compose_response" if response_composer is not None else "compose_test_response"
    fast_knowledge = bool(response_composer and getattr(response_composer, 'policy', None)
                          and response_composer.policy.allow_business_calculation)
    # Startup snapshot of every authorized chunk; the composer's copy is only
    # consulted when an answer is assembled, which is after the calculator ran.
    trusted_snapshots = tuple(getattr(getattr(response_composer, "policy", None), "documents", ()) or ())
    source_banner = {
        "fixture": "3B 受控工具替身验证；结果不代表真实数据库、文档检索、模型或计算器输出。",
        "offline_sql_integration": "3C-1：固定离线模型 + 真实 Chinook 样例 SQL；未接 RAG、计算器或联网模型。",
        "offline_sql_d12_integration": "3C-2：固定离线模型 + 真实 Chinook 样例 SQL + A 的真实 D12 Markdown；未接计算器、多格式解析或联网模型。",
        "calculation_fixture": "3C-3 受控测试：SQL、目标和公式文档为人工 fixture；仅计算由真实 LocalCalculator 执行，不是实际销售或达标结论。",
    }[execution_mode]
    if response_composer is not None:
        source_banner = response_composer.source_banner

    def mark(state, name, **updates):
        return {"node_path": [*state.get("node_path", []), name], **updates}

    def initialize(state: AgentState):
        profile = profiles.get(state["profile_id"])
        profile_limitations = list(profile.limitations)
        if profile.sql_backend and profile.sql_backend.model_mode == "live":
            profile_limitations = [
                item for item in profile_limitations
                if "离线模型" not in item and "固定问法" not in item
            ]
        memory = state.get("memory")
        if not memory or memory["profile_id"] != profile.profile_id or memory["config_version"] != profile.config_version:
            memory = SessionMemory(profile_id=profile.profile_id, config_version=profile.config_version).model_dump(mode="json")
        from .task_policy import POLICY_VERSION
        policy_version = POLICY_VERSION if profile.orchestration_policy == "c_v1" else "legacy"
        if memory.get("policy_version", "legacy") != policy_version:
            memory = SessionMemory(profile_id=profile.profile_id, config_version=profile.config_version, policy_version=policy_version).model_dump(mode="json")
        return {
            "memory": memory, "parsed": {}, "status": "", "route": "clarification",
            "tool_plan": [], "sql_outcome": None, "retrieval_outcome": None,
            "calculations": [], "calculation_binding": None, "calculation_evidence": None, "calculation_audits": [], "evidence_refs": [],
            "retrieval_outcomes": [], "calculation_records": [],
            "events": [], "node_path": ["initialize_turn"], "error": None, "response": None,
            "business_sources":None,"business_audit":None,
            "limitations": [source_banner, "当前解析覆盖已登记的业务指标、时间和实体范围；会话仅在本进程内保留。", *profile_limitations],
            "turn_deadline": time.monotonic() + 200,
        }

    def parse(state: AgentState):
        request = AskRequest.model_validate(state["request"])
        memory = SessionMemory.model_validate(state["memory"])
        parsed = parse_question(request.question, profiles.get(state["profile_id"]), memory)
        return mark(state, "parse_question", parsed=parsed.model_dump(mode="json"), memory=memory.model_dump(mode="json"))

    async def semantic_route(state: AgentState):
        parsed = ParsedTurn.model_validate(state["parsed"])
        if parsed.decision != "clarification" or (parsed.task and parsed.task.intent != "unknown"):
            return state
        model = getattr(response_composer, "answer_model", None)
        executor = getattr(response_composer, "executor", None)
        if model is None or executor is None:
            return state
        request = AskRequest.model_validate(state["request"])
        deadline = time.monotonic() + SEMANTIC_ROUTE_SECONDS

        def generate():
            return parse_route_proposal(model.generate_json(
                semantic_route_messages(request.question), {"type": "object"}, deadline
            ))

        try:
            proposal = await executor.run(generate, deadline=deadline)
        except Exception:
            return state
        if proposal is None:
            return state
        allowed = (
            proposal.question_type == "general_knowledge" and not proposal.needs_tool
        ) or (
            proposal.question_type == "business_rule" and proposal.needs_tool
        )
        if not allowed:
            return state
        if proposal.question_type == "business_rule" and not profiles.get(state["profile_id"]).supports("rag"):
            return state
        task = task_from_proposal(request.question, proposal)
        if task is None:
            return state
        parsed = ParsedTurn(
            decision="ready",
            task=task,
            reused_context=parsed.reused_context,
            rules=[*parsed.rules, "semantic_router"],
        )
        return mark(state, "semantic_route", parsed=parsed.model_dump(mode="json"))

    def validate(state: AgentState):
        parsed = ParsedTurn.model_validate(state["parsed"])
        if parsed.decision == "ready" and evidence_resolver is not None and parsed.task.needs_calculation and parsed.task.route == "sql":
            if not profiles.get(state["profile_id"]).supports("rag"):
                parsed.decision, parsed.reason = "unsupported", "受控计算还需本轮公式文档来源，但当前 profile 不具备 RAG。"
            else:
                parsed.task.route = "cross_source"  # E.g. growth needs formula evidence too.
        statuses = {"clarification": "clarification_required", "unsupported": "unsupported", "cancelled": "clarification_required", "ready": "ready"}
        route = parsed.task.route if parsed.decision == "ready" else "unsupported" if parsed.decision == "unsupported" else "clarification"
        if parsed.decision == "ready" and route == "general":
            statuses["ready"] = "answered"
        plan = []
        if parsed.decision == "ready":
            if route in ("sql", "cross_source"):
                plan.append("sql.task")
            if route in ("rag", "cross_source"):
                plan.append("rag.retrieve")
            if parsed.task.needs_calculation:
                plan.append("calculator.calculate")
            if profiles.get(state["profile_id"]).orchestration_policy == "c_v1":
                from .task_plan import make_plan
                parsed.task.subtask_plan = make_plan(parsed.task)
        limitations = state["limitations"]
        if "partial_data_coverage" in parsed.rules:
            profile = profiles.get(state["profile_id"])
            limitations = [*limitations, f"请求周期超出样例数据实际覆盖 {profile.data_start} 至 {profile.data_end}，仅代表已有数据，不能声称完整周期统计。"]
        year_assumed = (parsed.task.slots or {}).get("year_assumed") if parsed.task else None
        if year_assumed:
            limitations = [*limitations, f"原问题未给年份，按经营资料锚定的 {year_assumed} 年口径声明后计算，未静默猜测。"]
        return mark(state, "validate_slots_and_capabilities", parsed=parsed.model_dump(mode="json"), status=statuses[parsed.decision], route=route, tool_plan=plan, limitations=limitations)

    def start_route(state):
        if state["status"] != "ready":
            return compose_node
        task = ParsedTurn.model_validate(state["parsed"]).task
        if state["route"] == "general":
            return compose_node
        if (fast_knowledge and os.getenv('AGENT_CROSS_PARALLEL', '1') == '1' and state['route'] == 'cross_source'
                and task.intent not in WORKFLOWS
                and not task.slots.get('business_workflow')
                and 'ranking' not in task.required_outputs
                and 'monthly_actuals' not in task.required_outputs
                and not (task.subtask_plan and task.subtask_plan[0]['kind'] == 'rag')):
            return 'parallel_sources'
        if task.intent in WORKFLOWS or (task.subtask_plan and task.subtask_plan[0]["kind"] == "rag"):
            return "rag"
        return "sql" if state["route"] in ("sql", "cross_source") else "rag"

    def context(state):
        return ToolContext(request_id=state["request_id"], session_id=state["session_id"], profile_id=state["profile_id"], available_source_refs=tuple(state["evidence_refs"]), deadline=state.get("turn_deadline"))

    async def call_sql(state: AgentState):
        parsed = ParsedTurn.model_validate(state["parsed"])
        task = parsed.task
        profile = profiles.get(state["profile_id"])
        request = AskRequest.model_validate(state["request"])
        if profile.orchestration_policy == "c_v1":
            from .cross_templates import prepare
            documents = [DocumentChunk.model_validate(c) for r in state.get("retrieval_outcomes", []) if r["data"] for c in r["data"]["chunks"]]
            task = prepare(task, documents, getattr(getattr(response_composer, "policy", None), "documents", ()))
            if task is None:
                return mark(state, "sql_preflight", status="insufficient_evidence", limitations=[*state["limitations"], "未取得批准的规则/候选事实；未把未核验文档转换为SQL条件。"])
            parsed.task = task
        definitions = [profile.metric(metric) for metric in task.business_metric_ids]
        business_context = {ref: definition.definition for definition in definitions for ref in definition.source_refs}
        for recorded in state.get("retrieval_outcomes", []):
            if recorded["data"]:
                for chunk in recorded["data"]["chunks"]:
                    business_context[chunk["chunk_id"]] = chunk["text"]
        payload = SqlTaskRequest(
            query_id="task-" + str(uuid.uuid4()), profile_id=profile.profile_id,
            question=task.original_question, normalized_question=task.normalized_question,
            entities=task.entities, time_range=task.time_range,
            resolved_slots=(state['business_sources']['slots'] if state.get('business_sources') else
                {key: value for key, value in task.slots.items() if key in profile.sql_backend.declared_slots}),
            metric_ids=[metric for metric in task.business_metric_ids if metric in profile.sql_backend.registered_metric_ids],
            business_context=[BusinessContextItem(ref_id=ref, text=text) for ref, text in business_context.items()][:20],
            max_rows=request.options.max_rows, show_trace=request.options.show_trace,
            # 只把 C 覆盖检查需要的义务键交给 C；consumed_entities 等 B 侧字段不外传。
            query_obligations={key: value for key, value in task.query_obligations.items() if key in SQL_OBLIGATION_KEYS},
        )
        if task.intent == "growth_rate":
            slots = dict(payload.resolved_slots)
            slots.pop("comparison_period", None)
            metric_label = profile.metric(task.business_metric_ids[0]).name
            query = f"{task.time_range.label} {task.slots.get('data_scope', '')}{metric_label}是多少"
            payload = payload.model_copy(update={"question": query, "normalized_question": query, "resolved_slots": slots})
        outcome = await runtime.run("sql.task", payload, context(state))
        refs = list(outcome.source_refs)
        if outcome.succeeded:
            for result in outcome.data.sql_results:
                refs.extend(sql_cell_ref(result.query_id, index, column) for index, row in enumerate(result.rows) for column, value in row.items() if type(value) in (int, float))
        update = tool_update(state, "sql", outcome, "sql_outcome", refs)
        update["parsed"] = parsed.model_dump(mode="json")
        if outcome.succeeded and task.intent == "growth_rate" and task.slots.get("comparison_period"):
            from .parsing import _time_range
            prior = task.slots["comparison_period"]
            period = _time_range(prior)
            if period is None:
                return {
                    **update,
                    "status": "insufficient_evidence",
                    "limitations": [*state["limitations"], "增长率比较周期未形成有效时间范围，未执行基期查询。"],
                }
            slots = dict(payload.resolved_slots)
            slots.update(start_date=period.start, end_date=period.end)
            slots.pop("comparison_period", None)
            prior_question = f"{period.label} {task.slots.get('data_scope', '')}{metric_label}是多少"
            second = payload.model_copy(update={"query_id": "task-" + str(uuid.uuid4()), "question": prior_question, "normalized_question": prior_question, "resolved_slots": slots, "time_range": period})
            current = {**state, **update}
            previous = await runtime.run("sql.task", second, context(current))
            prior_refs = [*previous.source_refs, *[sql_cell_ref(r.query_id, i, c) for r in previous.data.sql_results for i, row in enumerate(r.rows) for c, v in row.items() if type(v) in (int, float)]] if previous.succeeded else []
            update.update(tool_update(current, "sql_previous", previous, "sql_outcome", prior_refs))
            if previous.succeeded:
                combined = outcome.data.model_copy(update={"sql_results": [*outcome.data.sql_results, *previous.data.sql_results], "source_refs": [*outcome.data.source_refs, *previous.data.source_refs], "limitations": [*outcome.data.limitations, *previous.data.limitations]})
                # 合并两期结果时保留安全字段：compose 的兜底判断依赖执行路径与义务覆盖结论。
                prior_snapshot = update.get("sql_outcome") or {}
                update["sql_outcome"] = {"status": "success", "data": combined.model_dump(mode="json"), "error": None,
                    "execution_path": prior_snapshot.get("execution_path", "none"),
                    "query_obligations_covered": prior_snapshot.get("query_obligations_covered")}
        return update

    async def call_rag(state: AgentState):
        task = ParsedTurn.model_validate(state["parsed"]).task
        request = AskRequest.model_validate(state["request"])
        if (fast_knowledge and profiles.get(state['profile_id']).orchestration_policy == 'c_v1'
                and os.getenv('AGENT_FAST_RAG', '1').casefold() not in {'0', 'false', 'off', 'no'}
                and task.intent not in WORKFLOWS
                and not {'customer_tiers', 'transaction_exists'} & set(task.required_outputs)):
            return await bounded_rag(state, task, request)
        if profiles.get(state["profile_id"]).orchestration_policy == "c_v1":
            from .task_plan import retrieval_specs, retrieval_task_from_sql, retry_query
            from ..knowledge.facts import FACTS, fact_matches, coverage
            sql, _ = _models(state)
            task = retrieval_task_from_sql(task, sql, profiles.get(state["profile_id"]))
            from ..knowledge.source_plan import rag_source_plan
            source_plan = rag_source_plan(task.retrieval_question or task.original_question)
            current, recorded, repairs, empty_repaired = dict(state), [], 0, False
            for doc, query, count, role in retrieval_specs(task, request.options.top_k):
                filters = {"slots": task.slots, "business_metric_ids": task.business_metric_ids}
                if source_plan.required_roles:
                    filters["required_roles"] = source_plan.required_roles
                if doc:
                    filters["doc_id"] = doc
                if role:
                    filters["fact_role"] = role
                payload = RetrievalRequest(retrieval_id="retrieval-" + str(uuid.uuid4()), profile_id=state["profile_id"], query=query, filters=filters, top_k=count)
                outcome = await runtime.run("rag.retrieve", payload, context(current))
                current.update(tool_update(current, "rag", outcome, "retrieval_outcome", list(outcome.source_refs)))
                incomplete = outcome.succeeded and (not outcome.data.chunks or role and not any(fact_matches(role, c.text) for c in outcome.data.chunks))
                if incomplete and doc and repairs < 2 and (role or not empty_repaired):
                    repairs += 1
                    empty_repaired = empty_repaired or not outcome.data.chunks
                    payload = payload.model_copy(update={"retrieval_id": "retrieval-" + str(uuid.uuid4()), "query": FACTS[role][1] if role else retry_query(task, doc)})
                    outcome = await runtime.run("rag.retrieve", payload, context(current))
                    current.update(tool_update(current, "rag", outcome, "retrieval_outcome", list(outcome.source_refs)))
                    current["limitations"] = [*current["limitations"], f"{doc}/{role or 'document'}事实缺证，定向补检索；未改变用户条件（整轮最多2次）。"]
                recorded.append(_snapshot(outcome))
                if not outcome.succeeded:
                    break
            primary = next((r for r in recorded if r["data"] and r["data"]["chunks"]), recorded[0] if recorded else None)
            all_chunks, seen_chunk_ids = [], set()
            for recorded_item in recorded:
                if not recorded_item["data"]:
                    continue
                for chunk in recorded_item["data"]["chunks"]:
                    chunk_id = chunk.get("chunk_id")
                    if chunk_id in seen_chunk_ids:
                        continue
                    seen_chunk_ids.add(chunk_id)
                    all_chunks.append(chunk)
            if primary and primary.get("data") and all_chunks:
                merged = dict(primary)
                merged_data = dict(primary["data"])
                primary_retrieval_id = merged_data.get("retrieval_id")
                merged_data["chunks"] = [{**chunk, "retrieval_id": primary_retrieval_id} for chunk in all_chunks]
                merged["data"] = merged_data
                primary = merged
            task.evidence_coverage = coverage(task, [DocumentChunk.model_validate(c) for c in all_chunks])
            if all_chunks:
                current["evidence_refs"] = list(dict.fromkeys([*current.get("evidence_refs", []), *(c.get("chunk_id") for c in all_chunks)]))
            parsed = ParsedTurn.model_validate(state["parsed"])
            parsed.task.evidence_coverage = task.evidence_coverage
            return {**current, "retrieval_outcomes": recorded, "retrieval_outcome": primary, "parsed": parsed.model_dump(mode="json")}
        retrieval_query = task.original_question if task.route == "cross_source" else task.normalized_question
        profile = profiles.get(state["profile_id"])
        filters = {"slots": task.slots, "business_metric_ids": task.business_metric_ids}
        if task.entities and task.intent in {"document_rule", "multidoc_query"} and any(item.get("type") == "genre" for item in task.entities):
            filters.update({"preferred_doc": "D03", "answer_type": "alias_mapping"})
        question = task.original_question
        if task.intent == "multidoc_query" or ("复盘" in question and any(term in question for term in ("比较", "变化", "对比"))):
            required = []
            if "二季度" in question or "Q2" in question:
                required.append("D07")
            if "三季度" in question or "Q3" in question:
                required.append("D08")
            if required:
                filters.update({"required_sources": list(dict.fromkeys(required)), "answer_type": "review_comparison"})
        if task.intent in {"target_difference", "target_attainment", "document_calculation"}:
            filters.update({"required_sources": ["D06"], "preferred_doc": "D06", "answer_type": "target_value", "query_terms": ["销售额达成率", "销售额差额", "公式"]})
        if task.intent == "document_rule" and any(term in question for term in ("分类", "归类", "Genre", "重金属", "摇滚", "拉丁", "媒体")):
            filters.update({"preferred_doc": "D03", "answer_type": "alias_mapping"})
        if task.route == "rag" and (task.slots.get("month") or any(term in question for term in ("目标表", "月度", "计划"))):
            filters.update({"preferred_doc": "D06", "answer_type": "table_cell"})
        if "活动" in question or "海报" in question:
            filters.update({"required_sources": ["D10", "D11"], "answer_type": "activity_entity"})
        if task.needs_calculation:
            retrieval_query += " " + {"target_difference": "目标 销售额差额 销售额达成率", "target_attainment": "目标 销售额达成率 销售额差额",
                "growth_rate": "销售额 增长率", "average_order_value": "客单价", "document_sum": "月度分解"}.get(task.intent, "")
        plan = _rag_plan(question, task.intent)
        from ..knowledge.source_plan import rag_source_plan
        source_plan = rag_source_plan(question)
        if plan["required_sources"]:
            filters["required_sources"] = list(dict.fromkeys([*filters.get("required_sources", []), *plan["required_sources"]]))
        if plan["evidence_requirements"]:
            filters["evidence_requirements"] = plan["evidence_requirements"]
        if plan["query_terms"]:
            filters["query_terms"] = list(dict.fromkeys([*filters.get("query_terms", []), *plan["query_terms"]]))
        if source_plan.required_roles:
            filters["required_roles"] = source_plan.required_roles
        if source_plan.preferred_doc and not filters.get("preferred_doc") and len(source_plan.required_docs) == 1:
            filters["preferred_doc"] = source_plan.preferred_doc
        if task.intent == "poster_candidates":
            filters["required_sources"] = ["D05", "D11"]
        if os.getenv("RAG_ENABLE_DENSE", "0") == "1":
            filters["dense_retrieval"] = True
        payload = RetrievalRequest(retrieval_id="retrieval-" + str(uuid.uuid4()), profile_id=state["profile_id"], query=retrieval_query, filters=filters, top_k=request.options.top_k)
        outcome = await runtime.run("rag.retrieve", payload, context(state))
        return tool_update(state, "rag", outcome, "retrieval_outcome", list(outcome.source_refs))

    async def bounded_rag(state, task, request):
        """Restore one-shot retrieval; repair only missing core content once."""
        from ..knowledge.source_plan import rag_source_plan
        from ..response.fast_renderer import core_content_missing
        from ..knowledge.facts import coverage
        sql, _ = _models(state)
        from .task_plan import retrieval_task_from_sql
        task = retrieval_task_from_sql(task, sql, profiles.get(state['profile_id']))
        question = task.retrieval_question or task.original_question
        source_plan = rag_source_plan(question)
        primary = list(source_plan.primary_docs)
        filters = {'slots': task.slots, 'business_metric_ids': task.business_metric_ids,
                   'query_terms': source_plan.query_terms}
        if primary:
            filters['required_sources'] = primary
            filters['preferred_doc'] = primary[0]
        only_sources = bool(source_plan.required_docs) and any(w in question for w in ('仅依据', '只依据', '仅使用', '只使用'))
        if only_sources and len(source_plan.required_docs) == 1:
            filters['doc_id'] = source_plan.required_docs[0]
        function = {'target_attainment':'attainment_rate', 'target_difference':'difference',
                    'growth_rate':'growth_rate', 'average_order_value':'average_order_value', 'document_sum':'sum'}.get(task.intent)
        if task.needs_calculation and function:
            primary = ['D06'] if function in {'attainment_rate', 'difference', 'sum'} else ['D01', 'D02']
            terms = {'attainment_rate':'目标明细 Genre 达成率', 'difference':'目标明细 Genre 销售额差额',
                     'growth_rate':'增长率 period_over_period_growth 本期 上期',
                     'average_order_value':'客单价 销售额 订单数', 'sum':'月度分解 7月金额 8月金额 9月金额'}[function]
            filters.update(required_sources=primary, preferred_doc=primary[0],
                           query_terms=list(dict.fromkeys([terms, *source_plan.query_terms])))
            if not only_sources and function != 'sum':
                support = [doc for doc in source_plan.required_docs if doc in {'D02', 'D07', 'D08', 'D09', 'D10', 'D11'}]
                if function in {'attainment_rate', 'difference'}:
                    review = {2:'D07', 3:'D08'}.get(task.slots.get('quarter'))
                    if review:
                        support.append(review)
                if function == 'growth_rate' and any(word in question for word in ('第二季度', '二季度', 'Q2', '第三季度', '三季度', 'Q3')):
                    support.extend(('D07', 'D08'))
                filters['required_sources'] = list(dict.fromkeys([*primary, *support]))
        payload = RetrievalRequest(retrieval_id='retrieval-'+str(uuid.uuid4()), profile_id=state['profile_id'],
                                   query=question, filters=filters, top_k=request.options.top_k)
        outcome = await runtime.run('rag.retrieve', payload, context(state))
        current = {**state, **tool_update(state, 'rag', outcome, 'retrieval_outcome', list(outcome.source_refs))}
        recorded = [_snapshot(outcome)]
        chunks = list(outcome.data.chunks) if outcome.succeeded else []
        if only_sources:
            chunks = [c for c in chunks if c.doc_id in source_plan.required_docs]
        missing_docs = [d for d in primary if not any(c.doc_id == d for c in chunks)]
        missing_secondary = [d for d in source_plan.secondary_docs if not any(c.doc_id == d for c in chunks)] if not only_sources else []
        from ..knowledge.facts import coverage as fact_coverage, role_repair_target
        missing_fact_roles = [item['role'] for item in fact_coverage(task, chunks) if not item['complete']]
        missing_content = core_content_missing(task.original_question, chunks) if task.route == 'rag' and not task.needs_calculation else []
        missing_formula = False
        if function and task.needs_calculation:
            from ..evidence.cross_source_bundle import _formula_chunk
            from ..evidence.formulas import FormulaRegistry
            required_functions = ['attainment_rate', 'difference'] if function in {'attainment_rate', 'difference'} else [function]
            registry = FormulaRegistry.from_profile('chinook')
            missing_formula = any(_formula_chunk(chunks, item, registry) is None for item in required_functions)
        plans = []
        if outcome.succeeded and (missing_docs or missing_content or missing_formula or missing_secondary or missing_fact_roles):
            repair_filters = dict(filters)
            covered_roles = set()
            repair_query = ' '.join(filters['query_terms']) + ' ' + question
            if missing_docs:
                repair_filters['required_sources'] = missing_docs
                repair_filters['preferred_doc'] = missing_docs[0]
            elif missing_formula and function in {'difference', 'attainment_rate'}:
                repair_filters.update(doc_id='D06', fact_role='target_formula', required_sources=['D06'])
                covered_roles.add('target_formula')
            elif missing_fact_roles:
                repair = role_repair_target(missing_fact_roles[0], chunks)
                if repair:
                    hint, patch = repair
                    # Multi-document roles drop the single-doc pin so every
                    # required source stays reachable.
                    if 'doc_id' not in patch:
                        repair_filters.pop('doc_id', None)
                    repair_filters.update(patch)
                    covered_roles.add(missing_fact_roles[0])
                    if hint:
                        # Lead with the owed evidence terms: the question alone
                        # keeps re-ranking the same summary chunk.
                        repair_query = hint + ' ' + repair_query
            elif missing_secondary and not missing_content and not missing_formula:
                repair_filters.update(required_sources=missing_secondary, preferred_doc=missing_secondary[0])
            plans.append((repair_query, repair_filters))
            # A single repair slot can be consumed by the formula fetch, so give an
            # uncovered fact obligation one more targeted pass (whole turn capped at 2).
            for role in missing_fact_roles:
                if len(plans) >= 2 or role in covered_roles:
                    continue
                repair = role_repair_target(role, chunks)
                if not repair:
                    continue
                hint, patch = repair
                plan_filters = dict(filters, **patch)
                if 'doc_id' not in patch:
                    plan_filters.pop('doc_id', None)
                plans.append((hint or question, plan_filters))
        for repair_query, repair_filters in plans:
            repair = await runtime.run('rag.retrieve', payload.model_copy(update={
                'retrieval_id':'retrieval-'+str(uuid.uuid4()), 'query':repair_query, 'filters':repair_filters}), context(current))
            recorded.append(_snapshot(repair))
            current['events'] = [*current['events'], *(e.model_dump(mode='json') for e in repair.events)]
            if repair.succeeded:
                chunks.extend(c for c in repair.data.chunks if not only_sources or c.doc_id in source_plan.required_docs)
                current['evidence_refs'] = list(dict.fromkeys([*current['evidence_refs'], *repair.source_refs]))
            else:
                current['limitations'] = [*current['limitations'], '补充文档检索未完成；已保留首次检索的有效来源。']
        unique = {}
        for chunk in chunks:
            unique.setdefault(chunk.chunk_id, chunk)
        if outcome.succeeded:
            merged = outcome.data.model_copy(update={'chunks': [c.model_copy(update={'retrieval_id':outcome.data.retrieval_id}) for c in unique.values()]})
            current['retrieval_outcome'] = {'status':'success', 'data':merged.model_dump(mode='json'), 'error':None}
        parsed = ParsedTurn.model_validate(state['parsed'])
        parsed.task.evidence_coverage = coverage(task, list(unique.values()))
        parsed.task.entities = task.entities
        if only_sources:
            for item in recorded:
                if item.get('data'):
                    item['data']['chunks'] = [c for c in item['data']['chunks'] if c['doc_id'] in source_plan.required_docs]
        return {**current, 'parsed':parsed.model_dump(mode='json'), 'retrieval_outcomes':recorded}

    async def parallel_sources(state):
        """Independent sources share a deadline; merge events only at the join."""
        sql_update, rag_update = await asyncio.gather(call_sql(dict(state)), call_rag(dict(state)))
        merged = {**state, **sql_update, **rag_update}
        merged['sql_outcome'] = sql_update.get('sql_outcome')
        event_count = len(state.get('events', []))
        merged['events'] = [*state.get('events', []), *sql_update.get('events', [])[event_count:],
                            *rag_update.get('events', [])[event_count:]]
        merged['evidence_refs'] = list(dict.fromkeys([*sql_update.get('evidence_refs', []), *rag_update.get('evidence_refs', [])]))
        merged['limitations'] = list(dict.fromkeys([*sql_update.get('limitations', []), *rag_update.get('limitations', [])]))
        merged['node_path'] = [*state.get('node_path', []), 'parallel_sql_rag']
        for update in (sql_update, rag_update):
            if update.get('status') not in {'ready', None}:
                merged['status'], merged['error'], merged['route'] = update['status'], update.get('error'), update.get('route', state['route'])
                break
        return merged

    def after_parallel(state):
        if state['status'] != 'ready':
            return 'evidence_check'
        return 'calculate' if 'calculator.calculate' in state['tool_plan'] else 'evidence_check'

    def tool_update(state, node, outcome, channel, refs):
        status, route, error = state["status"], state["route"], None
        if not outcome.succeeded:
            if outcome.status == "unsupported":
                status, route = "unsupported", "unsupported"
            else:
                status, error = "error", outcome.error.model_dump(mode="json")
        snapshot = _sql_snapshot(outcome) if channel == "sql_outcome" else _snapshot(outcome)
        return mark(state, node, **{
            channel: snapshot, "status": status, "route": route, "error": error,
            "events": [*state["events"], *(event.model_dump(mode="json") for event in outcome.events)],
            "evidence_refs": list(dict.fromkeys([*state["evidence_refs"], *refs])) if outcome.succeeded else state["evidence_refs"],
        })

    def after_sql(state):
        if state["status"] != "ready":
            return "evidence_check"
        if state.get('business_sources'):
            return 'evidence_check'
        if not any(result["rows"] for result in state["sql_outcome"]["data"]["sql_results"]):
            return "evidence_check"
        return "rag" if state["route"] == "cross_source" and not state.get("retrieval_outcome") else "calculate" if "calculator.calculate" in state["tool_plan"] else "evidence_check"

    def after_rag(state):
        rag = state["retrieval_outcome"]
        if state["status"] != "ready" or not rag["data"]["chunks"]:
            return "evidence_check"
        if ParsedTurn.model_validate(state["parsed"]).task.intent in WORKFLOWS:
            return "prepare_business"
        if state["route"] == "cross_source" and not state.get("sql_outcome"):
            return "sql"
        return "calculate" if "calculator.calculate" in state["tool_plan"] else "evidence_check"

    def prepare_business(state):
        parsed=ParsedTurn.model_validate(state['parsed'])
        _,rag=_models(state)
        try:
            sources=resolve_sources(parsed.task,rag,profiles.get(state['profile_id']))
        except WorkflowGap as exc:
            parsed.reason=str(exc)
            return mark(state,'prepare_business',status='insufficient_evidence',parsed=parsed.model_dump(mode='json'))
        return mark(state,'prepare_business',business_sources=sources)

    def after_business_plan(state):
        if state['status']!='ready': return 'evidence_check'
        sources=state['business_sources']
        if sources['intent']=='poster_candidates' and not sources['selected_candidates']:
            return 'evidence_check'
        return 'sql'

    def _tampered_this_turn(state, rag):
        """True when any chunk offered this turn drifted from the trusted snapshot.

        Covers both the merged retrieval response and the raw per-call outcomes,
        because ``calculate_many`` re-selects evidence from the latter.
        """
        if not trusted_snapshots:
            return False
        chunks = list(rag.chunks) if rag is not None else []
        for recorded in state.get("retrieval_outcomes", []):
            if recorded["data"]:
                chunks.extend(DocumentChunk.model_validate(item) for item in recorded["data"]["chunks"])
        return bool(tampered_chunks(chunks, trusted_snapshots))

    async def calculate(state: AgentState):
        task = ParsedTurn.model_validate(state["parsed"]).task
        sql, rag = _models(state)
        if _tampered_this_turn(state, rag):
            return mark(state, "calculate", status="insufficient_evidence",
                limitations=[*state["limitations"], "本轮文档片段与服务器可信快照不一致，未调用计算工具，也未生成达标或增长结论。"])
        if profiles.get(state["profile_id"]).orchestration_policy == "c_v1" and evidence_resolver is not None:
            return await calculate_many(state, task, sql, rag)
        calculation_id = "calc-" + str(uuid.uuid4())
        invocation_context = context(state)
        audit = None
        try:
            if evidence_resolver is not None:
                bundle = evidence_resolver(task.model_copy(deep=True), sql.model_copy(deep=True) if sql else None, rag.model_copy(deep=True) if rag else None, calculation_id, invocation_context)
                authorized = authorize_calculation(bundle, task=task, sql=sql, rag=rag, profile=profiles.get(state["profile_id"]), context=invocation_context, calculation_id=calculation_id,
                    expected_mode="fixture" if execution_mode == "calculation_fixture" else "verified_business") if bundle is not None else None
                binding = authorized.request if authorized else None
                if authorized:
                    invocation_context = invocation_context.model_copy(update={"calculation_permit": authorized.permit})
                    audit = authorized.audit
            else:
                binding = binder(task.model_copy(deep=True), sql.model_copy(deep=True) if sql else None, rag.model_copy(deep=True) if rag else None, calculation_id) if binder else None
            if binding is not None and evidence_resolver is None:
                binding = CalculationRequest.model_validate(binding.model_dump(mode="python"))
                expected = {"target_attainment": "attainment_rate", "target_difference": "difference", "growth_rate": "growth_rate"}.get(task.intent)
                if binding.calculation_id != calculation_id or binding.profile_id != state["profile_id"] or expected and binding.function != expected or not _binding_matches(binding, sql, rag):
                    binding = None
        except Exception:
            binding = None  # No exception text or guessed operand reaches an answer.
        if binding is None:
            return mark(state, "calculate", status="insufficient_evidence", limitations=[*state["limitations"], "缺少经过确认的公式/数值绑定，未调用计算工具，也未生成达标或增长结论。"])
        outcome = await runtime.run("calculator.calculate", binding, invocation_context)
        update = tool_update(state, "calculate", outcome, "calculation_binding", [])
        # Store only the explicit invocation binding; ToolOutcome is not the binding.
        update["calculation_binding"] = binding.model_dump(mode="json")
        update["calculation_evidence"] = audit
        update["calculations"] = [outcome.data.model_dump(mode="json")] if outcome.succeeded else []
        if outcome.succeeded and audit and audit.get("missing_roles"):
            missing = "、".join(dict.fromkeys(audit["missing_roles"]))
            update["limitations"] = [*state["limitations"], f"跨源解释证据缺少角色：{missing}；已保留实际/目标计算结果。"]
        return update

    async def calculate_many(state, task, sql, rag):
        current, records, calculations = dict(state), [], []
        binding_failures = []
        missing_context_roles = []
        named = {'growth_rate': 'growth_rate', 'average_order_value': 'average_order_value',
                 'document_sum': 'sum', 'period_difference': 'difference'}
        functions = [named.get(task.intent, 'attainment_rate')]
        if task.intent == 'growth_rate' and 'difference' in task.required_outputs:
            functions.append('difference')
        if task.intent not in named and ("difference" in task.required_outputs or task.intent == "target_difference"):
            functions.append("difference")
        months = [(task.slots["quarter"] - 1) * 3 + i for i in (1, 2, 3)] if "monthly_actuals" in task.required_outputs else [None]
        for metric in task.business_metric_ids:
            for month, function in [(m, f) for m in months for f in functions]:
                child = task.model_copy(deep=True)
                child.business_metric_ids = [metric]
                child.intent = ('period_difference' if task.intent == 'growth_rate' and function == 'difference'
                                else task.intent if task.intent in named else {"attainment_rate": "target_attainment", "difference": "target_difference"}[function])
                if month:
                    from datetime import date
                    from ..contracts import TimeRange
                    child.slots.pop("quarter", None)
                    child.slots["month"] = month
                    y = child.slots["year"]
                    child.time_range = TimeRange(start=date(y, month, 1).isoformat(), end=date(y + (month == 12), 1 if month == 12 else month + 1, 1).isoformat(), end_inclusive=False, label=f"{y}年{month}月")
                # Use the merged, this-turn retrieval response so formula,
                # target and review evidence can be checked together.  The
                # old D01/D06 selector discarded D08 and made valid bindings
                # depend on which single retrieval happened to be primary.
                selected_rag = rag
                if selected_rag is None or not selected_rag.chunks:
                    selected_rag = next((RetrievalResponse.model_validate(r["data"]) for r in state.get("retrieval_outcomes", []) if r["data"] and r["data"].get("chunks")), None)
                calculation_id = "calc-" + str(uuid.uuid4())
                invocation_context = context(current)
                try:
                    bundle = evidence_resolver(child, sql.model_copy(deep=True) if sql else None, selected_rag.model_copy(deep=True) if selected_rag else None, calculation_id, invocation_context)
                    if bundle is None:
                        raise ValueError("missing binding")
                    authorized = authorize_calculation(bundle, task=child, sql=sql, rag=selected_rag, profile=profiles.get(state["profile_id"]), context=invocation_context, calculation_id=calculation_id, expected_mode="verified_business")
                except Exception:
                    binding_failures.append(f"{metric}/{function}")
                    continue
                prior_status, prior_error = current.get("status"), current.get("error")
                outcome = await runtime.run("calculator.calculate", authorized.request, invocation_context.model_copy(update={"calculation_permit": authorized.permit}))
                current.update(tool_update(current, "calculate", outcome, "calculation_binding", []))
                if not outcome.succeeded:
                    # A failed child calculation must not erase successful
                    # siblings.  Keep the tool event for traceability while
                    # restoring the parent readiness for the remaining loop.
                    current["status"], current["error"] = prior_status, prior_error
                    binding_failures.append(f"{metric}/{function}")
                    continue
                calculations.append(outcome.data.model_dump(mode="json"))
                records.append({"task": child.model_dump(mode="json"), "binding": authorized.request.model_dump(mode="json"), "audit": authorized.audit, "retrieval_id": selected_rag.retrieval_id})
                missing_context_roles.extend(authorized.audit.get("missing_roles", []))
        if binding_failures:
            current["limitations"] = [*current["limitations"], "部分计算绑定失败，已保留其余成功结果：" + "、".join(dict.fromkeys(binding_failures)) + "。"]
        if missing_context_roles:
            current["limitations"] = [*current["limitations"], "跨源解释证据缺少角色：" + "、".join(dict.fromkeys(missing_context_roles)) + "；已保留实际/目标计算结果。"]
        return {**current, "calculations": calculations, "calculation_records": records,
            "calculation_binding": records[0]["binding"] if records else None, "calculation_evidence": records[0]["audit"] if records else None}

    def evidence_check(state: AgentState):
        status = state["status"]
        if status == "ready":
            sql, rag = _models(state)
            parsed=ParsedTurn.model_validate(state['parsed'])
            if parsed.task.intent in WORKFLOWS:
                try:
                    sources=resolve_sources(parsed.task,rag,profiles.get(state['profile_id']))
                    if sources!=state.get('business_sources'): raise WorkflowGap('本轮文档依赖未完整执行。')
                    audit=conclude(sources,sql)
                except WorkflowGap as exc:
                    parsed.reason=str(exc)
                    return mark(state,'evidence_check',status='insufficient_evidence',parsed=parsed.model_dump(mode='json'))
                return mark(state,'evidence_check',status='answered',business_audit=audit)
            gaps = []
            if state["route"] in ("sql", "cross_source") and (not sql or not any(result.rows for result in sql.sql_results)):
                status = "insufficient_evidence"
                gaps.append("数据库查询没有返回可用于本轮结论的结果行")
            if state["route"] in ("rag", "cross_source") and (not rag or not rag.chunks):
                status = "insufficient_evidence"
                gaps.append("文档检索没有返回可定位的片段")
            if state.get("retrieval_outcomes"):
                from .task_plan import document_needs
                from ..knowledge.source_plan import rag_source_plan
                task = ParsedTurn.model_validate(state["parsed"]).task
                soft = fast_knowledge and os.getenv('AGENT_FAST_RAG', '1').casefold() not in {'0', 'false', 'off', 'no'}
                source_plan = rag_source_plan(task.retrieval_question or task.original_question)
                required_docs = set(source_plan.primary_docs) if soft else {d for d in document_needs(task) if d}
                if soft and task.needs_calculation:
                    # Operand/formula coverage is proved by the calculator
                    # authorization, rather than optional review doc ids.
                    required_docs = set()
                present = {c["doc_id"] for outcome in state["retrieval_outcomes"] if outcome["data"] for c in outcome["data"]["chunks"]}
                if not required_docs <= present:
                    status = "insufficient_evidence"
                    gaps.append("缺少必要文档：" + "、".join(sorted(required_docs - present)))
                if not soft and any(not item["complete"] for item in task.evidence_coverage):
                    status = "insufficient_evidence"
                    gaps.append("本轮证据只覆盖了部分问题条件")
            if "calculator.calculate" in state["tool_plan"] and not state["calculations"]:
                status = "insufficient_evidence"
                gaps.append("计算所需的有效绑定或结果不存在")
            task = ParsedTurn.model_validate(state["parsed"]).task
            from .cross_templates import completed
            chunks = [DocumentChunk.model_validate(c) for r in state.get("retrieval_outcomes", []) if r["data"] for c in r["data"]["chunks"]]
            blocked = (set(task.required_outputs) & {"document_sum"}) if not state['calculations'] else set()
            if {"customer_tiers", "monthly_actuals", "transaction_exists"} & set(task.required_outputs) and not completed(task, sql, chunks):
                blocked.update(set(task.required_outputs) & {"customer_tiers", "monthly_actuals", "transaction_exists"})
            if blocked:
                # These need B's approved grain/document arithmetic seam. A
                # scalar quarter value or raw passage is not a completed answer.
                return mark(state, "evidence_check", status="insufficient_evidence", limitations=[*state["limitations"], "尚未解锁必要的客户分层/月度粒度/文档合计算子：" + ", ".join(sorted(blocked)) + "；不把片段命中当作完整结论。"])
            if status == "ready":
                status = "answered"
            if gaps:
                return mark(state, "evidence_check", status=status, limitations=[*state["limitations"], "证据检查缺口：" + "；".join(dict.fromkeys(gaps)) + "。"])
        return mark(state, "evidence_check", status=status)

    async def compose(state: AgentState):
        parsed = ParsedTurn.model_validate(state["parsed"])
        task = parsed.task
        memory = SessionMemory.model_validate(state["memory"])
        sql, rag = _models(state)
        status = state["status"]
        sql_outcome = state.get("sql_outcome") or {}
        if (status == "answered" and sql_outcome.get("execution_path") == "controlled_fallback"
                and sql_outcome.get("query_obligations_covered") is not True):
            # C 已经会拒绝未覆盖义务的降级；这里是最后一道防线，绝不允许它变成 answered。
            status = "unsupported"
            state = {**state, "status": status, "route": "unsupported",
                "limitations": [*state["limitations"], "模型失败后的受控查询未保留本轮的过滤、粒度或输出要求；未用更粗口径代替回答。"]}
            parsed.reason = parsed.reason or "模型失败后的受控查询未保留本轮记录的过滤、粒度或输出要求，未给出结论。"
        clarification = parsed.clarification
        answer = None
        if parsed.decision == "cancelled":
            memory = SessionMemory(profile_id=memory.profile_id, config_version=memory.config_version, policy_version=memory.policy_version)
            from .parsing import clarify
            clarification = clarify(None, ["intent"]).clarification
            answer = parsed.reason
        elif status == "clarification_required":
            memory.pending, memory.last_task, memory.last_answer_summary = logical_copy(task), None, None
            memory.pending_slots = [slot.name for slot in clarification.missing_slots] if clarification else []
            memory.pending_clarification = {"task_id": task.task_id, "task_version": task.task_version,
                "slots": memory.pending_slots, "blocking_slots": parsed.blocking_slots,
                "options": clarification.options if clarification else []} if task else {}
        else:
            memory.pending = None
            memory.pending_slots = []
            memory.pending_clarification = {}
            memory.last_task = logical_copy(task) if status == "answered" else None
            memory.last_answer_summary = None
        if status == "answered" and response_composer is None:
            if execution_mode == "fixture":
                answer = "3B 受控替身：本轮所需工具与证据检查已完成，结果见结构化证据。尚未生成真实业务结论。"
            elif execution_mode == "offline_sql_integration":
                answer = "3C-1 离线 SQL 集成检查完成；SQL 和查询结果来自真实 Chinook 样例库，详见 sql_results。尚未接入正式业务答案组装。"
            elif execution_mode == "offline_sql_d12_integration":
                answer = "3C-2 来源接入检查完成；本轮 SQL 见 sql_results，命中的真实 D12 原文见 documents。文档命中不等于完整语义答案或目标绑定，尚未接入正式业务答案组装。"
            else:
                answer = "3C-3 受控计算链检查完成；SQL 与文档是人工 fixture，calculations 由 LocalCalculator 实时计算，不能当作真实销售/达标结论。尚未接入正式业务答案组装。"
            memory.last_answer_summary = answer
        elif status == "unsupported":
            answer = parsed.reason or "本轮工具明确不支持该任务，未生成业务结论。"
        elif status == "insufficient_evidence":
            answer = "本轮证据或计算绑定不完整，不能形成业务结论。"
        events = [ToolEvent.model_validate_json(json.dumps(event)) for event in state["events"]]
        memory.active_task_id = task.task_id if task else None
        options = AskRequest.model_validate(state["request"]).options
        limitations = [*state["limitations"], *(sql.limitations if sql else [])]
        if response_composer is not None:
            compose_context = ComposeContext(
                request_id=state["request_id"], session_id=state["session_id"], profile=profiles.get(state["profile_id"]),
                status=status, route=state["route"], task=task, options=options, sql=sql, rag=rag,
                calculations=tuple(Calculation.model_validate(item) for item in state["calculations"]),
                binding=CalculationRequest.model_validate(state["calculation_binding"]) if state["calculation_binding"] else None,
                binding_audit=state["calculation_evidence"], available_source_refs=tuple(state["evidence_refs"]),
                calculation_audits=tuple(state.get('calculation_audits',[])),
                business_audit=state.get('business_audit'),
                events=tuple(events), limitations=tuple(limitations), clarification=clarification,
                error=ApiError.model_validate(state["error"]) if status == "error" else None,
                parser_reason=parsed.reason, reset_context=parsed.decision == "cancelled",
                retrievals=tuple(RetrievalResponse.model_validate(item["data"]) for item in state.get("retrieval_outcomes", []) if item["data"]),
                calculation_records=tuple(state.get("calculation_records", [])),
            )
            if hasattr(response_composer, "prepare"):
                compose_context = await response_composer.prepare(compose_context)
            response = response_composer.compose(compose_context)
            if response.status != "clarification_required":
                memory.pending = None
                memory.pending_slots = []
                memory.last_task = logical_copy(task) if response.status == "answered" or (memory.policy_version != "legacy" and response.status in {"error", "insufficient_evidence"}) else None
            memory.last_answer_summary = response.answer if response.status == "answered" else None
            return mark(state, compose_node, status=response.status, response=response.model_dump(mode="json"), memory=memory.model_dump(mode="json"))
        response = AskResponse(
            request_id=state["request_id"], session_id=state["session_id"], profile_id=state["profile_id"],
            status=status, answer=answer, route=state["route"], intent=Intent(name=task.intent if task else "reset_context" if parsed.decision == "cancelled" else "unknown"),
            entities=task.entities if task else [], time_range=task.time_range if task else None,
            sql_results=sql.sql_results if sql else [], documents=rag.chunks if rag else [],
            calculations=[Calculation.model_validate(item) for item in state["calculations"]],
            metric_definitions=sql.metric_definitions if sql else [], limitations=list(dict.fromkeys(limitations)),
            clarification=clarification, error=ApiError.model_validate(state["error"]) if status == "error" else None,
            trace=events_to_trace(events, request_id=state["request_id"], session_id=state["session_id"], profile_id=state["profile_id"], show_trace=options.show_trace),
        )
        return mark(state, "compose_test_response", response=response.model_dump(mode="json"), memory=memory.model_dump(mode="json"))

    builder = StateGraph(AgentState)
    for name, node in [
        ("initialize_turn", initialize), ("parse_question", parse), ("semantic_route", semantic_route),
        ("validate_slots_and_capabilities", validate), ("sql", call_sql),
        ("rag", call_rag), ("calculate", calculate),
        ('parallel_sources', parallel_sources),
        ('prepare_business',prepare_business),
        ("evidence_check", evidence_check), (compose_node, compose),
    ]:
        builder.add_node(name, node)
    builder.add_edge(START, "initialize_turn")
    builder.add_edge("initialize_turn", "parse_question")
    builder.add_edge("parse_question", "semantic_route")
    builder.add_edge("semantic_route", "validate_slots_and_capabilities")
    builder.add_conditional_edges("validate_slots_and_capabilities", start_route, ["sql", "rag", 'parallel_sources', compose_node])
    builder.add_conditional_edges('parallel_sources', after_parallel, ['calculate', 'evidence_check'])
    builder.add_conditional_edges("sql", after_sql, ["rag", "calculate", "evidence_check"])
    builder.add_conditional_edges("rag", after_rag, ["sql", "calculate", "evidence_check", "prepare_business"])
    builder.add_conditional_edges("prepare_business", after_business_plan, ["sql", "evidence_check"])
    builder.add_edge("calculate", "evidence_check")
    builder.add_edge("evidence_check", compose_node)
    builder.add_edge(compose_node, END)
    return builder.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())
