"""Dispatch public ask requests to the integration for their selected profile."""

from __future__ import annotations

from collections.abc import Mapping

from ..contracts import ApiError, AskRequest, AskResponse
from ..profiles import ProfileRegistry
from .ask_service import AskService


def build_profile_catalog(
    profiles: ProfileRegistry,
    *,
    status: str | None = None,
    statuses: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    if status is None and statuses is None:
        raise ValueError("profile catalog needs a status or status map")
    return [
        {
            "profile_id": profile.profile_id,
            "display_name": profile.sql_backend.display_name if profile.sql_backend else profile.profile_id,
            "capabilities": list(profile.capabilities),
            "status": statuses.get(profile.profile_id, "unavailable") if statuses is not None else status,
        }
        for profile in (profiles.get(profile_id) for profile_id in profiles.ids())
    ]


class ProfileRouterAskService:
    """Route one request to exactly one profile-owned graph service."""

    def __init__(self, services: Mapping[str, AskService], *, unavailable_profile_ids: set[str] | frozenset[str] = frozenset()):
        if not services or len(services) != len(set(services)):
            raise ValueError("profile router needs unique services")
        self.services = dict(services)
        self.unavailable_profile_ids = frozenset(unavailable_profile_ids)

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(self.services)

    async def ask(self, payload: AskRequest, *, request_id: str) -> AskResponse:
        service = self.services.get(payload.profile_id)
        if service is None:
            session_id = payload.session_id or "session-unavailable"
            unavailable = payload.profile_id in self.unavailable_profile_ids
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
                    code="PROFILE_UNAVAILABLE" if unavailable else "PROFILE_NOT_FOUND",
                    message="该数据集在初赛中暂不可用。" if unavailable else "业务 profile 不存在。",
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

    def create_formal_graph_service(self, *, unavailable_profile_ids: set[str] | frozenset[str] = frozenset()) -> ProfileRouterAskService:
        services = {
            profile_id: self.integrations[profile_id].create_formal_graph_service()
            for profile_id in self.profiles.ids()
        }
        return ProfileRouterAskService(services, unavailable_profile_ids=unavailable_profile_ids)

    async def aclose(self, *, grace_seconds: float = 10) -> bool:
        complete = True
        for integration in self.integrations.values():
            try:
                closed = await integration.aclose(grace_seconds=grace_seconds)
            except Exception:
                closed = False
            complete = complete and closed is True
        return complete
