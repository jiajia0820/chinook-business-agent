"""Replaceable service boundary used by the public ask endpoint."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from ..contracts import ApiError, AskRequest, AskResponse
from ..core.errors import ApplicationError, service_unavailable_error


class AskService(Protocol):
    async def ask(self, payload: AskRequest, *, request_id: str) -> AskResponse:
        """Process one contract-valid question."""


class UnavailableAskService:
    """Cold/disabled/failed/closed service, never a successful fallback."""

    def __init__(self, error: ApiError | None = None):
        self.error = error.model_copy(deep=True) if error is not None else None

    async def ask(self, payload: AskRequest, *, request_id: str) -> AskResponse:
        del payload, request_id
        if self.error is not None:
            raise ApplicationError(status_code=503, error=self.error.model_copy(deep=True))
        raise service_unavailable_error()


def get_ask_service(request: Request) -> AskService:
    return request.app.state.ask_service
