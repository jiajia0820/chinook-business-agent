"""Injectable graph service; default offline ownership is managed by 3C-5 lifespan."""

import uuid
from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context

from ..agent.graph import CalculationBinder, build_agent_graph
from ..evidence.models import EvidenceResolver
from ..agent.session import SessionLocks, session_key
from ..contracts import AskRequest, AskResponse
from ..profiles import ProfileRegistry
from ..runtime import ToolRuntime
from ..response.models import ResponseComposer


class GraphAskService:
    def __init__(self, profiles: ProfileRegistry, runtime: ToolRuntime, *, binder: CalculationBinder | None = None, evidence_resolver: EvidenceResolver | None = None, response_composer: ResponseComposer | None = None, execution_mode: Literal["fixture", "offline_sql_integration", "offline_sql_d12_integration", "calculation_fixture"] = "fixture"):
        if runtime.profiles is not profiles:
            raise ValueError("graph and runtime must use the same profile registry")
        self.profiles = profiles
        self.checkpointer = InMemorySaver()
        self.graph = build_agent_graph(profiles, runtime, binder=binder, evidence_resolver=evidence_resolver, response_composer=response_composer, checkpointer=self.checkpointer, execution_mode=execution_mode)
        self._locks = SessionLocks()

    async def ask(self, payload: AskRequest, *, request_id: str) -> AskResponse:
        payload = AskRequest.model_validate(payload.model_dump(mode="python"))
        session_id = payload.session_id or "session-" + str(uuid.uuid4())
        if len(payload.question) > 4000:
            return AskResponse(
                request_id=request_id, session_id=session_id, profile_id=payload.profile_id,
                status="error", answer=None, route="clarification", intent={"name": "unknown"},
                time_range=None, clarification=None,
                error={"code": "INVALID_REQUEST", "message": "本阶段问题最多支持 4000 个字符。", "retryable": False},
            )
        try:
            self.profiles.get(payload.profile_id)
        except KeyError:
            return AskResponse(
                request_id=request_id, session_id=session_id, profile_id=payload.profile_id,
                status="error", answer=None, route="unsupported", intent={"name": "unknown"},
                time_range=None, clarification=None,
                error={"code": "PROFILE_NOT_FOUND", "message": "业务 profile 不存在。", "retryable": False},
            )
        key = session_key(payload.profile_id, session_id)
        config = {"configurable": {"thread_id": key}, "recursion_limit": 16}
        async with self._locks.hold(key):
            # Always new input from START; never ainvoke(None)/resume an abandoned
            # invocation after cancellation, never execute a saved tool plan.
            # No opt-in to third-party remote traces during this local checkpoint.
            with tracing_context(enabled=False):
                state = await self.graph.ainvoke({
                    "request": payload.model_dump(mode="json"), "request_id": request_id,
                    "session_id": session_id, "profile_id": payload.profile_id,
                }, config)
            return AskResponse.model_validate(state["response"])
