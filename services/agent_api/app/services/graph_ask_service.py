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
from .ask_service import AskStreamItem


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
            return self._error_response(payload, request_id, session_id, "INVALID_REQUEST", "本阶段问题最多支持 4000 个字符。", "clarification")
        try:
            self.profiles.get(payload.profile_id)
        except KeyError:
            return self._error_response(payload, request_id, session_id, "PROFILE_NOT_FOUND", "业务 profile 不存在。", "unsupported")
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

    def ask_stream(self, payload: AskRequest, *, request_id: str):
        """Preflight runs eagerly so admission/contract failures stay plain HTTP errors."""
        payload = AskRequest.model_validate(payload.model_dump(mode="python"))
        session_id = payload.session_id or "session-" + str(uuid.uuid4())
        if len(payload.question) > 4000:
            return self._single(self._error_response(payload, request_id, session_id, "INVALID_REQUEST", "本阶段问题最多支持 4000 个字符。", "clarification"))
        try:
            self.profiles.get(payload.profile_id)
        except KeyError:
            return self._single(self._error_response(payload, request_id, session_id, "PROFILE_NOT_FOUND", "业务 profile 不存在。", "unsupported"))
        return self._stream(payload, request_id, session_id)

    @staticmethod
    def _error_response(payload: AskRequest, request_id: str, session_id: str, code: str, message: str, route: str) -> AskResponse:
        return AskResponse(
            request_id=request_id, session_id=session_id, profile_id=payload.profile_id,
            status="error", answer=None, route=route, intent={"name": "unknown"},
            time_range=None, clarification=None,
            error={"code": code, "message": message, "retryable": False},
        )

    @staticmethod
    async def _single(response: AskResponse):
        yield ("result", response)

    async def _stream(self, payload: AskRequest, request_id: str, session_id: str):
        key = session_key(payload.profile_id, session_id)
        config = {"configurable": {"thread_id": key}, "recursion_limit": 16}
        async with self._locks.hold(key):
            # Same START-fresh rule as ask(); updates mode yields one chunk per node,
            # which is exactly the stage progress the SSE route reports.
            with tracing_context(enabled=False):
                response = None
                async for chunk in self.graph.astream({
                    "request": payload.model_dump(mode="json"), "request_id": request_id,
                    "session_id": session_id, "profile_id": payload.profile_id,
                }, config, stream_mode="updates"):
                    for node, updates in chunk.items():
                        yield ("stage", node)
                        if isinstance(updates, dict) and updates.get("response") is not None:
                            response = updates["response"]
                if response is None:
                    raise RuntimeError("graph finished without a composed response")
                yield ("result", AskResponse.model_validate(response))
