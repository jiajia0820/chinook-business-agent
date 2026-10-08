"""Load replaceable JSON configurations without importing C or opening a DB."""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Iterable

from .models import BusinessProfile


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON configuration key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON configuration number")


class ProfileRegistry:
    def __init__(self, profiles: Iterable[BusinessProfile]):
        self._profiles: dict[str, BusinessProfile] = {}
        for profile in profiles:
            validated = BusinessProfile.model_validate(profile.model_dump(mode="python"))
            if validated.profile_id in self._profiles:
                raise ValueError("duplicate public profile ID")
            self._profiles[validated.profile_id] = validated.model_copy(deep=True)

    def get(self, profile_id: str) -> BusinessProfile:
        # Consumers cannot mutate the registry's trusted shared configuration.
        return self._profiles[profile_id].model_copy(deep=True)

    @classmethod
    def from_directory(cls, directory: Path | str) -> ProfileRegistry:
        files = sorted(Path(directory).glob("*.json"))
        if not files:
            raise ValueError("profile directory has no JSON profiles")
        profiles = []
        for file in files:
            payload = json.loads(file.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
            profiles.append(BusinessProfile.model_validate(payload))
        return cls(profiles)

    @classmethod
    def defaults(cls) -> ProfileRegistry:
        return cls.from_directory(Path(__file__).parent / "configs")

    @classmethod
    def stage1(cls) -> ProfileRegistry:
        """Frozen historical SQL+D12 configuration for compatibility tests."""
        cls.defaults()  # Preserve startup validation and dependency injection hooks.
        return cls.from_directory(Path(__file__).parent / "history")
