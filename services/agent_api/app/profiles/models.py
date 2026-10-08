"""Business capabilities and explicit C-backend mappings."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from ..contracts import MetricDefinition
from ..contracts.models import NonEmptyString
from ..tools.models import Capability, InternalModel


class SlotRule(InternalModel):
    type: Literal["integer", "number", "boolean", "string"]
    values: list[JsonValue] | None = None
    required_by_default: bool = False

    def accepts(self, value: JsonValue) -> bool:
        accepted_types = {"integer": (int,), "number": (int, float), "boolean": (bool,), "string": (str,)}
        if type(value) not in accepted_types[self.type]:
            return False
        return self.values is None or any(type(item) is type(value) and item == value for item in self.values)


class SqlBackend(InternalModel):
    profile_id: NonEmptyString
    display_name: NonEmptyString
    model_mode: Literal["offline", "live"] = "offline"
    registered_metric_ids: list[NonEmptyString] = Field(default_factory=list)
    declared_slots: dict[str, SlotRule] = Field(default_factory=dict)


class CatalogPaths(InternalModel):
    # None means not delivered, rather than pretending that A supplied a file.
    dictionary: str | None = None
    aliases: str | None = None
    metrics: str | None = None
    documents: str | None = None


class ConfigSource(InternalModel):
    ref_id: NonEmptyString
    status: Literal["snapshot_verified", "document_derived_pending_alignment"]
    description: NonEmptyString


class EntityAlias(InternalModel):
    alias: NonEmptyString
    entity_type: NonEmptyString
    entity_id: NonEmptyString
    label: NonEmptyString


class OfflineQuery(InternalModel):
    canonical_question: NonEmptyString
    aliases: list[NonEmptyString] = Field(min_length=1)
    intent: NonEmptyString
    business_metric_ids: list[NonEmptyString] = Field(default_factory=list)


class DocumentQuery(InternalModel):
    canonical_question: NonEmptyString
    aliases: list[NonEmptyString] = Field(min_length=1)
    business_metric_ids: list[NonEmptyString] = Field(default_factory=list)


class QuestionRules(InternalModel):
    """Business vocabulary belongs to profiles, never the generic runtime."""

    metric_terms: dict[str, list[NonEmptyString]] = Field(default_factory=dict)
    unsupported_terms: dict[str, list[NonEmptyString]] = Field(default_factory=dict)
    ambiguous_scope_terms: list[NonEmptyString] = Field(default_factory=list)
    intent_terms: dict[str, list[NonEmptyString]] = Field(default_factory=dict)
    intent_default_metric_ids: dict[str, list[NonEmptyString]] = Field(default_factory=dict)
    query_fillers: list[NonEmptyString] = Field(default_factory=list)
    offline_queries: list[OfflineQuery] = Field(default_factory=list)
    document_queries: list[DocumentQuery] = Field(default_factory=list)


class BusinessProfile(InternalModel):
    profile_id: NonEmptyString
    config_version: NonEmptyString
    mode: Literal["sql_only", "rag_only", "hybrid"]
    capabilities: list[Capability]
    sql_backend: SqlBackend | None = None
    document_root: str | None = None  # Deployment-relative; no tmp/absolute review path.
    catalogs: CatalogPaths = Field(default_factory=CatalogPaths)
    metric_definitions: list[MetricDefinition] = Field(default_factory=list)
    aliases: list[EntityAlias] = Field(default_factory=list)
    question_rules: QuestionRules = Field(default_factory=QuestionRules)
    required_slots_by_intent: dict[str, list[NonEmptyString]] = Field(default_factory=dict)
    unsupported_metrics: list[NonEmptyString] = Field(default_factory=list)
    default_media_ids: list[int] = Field(default_factory=list)
    data_start: str | None = None
    data_end: str | None = None
    currency: str | None = None
    sources: list[ConfigSource] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_configuration(self) -> BusinessProfile:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate capabilities")
        if self.mode == "sql_only" and "rag" in self.capabilities:
            raise ValueError("sql_only cannot enable rag")
        if self.mode == "rag_only" and "sql" in self.capabilities:
            raise ValueError("rag_only cannot enable sql")
        if "sql" in self.capabilities and self.sql_backend is None:
            raise ValueError("SQL capability needs an explicit backend mapping")
        if "rag" in self.capabilities and not self.document_root:
            raise ValueError("RAG capability needs a document root")
        ids = [metric.metric_id for metric in self.metric_definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate metric definitions")
        if not set(self.question_rules.metric_terms) <= set(ids):
            raise ValueError("question rules reference unknown business metrics")
        if any(not set(metrics) <= set(ids) for metrics in self.question_rules.intent_default_metric_ids.values()):
            raise ValueError("intent defaults reference unknown business metrics")
        offline_aliases = [alias.strip().rstrip("？?") for query in self.question_rules.offline_queries for alias in query.aliases]
        if len(offline_aliases) != len(set(offline_aliases)):
            raise ValueError("duplicate offline question alias")
        if any(not set(query.business_metric_ids) <= set(ids) for query in self.question_rules.offline_queries):
            raise ValueError("offline query references unknown business metrics")
        document_aliases = [alias.strip().rstrip("？?") for query in self.question_rules.document_queries for alias in query.aliases]
        if len(document_aliases) != len(set(document_aliases)) or set(document_aliases) & set(offline_aliases):
            raise ValueError("duplicate/conflicting document question alias")
        if any(not set(query.business_metric_ids) <= set(ids) for query in self.question_rules.document_queries):
            raise ValueError("document query references unknown business metrics")
        if not set(self.question_rules.unsupported_terms) <= set(self.unsupported_metrics):
            raise ValueError("unsupported vocabulary needs a declared data gap")
        if self.sql_backend:
            registered = self.sql_backend.registered_metric_ids
            if len(registered) != len(set(registered)) or not set(registered) <= set(ids):
                raise ValueError("registered C metrics must have unique known definitions")
        if bool(self.data_start) != bool(self.data_end):
            raise ValueError("data range requires both endpoints")
        if self.data_start and self.data_end:
            if date.fromisoformat(self.data_start) > date.fromisoformat(self.data_end):
                raise ValueError("data range is reversed")
        return self

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def metric(self, metric_id: str) -> MetricDefinition | None:
        return next((metric for metric in self.metric_definitions if metric.metric_id == metric_id), None)
