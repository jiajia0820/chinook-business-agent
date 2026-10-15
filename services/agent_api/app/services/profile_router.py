"""Dispatch public ask requests to the integration for their selected profile."""

from __future__ import annotations

from collections.abc import Mapping

from ..contracts import ApiError, AskRequest, AskResponse
from ..profiles import ProfileRegistry
from .ask_service import AskService


class ProfileRouterAskService:
    """Route one request to exactly one profile-owned graph service."""

    def __init__(self, services: Mapping[str, AskService]):
        if not services or len(services) != len(set(services)):
            raise ValueError("profile router needs unique services")
        self.services = dict(services)

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(self.services)

    async def ask(self, payload: AskRequest, *, request_id: str) -> AskResponse:
        service = self.services.get(payload.profile_id)
        if service is None:
            session_id = payload.session_id or "session-unavailable"
            return AskResponse(
                request_id=request_id,
                session_id=session_id,
                profile_id=payload.profile_id,
                status="error",
                answer=None,
                route="unsupported",
                intent={"name": "unknown"},
                time_range=None,
                clarification=None,
                error=ApiError(
                    code="PROFILE_NOT_FOUND",
                    message="业务 profile 不存在。",
                    retryable=False,
                ),
            )
        return await service.ask(payload, request_id=request_id)


class CompositeBackend:
    """Own multiple profile integrations while exposing one lifecycle boundary."""

    def __init__(self, profiles: ProfileRegistry, integrations: Mapping[str, object]):
        selected = tuple(profiles.ids())
        if selected != tuple(integrations):
            raise ValueError("backend integrations must match profile order")
        self.profiles = profiles
        self.integrations = dict(integrations)

    def create_formal_graph_service(self) -> ProfileRouterAskService:
        services = {
            profile_id: self.integrations[profile_id].create_formal_graph_service()
            for profile_id in self.profiles.ids()
        }
        return ProfileRouterAskService(services)

    async def aclose(self, *, grace_seconds: float = 10) -> bool:
        complete = True
        for integration in self.integrations.values():
            try:
                closed = await integration.aclose(grace_seconds=grace_seconds)
            except Exception:
                closed = False
            complete = complete and closed is True
        return complete
