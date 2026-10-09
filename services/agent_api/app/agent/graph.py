"""One LangGraph owns routing; runtime owns bounded, validated tool calls."""

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
import json
import re
import uuid
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from ..contracts import ApiError, AskRequest, AskResponse, Calculation, Intent, RetrievalRequest, RetrievalResponse
from ..evidence.binding import authorize_calculation
from ..evidence.models import EvidenceResolver
from ..profiles import ProfileRegistry
from ..runtime import ToolContext, ToolEvent, ToolRuntime, events_to_trace
from ..response.models import ComposeContext, ResponseComposer
from ..tools.models import BusinessContextItem, CalculationRequest, SqlTaskOutcome, SqlTaskRequest
from ..tools.references import sql_cell_ref
from .parsing import parse_question
from .state import AgentState, BusinessTask, ParsedTurn, SessionMemory


CalculationBinder = Callable[[BusinessTask, SqlTaskOutcome | None, RetrievalResponse | None, str], CalculationRequest | None]


def _snapshot(outcome):
    data = outcome.data.model_dump(mode="json", exclude={"diagnostics"}) if outcome.data is not None else None
    return {"status": outcome.status, "data": data, "error": outcome.error.model_dump(mode="json") if outcome.error else None}


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
        return {
            "memory": memory, "parsed": {}, "status": "", "route": "clarification",
            "tool_plan": [], "sql_outcome": None, "retrieval_outcome": None,
            "calculations": [], "calculation_binding": None, "calculation_evidence": None, "evidence_refs": [],
            "events": [], "node_path": ["initialize_turn"], "error": None, "response": None,
            "limitations": [source_banner, "当前解析覆盖已登记的业务指标、时间和实体范围；会话仅在本进程内保留。", *profile_limitations],
        }

    def parse(state: AgentState):
        request = AskRequest.model_validate(state["request"])
        parsed = parse_question(request.question, profiles.get(state["profile_id"]), SessionMemory.model_validate(state["memory"]))
        return mark(state, "parse_question", parsed=parsed.model_dump(mode="json"))

    def validate(state: AgentState):
        parsed = ParsedTurn.model_validate(state["parsed"])
        if parsed.decision == "ready" and evidence_resolver is not None and parsed.task.needs_calculation and parsed.task.route == "sql":
            if not profiles.get(state["profile_id"]).supports("rag"):
                parsed.decision, parsed.reason = "unsupported", "受控计算还需本轮公式文档来源，但当前 profile 不具备 RAG。"
            else:
                parsed.task.route = "cross_source"  # E.g. growth needs formula evidence too.
        statuses = {"clarification": "clarification_required", "unsupported": "unsupported", "cancelled": "clarification_required", "ready": "ready"}
        route = parsed.task.route if parsed.decision == "ready" else "unsupported" if parsed.decision == "unsupported" else "clarification"
        plan = []
        if parsed.decision == "ready":
            if route in ("sql", "cross_source"):
                plan.append("sql.task")
            if route in ("rag", "cross_source"):
                plan.append("rag.retrieve")
            if parsed.task.needs_calculation:
                plan.append("calculator.calculate")
        limitations = state["limitations"]
        if "partial_data_coverage" in parsed.rules:
            profile = profiles.get(state["profile_id"])
            limitations = [*limitations, f"请求周期超出样例数据实际覆盖 {profile.data_start} 至 {profile.data_end}，仅代表已有数据，不能声称完整周期统计。"]
        return mark(state, "validate_slots_and_capabilities", parsed=parsed.model_dump(mode="json"), status=statuses[parsed.decision], route=route, tool_plan=plan, limitations=limitations)

    def start_route(state):
        if state["status"] != "ready":
            return compose_node
        return "sql" if state["route"] in ("sql", "cross_source") else "rag"

    def context(state):
        return ToolContext(request_id=state["request_id"], session_id=state["session_id"], profile_id=state["profile_id"], available_source_refs=tuple(state["evidence_refs"]))

    async def call_sql(state: AgentState):
        parsed = ParsedTurn.model_validate(state["parsed"])
        task = parsed.task
        profile = profiles.get(state["profile_id"])
        request = AskRequest.model_validate(state["request"])
        definitions = [profile.metric(metric) for metric in task.business_metric_ids]
        business_context = {ref: definition.definition for definition in definitions for ref in definition.source_refs}
        payload = SqlTaskRequest(
            query_id="task-" + str(uuid.uuid4()), profile_id=profile.profile_id,
            question=task.original_question, normalized_question=task.normalized_question,
            entities=task.entities, time_range=task.time_range,
            resolved_slots={key: value for key, value in task.slots.items() if key in profile.sql_backend.declared_slots},
            metric_ids=[metric for metric in task.business_metric_ids if metric in profile.sql_backend.registered_metric_ids],
            business_context=[BusinessContextItem(ref_id=ref, text=text) for ref, text in business_context.items()][:20],
            max_rows=request.options.max_rows, show_trace=request.options.show_trace,
        )
        outcome = await runtime.run("sql.task", payload, context(state))
        refs = list(outcome.source_refs)
        if outcome.succeeded:
            for result in outcome.data.sql_results:
                refs.extend(sql_cell_ref(result.query_id, index, column) for index, row in enumerate(result.rows) for column, value in row.items() if type(value) in (int, float))
        return tool_update(state, "sql", outcome, "sql_outcome", refs)

    async def call_rag(state: AgentState):
        task = ParsedTurn.model_validate(state["parsed"]).task
        request = AskRequest.model_validate(state["request"])
        retrieval_query = task.original_question if task.route == "cross_source" else task.normalized_question
        payload = RetrievalRequest(retrieval_id="retrieval-" + str(uuid.uuid4()), profile_id=state["profile_id"], query=retrieval_query, filters={"slots": task.slots, "business_metric_ids": task.business_metric_ids}, top_k=request.options.top_k)
        outcome = await runtime.run("rag.retrieve", payload, context(state))
        return tool_update(state, "rag", outcome, "retrieval_outcome", list(outcome.source_refs))

    def tool_update(state, node, outcome, channel, refs):
        status, route, error = state["status"], state["route"], None
        if not outcome.succeeded:
            if outcome.status == "unsupported":
                status, route = "unsupported", "unsupported"
            else:
                status, error = "error", outcome.error.model_dump(mode="json")
        return mark(state, node, **{
            channel: _snapshot(outcome), "status": status, "route": route, "error": error,
            "events": [*state["events"], *(event.model_dump(mode="json") for event in outcome.events)],
            "evidence_refs": list(dict.fromkeys([*state["evidence_refs"], *refs])) if outcome.succeeded else state["evidence_refs"],
        })

    def after_sql(state):
        if state["status"] != "ready":
            return "evidence_check"
        if not any(result["rows"] for result in state["sql_outcome"]["data"]["sql_results"]):
            return "evidence_check"
        return "rag" if state["route"] == "cross_source" else "calculate" if "calculator.calculate" in state["tool_plan"] else "evidence_check"

    def after_rag(state):
        rag = state["retrieval_outcome"]
        if state["status"] != "ready" or not rag["data"]["chunks"]:
            return "evidence_check"
        return "calculate" if "calculator.calculate" in state["tool_plan"] else "evidence_check"

    async def calculate(state: AgentState):
        task = ParsedTurn.model_validate(state["parsed"]).task
        sql, rag = _models(state)
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
        return update

    def evidence_check(state: AgentState):
        status = state["status"]
        if status == "ready":
            sql, rag = _models(state)
            if state["route"] in ("sql", "cross_source") and (not sql or not any(result.rows for result in sql.sql_results)):
                status = "insufficient_evidence"
            if state["route"] in ("rag", "cross_source") and (not rag or not rag.chunks):
                status = "insufficient_evidence"
            if "calculator.calculate" in state["tool_plan"] and not state["calculations"]:
                status = "insufficient_evidence"
            if status == "ready":
                status = "answered"
        return mark(state, "evidence_check", status=status)

    def compose(state: AgentState):
        parsed = ParsedTurn.model_validate(state["parsed"])
        task = parsed.task
        memory = SessionMemory.model_validate(state["memory"])
        sql, rag = _models(state)
        status = state["status"]
        clarification = parsed.clarification
        answer = None
        if parsed.decision == "cancelled":
            memory = SessionMemory(profile_id=memory.profile_id, config_version=memory.config_version)
            from .parsing import clarify
            clarification = clarify(None, ["intent"]).clarification
            answer = parsed.reason
        elif status == "clarification_required":
            memory.pending, memory.last_task, memory.last_answer_summary = task, None, None
        else:
            memory.pending = None
            memory.last_task = task if status == "answered" else None
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
        options = AskRequest.model_validate(state["request"]).options
        limitations = [*state["limitations"], *(sql.limitations if sql else [])]
        if response_composer is not None:
            response = response_composer.compose(ComposeContext(
                request_id=state["request_id"], session_id=state["session_id"], profile=profiles.get(state["profile_id"]),
                status=status, route=state["route"], task=task, options=options, sql=sql, rag=rag,
                calculations=tuple(Calculation.model_validate(item) for item in state["calculations"]),
                binding=CalculationRequest.model_validate(state["calculation_binding"]) if state["calculation_binding"] else None,
                binding_audit=state["calculation_evidence"], available_source_refs=tuple(state["evidence_refs"]),
                events=tuple(events), limitations=tuple(limitations), clarification=clarification,
                error=ApiError.model_validate(state["error"]) if status == "error" else None,
                parser_reason=parsed.reason, reset_context=parsed.decision == "cancelled",
            ))
            if response.status != "clarification_required":
                memory.pending = None
                memory.last_task = task if response.status == "answered" else None
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
        ("initialize_turn", initialize), ("parse_question", parse),
        ("validate_slots_and_capabilities", validate), ("sql", call_sql),
        ("rag", call_rag), ("calculate", calculate),
        ("evidence_check", evidence_check), (compose_node, compose),
    ]:
        builder.add_node(name, node)
    builder.add_edge(START, "initialize_turn")
    builder.add_edge("initialize_turn", "parse_question")
    builder.add_edge("parse_question", "validate_slots_and_capabilities")
    builder.add_conditional_edges("validate_slots_and_capabilities", start_route, ["sql", "rag", compose_node])
    builder.add_conditional_edges("sql", after_sql, ["rag", "calculate", "evidence_check"])
    builder.add_conditional_edges("rag", after_rag, ["calculate", "evidence_check"])
    builder.add_edge("calculate", "evidence_check")
    builder.add_edge("evidence_check", compose_node)
    builder.add_edge(compose_node, END)
    return builder.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())
