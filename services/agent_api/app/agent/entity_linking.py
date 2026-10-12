"""Profile 驱动的实体别名、边界和排除条件识别。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..profiles import ProfileRegistry
from ..tools.models import InternalModel
from .semantic_ir import EntityMention, FieldProvenance


class EntityLinkResult(InternalModel):
    entities: list[EntityMention] = Field(default_factory=list)
    exclusions: list[tuple[str, int | str]] = Field(default_factory=list)
    boundary_status: Literal["confirmed", "ambiguous", "separate"] = "confirmed"
    excluded_mentions: list[EntityMention] = Field(default_factory=list)


def _entity_id(value: object) -> int | str:
    if isinstance(value, int):
        return value
    text = str(value)
    match = re.fullmatch(r"[^:]+:(\d+)", text)
    return int(match.group(1)) if match else text


def _catalog_entries(profile_id: str) -> list[dict[str, object]]:
    """Merge the standalone catalog with the existing profile.aliases.

    Existing aliases remain the compatibility source for runtime profiles;
    ``entities.json`` adds canonical English names and future profile data.
    """
    profile = None
    try:
        profile = ProfileRegistry.defaults().get(profile_id)
    except KeyError:
        if profile_id != "chinook":
            raise

    entries: dict[tuple[str, str], dict[str, object]] = {}
    if profile is not None:
        for alias in profile.aliases:
            key = (alias.entity_type, str(_entity_id(alias.entity_id)))
            item = entries.setdefault(key, {"entity_type": alias.entity_type, "id": _entity_id(alias.entity_id), "name": alias.label, "aliases": []})
            if alias.alias not in item["aliases"]:
                item["aliases"].append(alias.alias)

    catalog_path = Path(__file__).resolve().parents[4] / "profiles" / "chinook" / "entities.json"
    if catalog_path.exists() and profile_id in {"chinook", "chinook-music"}:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        for entity_type, values in payload.items():
            if entity_type == "media_scope" or not isinstance(values, list):
                continue
            normalized_type = {"genres": "genre", "countries": "country", "media_types": "media_type"}.get(entity_type, entity_type.rstrip("s"))
            for item in values:
                key = (normalized_type, str(_entity_id(item["id"])))
                merged = entries.setdefault(key, {"entity_type": normalized_type, "id": _entity_id(item["id"]), "name": item["name"], "aliases": []})
                merged["name"] = item.get("name", merged["name"])
                merged["aliases"].extend(item.get("aliases", []))

    return list(entries.values())


def _is_exclusion(question: str, start: int) -> bool:
    prefix = question[max(0, start - 12):start]
    return bool(re.search(r"(?:不包括|不包含|不含|不并入|排除|剔除)\s*$", prefix))


def link_entities(question: str, profile_id: str = "chinook-music") -> EntityLinkResult:
    """只做实体识别和边界判断，不执行 SQL 或检索。"""
    entries = _catalog_entries(profile_id)
    candidates: list[tuple[int, int, dict[str, object], str]] = []
    for entry in entries:
        terms = {str(entry["name"]), *(str(value) for value in entry.get("aliases", []))}
        for term in terms:
            if not term:
                continue
            pattern = re.escape(term)
            if term.isascii():
                pattern = rf"(?<![A-Za-z]){pattern}(?![A-Za-z])"
            for match in re.finditer(pattern, question, re.I):
                candidates.append((match.start(), match.end(), entry, match.group(0)))

    chosen: list[tuple[int, int, dict[str, object], str]] = []
    occupied: list[tuple[int, int]] = []
    for candidate in sorted(candidates, key=lambda item: (-(item[1] - item[0]), item[0])):
        start, end = candidate[:2]
        if any(start < other_end and end > other_start for other_start, other_end in occupied):
            continue
        occupied.append((start, end))
        chosen.append(candidate)

    chosen.sort(key=lambda item: item[0])
    included: list[EntityMention] = []
    excluded: list[EntityMention] = []
    for start, _end, entry, surface in chosen:
        is_included = not _is_exclusion(question, start)
        mention = EntityMention(
            surface=surface,
            entity_type=str(entry["entity_type"]),
            canonical_name=str(entry["name"]),
            entity_id=entry["id"],
            included=is_included,
            provenance=FieldProvenance(source="user", status="confirmed", evidence=[profile_id, surface]),
        )
        (included if is_included else excluded).append(mention)

    status: Literal["confirmed", "ambiguous", "separate"] = "confirmed"
    if "分开" in question or "分别" in question:
        status = "separate"
        included = [item.model_copy(update={"boundary_status": "separate"}) for item in included]
    elif len({(item.entity_type, item.entity_id) for item in included}) != len(included):
        status = "ambiguous"

    return EntityLinkResult(
        entities=included,
        exclusions=[(item.entity_type, item.entity_id) for item in excluded],
        boundary_status=status,
        excluded_mentions=excluded,
    )
