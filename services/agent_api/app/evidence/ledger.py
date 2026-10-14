"""Role-based evidence records used by cross-source workflows.

The ledger deliberately keeps the existing scalar semantic models usable.  New
callers can pass a :class:`ScalarSemantics` object through ``semantics`` while
older callers may continue to provide the flattened ``metric``/``period`` and
scope fields.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..tools.models import InternalModel
from .models import EvidencePeriod, EvidenceScope, ScalarSemantics


FactRole = Literal[
    "actual",
    "target",
    "formula",
    "review",
    "operation",
    "current",
    "previous",
]
SourceKind = Literal["sql", "document", "formula"]
ValidationStatus = Literal["unverified", "verified", "conflict"]


class EvidenceItem(InternalModel):
    """One role-labelled fact and the dimensions needed to compare it.

    ``semantics`` is an optional compatibility bridge to the canonical scalar
    models in :mod:`evidence.models`.  The flattened fields remain available
    for the first ledger API and for persisted records created by earlier
    callers.
    """

    fact_role: FactRole
    metric: str | None = None
    metric_id: str | None = None
    value: object | None = None
    unit: str | None = None
    period: EvidencePeriod | str | None = None
    scope: EvidenceScope | None = None
    entity_scope: list[int | str] = Field(default_factory=list)
    media_scope: list[int] = Field(default_factory=list)
    source_ref: str
    source_kind: SourceKind
    validation_status: ValidationStatus = "unverified"
    semantics: ScalarSemantics | None = None
    confirmed: bool | None = None

    @model_validator(mode="after")
    def normalize_semantics(self) -> "EvidenceItem":
        semantic = self.semantics

        canonical_metric = self.metric or self.metric_id
        if semantic is not None:
            if canonical_metric is not None and canonical_metric != semantic.metric_id:
                raise ValueError("metric does not match scalar semantics")
            canonical_metric = semantic.metric_id
            if self.period is not None and self.period != semantic.period:
                raise ValueError("period does not match scalar semantics")
            if self.scope is not None and self.scope != semantic.scope:
                raise ValueError("scope does not match scalar semantics")
            if self.unit is not None and self.unit != semantic.unit:
                raise ValueError("unit does not match scalar semantics")
            if self.value is not None and self.value != semantic.value:
                raise ValueError("value does not match scalar semantics")
            self.period = semantic.period
            self.scope = semantic.scope
            self.unit = semantic.unit
            self.value = semantic.value
            if self.confirmed is not None and self.confirmed != semantic.confirmed:
                raise ValueError("confirmed does not match scalar semantics")
            self.confirmed = semantic.confirmed

        if canonical_metric is None:
            raise ValueError("metric is required")
        self.metric = canonical_metric
        self.metric_id = canonical_metric
        if self.confirmed is None and semantic is None:
            self.confirmed = self.validation_status == "verified"
        return self


class EvidenceLedger(InternalModel):
    """Collection of evidence items addressable by fact role."""

    items: list[EvidenceItem] = Field(default_factory=list)

    def required(self, role: str) -> EvidenceItem:
        matches = [
            item
            for item in self.items
            if item.fact_role == role and item.validation_status == "verified"
        ]
        if len(matches) != 1:
            raise ValueError(f"expected one verified {role}, got {len(matches)}")
        return matches[0]
