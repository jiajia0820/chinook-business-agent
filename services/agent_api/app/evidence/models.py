from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from ..contracts import RetrievalResponse
from ..contracts.models import NonEmptyString
from ..tools.models import InternalModel, SqlTaskOutcome


class EvidencePeriod(InternalModel):
    start: NonEmptyString
    end: NonEmptyString  # Exclusive; labels do not confer period correctness.

    @model_validator(mode="after")
    def check_dates(self):
        start, end = date.fromisoformat(self.start), date.fromisoformat(self.end)
        if start.isoformat() != self.start or end.isoformat() != self.end or start >= end:
            raise ValueError("invalid explicit period")
        return self


class EvidenceScope(InternalModel):
    media_ids: list[int] = Field(min_length=1)
    genre_ids: list[NonEmptyString] = Field(default_factory=list)
    country_ids: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_scope(self):
        if any(type(item) is not int or item < 1 for item in self.media_ids):
            raise ValueError("invalid media IDs")
        for values in (self.media_ids, self.genre_ids, self.country_ids):
            if len(values) != len(set(values)) or values != sorted(values):
                raise ValueError("scope must be unique and canonical")
        if any(not item.startswith("Genre:") for item in self.genre_ids) or any(not item.startswith("Country:") for item in self.country_ids):
            raise ValueError("invalid scope identities")
        return self


class ScalarSemantics(InternalModel):
    metric_id: NonEmptyString
    period: EvidencePeriod
    scope: EvidenceScope
    unit: NonEmptyString
    value: Decimal = Field(strict=False)
    confirmed: bool  # Only a curated server resolver may issue this attestation.


class SqlScalarEvidence(ScalarSemantics):
    kind: Literal["sql"] = "sql"
    query_id: NonEmptyString
    row_index: int = Field(ge=0)
    column: NonEmptyString
    result_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class DocumentLocator(InternalModel):
    chunk_id: NonEmptyString
    doc_id: NonEmptyString
    version: NonEmptyString
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    source_uri: NonEmptyString
    start: int = Field(ge=0)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def check_span(self):
        if self.start >= self.end:
            raise ValueError("invalid source span")
        return self


class DocumentScalarEvidence(ScalarSemantics):
    kind: Literal["document"] = "document"
    locator: DocumentLocator


class FormulaEvidence(InternalModel):
    function: Literal["difference", "attainment_rate", "growth_rate"]
    metric_id: NonEmptyString
    period: EvidencePeriod
    scope: EvidenceScope
    input_unit: NonEmptyString
    result_unit: NonEmptyString
    confirmed: bool
    locator: DocumentLocator


class CalculationEvidenceBundle(InternalModel):
    request_id: NonEmptyString
    session_id: NonEmptyString
    profile_id: NonEmptyString
    calculation_id: NonEmptyString
    source_mode: Literal["fixture", "verified_business"]
    function: Literal["difference", "attainment_rate", "growth_rate"]
    operands: dict[str, Annotated[SqlScalarEvidence | DocumentScalarEvidence, Field(discriminator="kind")]] = Field(min_length=2, max_length=2)
    formula: FormulaEvidence


class EvidenceResolver(Protocol):
    # This callable is injected by the server factory; never selected by the
    # client or executed from document text. Its metadata is an attestation,
    # not a proof that arbitrary SQL has the claimed business semantics.
    def __call__(self, task, sql: SqlTaskOutcome | None, rag: RetrievalResponse | None,
        calculation_id: str, context) -> CalculationEvidenceBundle | None: ...


class UnavailableEvidenceResolver:
    def __call__(self, task, sql, rag, calculation_id, context):
        return None  # Real confirmed targets/formulas have not been delivered.
