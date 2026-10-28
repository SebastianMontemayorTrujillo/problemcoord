"""Versioned domain contracts (schema 1.0). Must not import provider SDKs or agent frameworks."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"

Role = Literal["submission", "reference"]
ProcessingState = Literal["queued", "running", "completed", "failed", "canceled"]
ReviewState = Literal["draft", "needs_review", "approved", "rejected"]
SpanStatus = Literal["ok", "uncertain"]
Uncertainty = Literal["low", "medium", "high"]
IssueKind = Literal["logical_gap", "missing_work", "unclear", "other"]

TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "canceled"})


def _id(prefix: str):
    return Annotated[str, Field(pattern=rf"^{prefix}_[0-9a-f]{{32}}$")]


DocumentId = _id("doc")
JobId = _id("job")
BatchId = _id("bat")
ResultId = _id("res")
AssignmentId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmpty = Annotated[str, Field(min_length=1, max_length=200)]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def span_id(document_id: str, page: int, paragraph: int) -> str:
    return f"{document_id}:p{page}:g{paragraph}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ErrorInfo(_Model):
    code: str
    message: str
    retryable: bool


class Document(_Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    document_id: DocumentId
    role: Role
    assignment_id: AssignmentId
    sha256: Sha256
    byte_count: int = Field(ge=1)
    page_count: int = Field(ge=1)
    storage_key: Sha256


class EvidenceSpan(_Model):
    span_id: str
    document_id: DocumentId
    page: int = Field(ge=1)
    paragraph: int = Field(ge=0)
    text: str
    status: SpanStatus

    @model_validator(mode="after")
    def _consistent_id(self):
        if self.span_id != span_id(self.document_id, self.page, self.paragraph):
            raise ValueError("span_id does not match document/page/paragraph")
        return self


class VersionKey(_Model):
    rubric_version: NonEmpty
    reference_set_version: NonEmpty
    prompt_version: NonEmpty
    extraction_version: NonEmpty
    provider_config_id: NonEmpty


class ReviewRequest(_Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    job_id: JobId
    assignment_id: AssignmentId
    submission_document_id: DocumentId
    submission_spans: list[EvidenceSpan]
    reference_spans: list[EvidenceSpan] = []
    versions: VersionKey

    def evidence_ids(self) -> set[str]:
        return {s.span_id for s in self.submission_spans} | {s.span_id for s in self.reference_spans}


class Issue(_Model):
    kind: IssueKind
    description: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1)


class ReviewDraft(_Model):
    """Adapter output. Untrusted until validated against its request."""
    provider: NonEmpty
    model: NonEmpty
    issues: list[Issue]
    uncertainty: Uncertainty
    suggested_feedback: str = Field(max_length=5000)
    needs_review: bool


class ReviewResult(_Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    result_id: ResultId
    result_version: int = Field(ge=1)
    job_id: JobId
    document_id: DocumentId
    provider: NonEmpty
    model: NonEmpty
    issues: list[Issue]
    uncertainty: Uncertainty
    suggested_feedback: str
    review_state: Literal["draft", "needs_review"]
    versions: VersionKey
    created_at: datetime


class TeacherDecision(_Model):
    result_id: ResultId
    result_version: int = Field(ge=1)
    reviewer: NonEmpty
    decision: Literal["approved", "rejected"]
    edited_feedback: str = Field(max_length=5000)
    decided_at: datetime
