"""Per-worker offline backend owner and non-queuing HTTP admission boundary."""

import asyncio
from collections.abc import Awaitable, Callable
import logging
import time

from ..contracts import ApiError
from ..core.config import Settings
from ..core.errors import ApplicationError
from ..integrations.sql_d12 import OfflineKnowledgeIntegration, create_offline_knowledge_integration
from .ask_service import AskService, UnavailableAskService


logger = logging.getLogger("agent_api")
BackendFactory = Callable[[Settings], Awaitable[OfflineKnowledgeIntegration]]
STARTUP_REASONS = frozenset({"CONFIG_INVALID", "DEPENDENCY_MISSING", "CONFIG_OR_ARTIFACT_INVALID", "BACKEND_CONFIGURATION_INVALID", "BOOTSTRAP_FAILED", "STARTUP_TIMEOUT"})


def unavailable(reason: str, *, retryable: bool = False) -> UnavailableAskService:
    return UnavailableAskService(ApiError(code="PROFILE_UNAVAILABLE", message="离线 SQL+D12 服务当前不可用；没有回退替身或启用在线模型。",
        retryable=retryable, details={"reason": reason}))


async def create_default_backend(settings: Settings) -> OfflineKnowledgeIntegration:
    return await create_offline_knowledge_integration(max_workers=settings.sql_workers,
        sql_timeout_ms=settings.sql_timeout_ms, rag_timeout_ms=settings.rag_timeout_ms, model_mode=settings.model_mode)


class ManagedAskService:
    """Same-loop admission only, not authentication or a distributed rate limit."""
    def __init__(self, delegate: AskService, maximum: int):
        self.delegate, self.maximum = delegate, maximum
        self.accepting = True
        self.active: set[asyncio.Task] = set()

    async def ask(self, payload, *, request_id):
        if not self.accepting:
            return await unavailable("SERVICE_STOPPING").ask(payload, request_id=request_id)
        if len(self.active) >= self.maximum:
            return await unavailable("CAPACITY_EXCEEDED", retryable=True).ask(payload, request_id=request_id)
        task = asyncio.current_task()
        # No await between the admission check and reservation on one loop.
        self.active.add(task)
        try:
            return await self.delegate.ask(payload, request_id=request_id)
        finally:
            self.active.discard(task)

    def stop_accepting(self):
        self.accepting = False

    async def drain_or_cancel(self, timeout: float) -> bool:
        tasks = set(self.active)
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()  # Cancels async waiters, never kills underlying C threads.
        if pending:
            await asyncio.sleep(0)
        return not pending


class BackendLifecycle:
    def __init__(self, app, settings: Settings, factory: BackendFactory):
        self.app, self.settings, self.factory = app, settings, factory
        self.integration = None
        self.service = None
        self._closed = False

    async def start(self):
        self.app.state.backend_state = "starting"
        try:
            async with asyncio.timeout(self.settings.startup_timeout_seconds):
                self.integration = await self.factory(self.settings)
            profile = self.integration.profiles.get("chinook-music")
            if profile.capabilities != ["sql", "rag"] or profile.document_root != "data/knowledge":
                raise ValueError("backend configuration mismatch")
            delegate = self.integration.create_formal_graph_service()
            self.service = ManagedAskService(delegate, self.settings.max_inflight_requests)
            self.app.state.ask_service = self.service
            self.app.state.backend_owner = self
            self.app.state.backend_state = "ready"
            logger.info("offline_backend_ready mode=offline_sql_d12")
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as exc:
            reason = "STARTUP_TIMEOUT" if isinstance(exc, TimeoutError) else "BOOTSTRAP_FAILED"
            candidate = exc.error.details.get("reason") if isinstance(exc, ApplicationError) and isinstance(exc.error.details, dict) else None
            if isinstance(candidate, str) and candidate in STARTUP_REASONS:
                reason = candidate
            self.app.state.ask_service = unavailable(reason)
            self.app.state.backend_state = "failed"
            logger.warning("offline_backend_initialization_failed reason=%s", reason)
            await self._close_integration(self.settings.shutdown_grace_seconds)

    async def _close_integration(self, grace: float) -> bool:
        if self.integration is None or self._closed:
            return self.app.state.backend_close_complete is not False
        self._closed = True  # Exactly one close attempt, including failure paths.
        try:
            complete = await self.integration.aclose(grace_seconds=grace)
        except asyncio.CancelledError:
            self.app.state.backend_close_complete = False
            raise
        except Exception:
            complete = False
        self.app.state.backend_close_complete = complete is True
        if complete is not True:
            logger.warning("offline_backend_close_incomplete threads_may_still_be_running=true")
        return complete is True

    async def close(self):
        self.app.state.ask_service = unavailable("SERVICE_STOPPING")
        self.app.state.backend_state = "stopping"
        deadline = time.monotonic() + self.settings.shutdown_grace_seconds
        drained = True
        if self.service:
            self.service.stop_accepting()
            drained = await self.service.drain_or_cancel(max(0.0, deadline - time.monotonic()))
            if not drained:
                logger.warning("offline_backend_request_waiters_cancelled_at_shutdown")
        closed = await self._close_integration(max(0.0, deadline - time.monotonic()))
        self.app.state.backend_close_complete = drained and closed
        self.app.state.backend_state = "stopped"
        self.app.state.ask_service = unavailable("SERVICE_CLOSED")
