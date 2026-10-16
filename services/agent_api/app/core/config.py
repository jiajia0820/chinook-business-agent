"""Environment-backed application configuration without external settings packages."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Literal


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Explainable Data Agent API"
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    backend_mode: Literal["offline_sql_d12", "unavailable"] = "offline_sql_d12"
    # ``run_mode`` is the single public source of truth. ``model_mode`` stays
    # as a compatibility alias for existing direct integration callers.
    run_mode: Literal["demo", "live"] = "live"
    model_mode: Literal["offline", "live"] | None = None
    enabled_profiles: tuple[str, ...] = ("chinook-music",)
    sql_workers: int = 2
    max_inflight_requests: int = 8
    sql_timeout_ms: int = 60000
    rag_timeout_ms: int = 5000
    startup_timeout_seconds: float = 30.0
    shutdown_grace_seconds: float = 10.0

    def __post_init__(self):
        # Never include environment values in validation errors or logs.
        supported_profiles = {"chinook-music", "adventureworks"}
        if self.model_mode is not None:
            if self.model_mode not in {"offline", "live"}:
                raise ValueError("unsupported model mode")
            requested = "demo" if self.model_mode == "offline" else "live"
            if self.run_mode != "live" and self.run_mode != requested:
                raise ValueError("run mode and model mode disagree")
            object.__setattr__(self, "run_mode", requested)
        object.__setattr__(self, "model_mode", "offline" if self.run_mode == "demo" else "live")
        if (type(self.backend_mode) is not str or self.backend_mode not in {"offline_sql_d12", "unavailable"}
                or self.run_mode not in {"demo", "live"}
                or not self.enabled_profiles
                or any(profile not in supported_profiles for profile in self.enabled_profiles)
                or len(self.enabled_profiles) != len(set(self.enabled_profiles))):
            raise ValueError("unsupported backend/profile configuration")
        # sql_timeout_ms 上限要覆盖 C 的整轮预算（模型重试 + 受控降级 = 115 秒），
        # 否则真实模型稍慢就会在降级前被切断，只能返回超时错误。
        for value, maximum in [(self.sql_workers, 16), (self.max_inflight_requests, 64), (self.sql_timeout_ms, 180000), (self.rag_timeout_ms, 60000)]:
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid backend integer setting")
        for value, minimum in [(self.startup_timeout_seconds, 0.05), (self.shutdown_grace_seconds, 0.0)]:
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= 30:
                raise ValueError("invalid backend time setting")

    @classmethod
    def from_env(cls) -> Settings:
        raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
        origins = tuple(
            origin.strip() for origin in raw_origins.split(",") if origin.strip()
        )
        try:
            raw_run_mode = os.getenv("AGENT_RUN_MODE", "").strip().lower()
            if not raw_run_mode:
                legacy_mode = os.getenv("AGENT_MODEL_MODE", "live").strip().lower()
                raw_run_mode = {"offline": "demo", "live": "live"}.get(legacy_mode, legacy_mode)
            return cls(cors_origins=origins or ("http://localhost:5173",),
                backend_mode=os.getenv("AGENT_BACKEND_MODE", "offline_sql_d12").strip(),
                run_mode=raw_run_mode,
                enabled_profiles=tuple(value.strip() for value in os.getenv("AGENT_ENABLED_PROFILES", "chinook-music").split(",") if value.strip()),
                sql_workers=int(os.getenv("AGENT_SQL_WORKERS", "2")),
                max_inflight_requests=int(os.getenv("AGENT_MAX_INFLIGHT_REQUESTS", "8")),
                sql_timeout_ms=int(os.getenv("AGENT_SQL_TIMEOUT_MS", "60000")),
                rag_timeout_ms=int(os.getenv("AGENT_RAG_TIMEOUT_MS", "5000")),
                startup_timeout_seconds=float(os.getenv("AGENT_STARTUP_TIMEOUT_SECONDS", "30")),
                shutdown_grace_seconds=float(os.getenv("AGENT_SHUTDOWN_GRACE_SECONDS", "10")))
        except (ValueError, TypeError):
            raise ValueError("invalid server backend configuration") from None

    @property
    def runtime_profile_ids(self) -> tuple[str, ...]:
        """Profiles allowed into the preliminary default backend."""
        return ("chinook-music",)
