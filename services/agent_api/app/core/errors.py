"""Application exceptions rendered with the contract's error shape."""

from __future__ import annotations

from services.agent_api.app.contracts import ApiError


class ApplicationError(Exception):
    def __init__(self, *, status_code: int, error: ApiError) -> None:
        super().__init__(error.message)
        self.status_code = status_code
        self.error = error


def service_unavailable_error() -> ApplicationError:
    return ApplicationError(
        status_code=503,
        error=ApiError(
            code="PROFILE_UNAVAILABLE",
            message="Agent 编排服务尚未配置。",
            retryable=True,
            details=None,
        ),
    )

