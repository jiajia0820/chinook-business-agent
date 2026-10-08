"""Bound submitted sync work, not just async waiters. Cancellation isn't kill."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import threading
import time
from typing import TypeVar

from ..contracts import ApiError
from ..tools.models import ToolFailure


T = TypeVar("T")


def _unavailable():
    return ToolFailure(ApiError(code="PROFILE_UNAVAILABLE", message="离线 SQL 执行器已关闭。", retryable=False))


class BoundedExecutor:
    """One event loop, at most max_workers submitted/running tasks; no backlog.

    On timeout/cancellation the waiter stops, but the permit is returned only
    by the underlying concurrent Future's completion callback. Threads cannot
    be forcibly cancelled; C owns database-side limits.
    """

    def __init__(self, max_workers: int = 2):
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be an integer in 1..16")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="c-offline-sql")
        self._semaphore = asyncio.Semaphore(max_workers)
        self._loop = None
        self._closed = False
        self._futures: set[Future] = set()
        self._guard = threading.Lock()

    @property
    def in_flight(self) -> int:
        with self._guard:
            return len(self._futures)

    def _bind_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise ValueError("executor cannot be reused across event loops")
        return loop

    async def run(self, work: Callable[[], T], *, deadline: float | None = None) -> T:
        loop = self._bind_loop()
        if self._closed:
            raise _unavailable()
        async with asyncio.timeout_at(deadline):
            await self._semaphore.acquire()
            submitted = False
            try:
                if self._closed:
                    raise _unavailable()
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError()
                future = self._executor.submit(work)
                with self._guard:
                    self._futures.add(future)
                submitted = True

                def complete(done):
                    with self._guard:
                        self._futures.discard(done)
                    # An already closed event loop cannot receive a release;
                    # that pool is never allowed on another loop anyway.
                    try:
                        loop.call_soon_threadsafe(self._semaphore.release)
                    except RuntimeError:
                        pass

                future.add_done_callback(complete)
                wrapped = asyncio.wrap_future(future, loop=loop)

                def consume_error(done):
                    if not done.cancelled():
                        done.exception()  # Avoid unobserved late failures after cancellation.

                wrapped.add_done_callback(consume_error)
                return await asyncio.shield(wrapped)
            finally:
                if not submitted:
                    self._semaphore.release()

    async def aclose(self, *, grace_seconds: float = 10) -> bool:
        """Stop submissions, wait a bounded grace period; False means still running."""
        loop = self._bind_loop()
        if not isinstance(grace_seconds, (int, float)) or isinstance(grace_seconds, bool) or not 0 <= grace_seconds <= 60:
            raise ValueError("grace_seconds must be finite in 0..60")
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._guard:
            futures = list(self._futures)
        if futures:
            wrapped = [asyncio.wrap_future(future, loop=loop) for future in futures]
            try:
                async with asyncio.timeout(grace_seconds):
                    await asyncio.shield(asyncio.gather(*wrapped, return_exceptions=True))
            except TimeoutError:
                return False
        return self.in_flight == 0
