"""Deterministic semantic checks for evidence dimensions."""

from __future__ import annotations

from .ledger import EvidenceItem
from .models import EvidenceScope


def _canonical_entity(value: int | str) -> str:
    if type(value) is int:
        # The legacy flattened shape used integer genre IDs.  Keep it
        # comparable with EvidenceScope.genre_ids without losing the type.
        return f"Genre:{value}"
    return value


def _scope_key(item: EvidenceItem) -> tuple[tuple[int, ...], tuple[str, ...], tuple[str, ...]] | None:
    scope: EvidenceScope | None = item.scope
    if scope is not None:
        return (
            tuple(scope.media_ids),
            tuple(scope.genre_ids),
            tuple(scope.country_ids),
        )

    # Legacy records carry entity_scope/media_scope separately.  Treat these
    # collections as sets because scope order does not change the fact.
    if not item.entity_scope and not item.media_scope:
        return None
    entities = {_canonical_entity(value) for value in item.entity_scope}
    genres = tuple(sorted(value for value in entities if value.startswith("Genre:")))
    countries = tuple(sorted(value for value in entities if value.startswith("Country:")))
    media = tuple(sorted(set(item.media_scope)))
    return media, genres, countries


def _metric(item: EvidenceItem) -> str | None:
    return item.metric or item.metric_id or (item.semantics.metric_id if item.semantics else None)


def _unit(item: EvidenceItem) -> str | None:
    return item.unit or (item.semantics.unit if item.semantics else None)


def _period(item: EvidenceItem):
    return item.period or (item.semantics.period if item.semantics else None)


def verify_same_scope(left: EvidenceItem, right: EvidenceItem) -> bool:
    """Return whether two evidence records can participate in one calculation.

    Actual/target records must agree on period, metric, unit and scope.  A
    growth-rate pair is represented by ``current`` and ``previous`` roles; its
    periods may differ, while the other dimensions remain required to match.
    """

    left_metric, right_metric = _metric(left), _metric(right)
    left_unit, right_unit = _unit(left), _unit(right)
    left_scope, right_scope = _scope_key(left), _scope_key(right)
    if None in (left_metric, right_metric, left_unit, right_unit, left_scope, right_scope):
        return False
    if left_metric != right_metric or left_unit != right_unit or left_scope != right_scope:
        return False

    left_period, right_period = _period(left), _period(right)
    if left_period is None or right_period is None:
        return False
    growth_pair = {left.fact_role, right.fact_role} == {"current", "previous"}
    return growth_pair or left_period == right_period

