"""Load replaceable JSON configurations without importing C or opening a DB."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from collections.abc import Iterable

from .models import BusinessProfile

_DEFAULTS_LOCK = threading.RLock()
_DEFAULTS_REGISTRY: "ProfileRegistry | None" = None


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

    def ids(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def select(self, profile_ids: tuple[str, ...] | list[str]) -> ProfileRegistry:
        selected = tuple(profile_ids)
        if not selected or len(selected) != len(set(selected)):
            raise ValueError("profile selection must contain unique IDs")
        missing = [profile_id for profile_id in selected if profile_id not in self._profiles]
        if missing:
            raise KeyError(missing[0])
        return ProfileRegistry(self._profiles[profile_id] for profile_id in selected)

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
        # Read-only server catalog: parse and validate once per process instead of
        # on every question turn.  ``get``/``select`` already deep-copy, so sharing
        # the snapshot cannot let a caller mutate trusted configuration.
        global _DEFAULTS_REGISTRY
        with _DEFAULTS_LOCK:
            if _DEFAULTS_REGISTRY is None:
                _DEFAULTS_REGISTRY = cls.from_directory(Path(__file__).parent / "configs")
            return _DEFAULTS_REGISTRY

    @classmethod
    def stage1(cls) -> ProfileRegistry:
        """Frozen historical SQL+D12 configuration for compatibility tests."""
        cls.defaults()  # Preserve startup validation and dependency injection hooks.
        return cls.from_directory(Path(__file__).parent / "history")
