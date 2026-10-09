"""FastAPI application factory for the public agent API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.routes.ask import router as ask_router
from .api.routes.health import router as health_router
from .contracts import ApiError
from .core.config import Settings
from .core.errors import ApplicationError
from .services.ask_service import AskService
from .services.backend_lifecycle import BackendFactory, BackendLifecycle, create_default_backend, unavailable


logger = logging.getLogger("agent_api")

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_runtime_environment() -> None:
    """Load .env before Settings.from_env so the API never silently falls back to offline.

    `uv run` does not reliably export .env here, and a missing AGENT_MODEL_MODE used to
    downgrade the whole product to the fixed-question demo model while the UI still
    presented the result as a real database answer.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("python-dotenv 不可用；本次仅使用进程环境变量")
        return
    for candidate in (PROJECT_ROOT / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


load_runtime_environment()


def _error_response(status_code: int, error: ApiError) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


def _validation_details(exc: RequestValidationError) -> list[dict[str, object]]:
    return [
        {
            "location": [str(part) for part in item.get("loc", ())],
            "message": item.get("msg", "Invalid value"),
            "type": item.get("type", "value_error"),
        }
        for item in exc.errors()
    ]


def create_app(
    *,
    settings: Settings | None = None,
    ask_service: AskService | None = None,
    backend_factory: BackendFactory | None = None,
) -> FastAPI:
    configuration_failed = False
    try:
        resolved_settings = settings if settings is not None else Settings.from_env()
        logger.info(
            "agent_api 配置 model_mode=%s backend_mode=%s sql_timeout_ms=%s",
            resolved_settings.model_mode, resolved_settings.backend_mode, resolved_settings.sql_timeout_ms,
        )
    except ValueError:
        # Keep HTTP liveness and structured 503 available; never log raw env.
        resolved_settings = Settings(backend_mode="unavailable")
        configuration_failed = True

    @asynccontextmanager
    async def lifespan(application):
        if application.state.lifespan_active:
            raise RuntimeError("application lifespan cannot overlap")
        application.state.lifespan_active = True
        application.state.backend_owner = None
        owner = None
        try:
            if ask_service is not None:
                application.state.ask_service = ask_service
                application.state.backend_state = "injected"
            elif configuration_failed or resolved_settings.backend_mode == "unavailable":
                application.state.ask_service = unavailable("CONFIG_INVALID" if configuration_failed else "BACKEND_DISABLED")
                application.state.backend_state = "failed" if configuration_failed else "disabled"
            else:
                application.state.backend_close_complete = None
                owner = BackendLifecycle(application, resolved_settings, backend_factory if backend_factory is not None else create_default_backend)
                await owner.start()
            yield
        finally:
            try:
                if owner is not None:
                    await owner.close()
                elif ask_service is None:
                    application.state.ask_service = unavailable("SERVICE_CLOSED")
                    application.state.backend_state = "stopped"
            finally:
                application.state.lifespan_active = False

    application = FastAPI(title=resolved_settings.app_name, version="0.1.0", lifespan=lifespan)
    application.state.ask_service = ask_service if ask_service is not None else unavailable("CONFIG_INVALID" if configuration_failed else "SERVICE_NOT_STARTED", retryable=not configuration_failed)
    application.state.backend_state = "injected" if ask_service is not None else "cold"
    application.state.backend_owner = None
    application.state.backend_close_complete = None
    application.state.lifespan_active = False
    application.state.settings = resolved_settings

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        incoming_request_id = request.headers.get("X-Request-ID", "").strip()
        request_id = (
            incoming_request_id
            if incoming_request_id and len(incoming_request_id) <= 128
            else f"req-{uuid4().hex}"
        )
        request.state.request_id = request_id
        started = perf_counter()

        try:
            response = await call_next(request)
        except Exception as exc:
            # Keep ordinary route failures inside the outer CORS middleware.
            response = await unexpected_error_handler(request, exc)
        response.headers["X-Request-ID"] = request_id
        duration_ms = round((perf_counter() - started) * 1000)
        logger.info(
            "request_complete method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response

    @application.exception_handler(ApplicationError)
    async def application_error_handler(
        request: Request, exc: ApplicationError
    ) -> JSONResponse:
        del request
        return _error_response(exc.status_code, exc.error)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        return _error_response(
            422,
            ApiError(
                code="INVALID_REQUEST",
                message="请求格式不符合接口契约。",
                retryable=False,
                details=_validation_details(exc),
            ),
        )

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        del request
        return _error_response(
            exc.status_code,
            ApiError(
                code="INVALID_REQUEST" if exc.status_code < 500 else "INTERNAL_ERROR",
                message=str(exc.detail),
                retryable=exc.status_code >= 500,
                details=None,
            ),
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception path=%s request_id=%s",
            request.url.path,
            getattr(request.state, "request_id", "unknown"),
        )
        response = _error_response(
            500,
            ApiError(
                code="INTERNAL_ERROR",
                message="服务内部异常。",
                retryable=True,
                details=None,
            ),
        )
        response.headers["X-Request-ID"] = getattr(request.state, "request_id", "req-" + uuid4().hex)
        return response

    # Last registered = outermost user middleware; includes sanitized 500s.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    application.include_router(health_router)
    application.include_router(ask_router)
    return application


app = create_app()
