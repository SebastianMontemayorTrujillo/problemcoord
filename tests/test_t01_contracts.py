"""T01 — R01: versioned contracts reject invalid and grade-like fields; enums match the spec."""

import pytest
from pydantic import ValidationError

from hwreview import contracts as c
from hwreview.errors import ErrorCode

SHA = "a" * 64


def doc(**kw):
    base = dict(
        document_id=c.new_id("doc"), role="submission", assignment_id="A1", sha256=SHA,
        byte_count=10, page_count=1, storage_key="b" * 64,
    )
    base.update(kw)
    return c.Document(**base)


def versions():
    return c.VersionKey(
        rubric_version="r1", reference_set_version="ref1", prompt_version="p1",
        extraction_version="e1", provider_config_id="fake:v1",
    )


def span(document_id, page=1, paragraph=0, status="ok"):
    return c.EvidenceSpan(
        span_id=c.span_id(document_id, page, paragraph), document_id=document_id,
        page=page, paragraph=paragraph, text="x", status=status,
    )


def test_document_round_trip():
    d = doc()
    assert c.Document.model_validate_json(d.model_dump_json()) == d
    assert d.schema_version == "1.0"


@pytest.mark.parametrize("bad", [
    dict(role="teacher"), dict(sha256="xyz"), dict(sha256="A" * 64), dict(byte_count=0),
    dict(page_count=0), dict(assignment_id="../etc"), dict(storage_key="../x"),
    dict(document_id="doc_1"), dict(schema_version="2.0"),
])
def test_document_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        doc(**bad)


@pytest.mark.parametrize("field", ["grade", "score", "proof_verified", "unexpected"])
def test_unknown_and_grade_fields_rejected(field):
    with pytest.raises(ValidationError):
        doc(**{field: 1})
    payload = c.ReviewDraft(provider="fake", model="m", issues=[], uncertainty="low",
                            suggested_feedback="ok", needs_review=False).model_dump()
    payload[field] = True
    with pytest.raises(ValidationError):
        c.ReviewDraft.model_validate(payload)
    assert field not in c.ReviewResult.model_fields


def test_span_id_format_and_consistency():
    did = c.new_id("doc")
    s = span(did, page=2, paragraph=3)
    assert s.span_id == f"{did}:p2:g3"
    with pytest.raises(ValidationError):
        c.EvidenceSpan(span_id=f"{did}:p9:g9", document_id=did, page=2, paragraph=3,
                       text="x", status="ok")
    with pytest.raises(ValidationError):
        span(did, page=0)


def test_issue_requires_evidence():
    with pytest.raises(ValidationError):
        c.Issue(kind="logical_gap", description="d", evidence_ids=[])


def test_review_request_and_result_round_trip():
    did = c.new_id("doc")
    req = c.ReviewRequest(job_id=c.new_id("job"), assignment_id="A1",
                          submission_document_id=did, submission_spans=[span(did)],
                          reference_spans=[], versions=versions())
    assert c.ReviewRequest.model_validate_json(req.model_dump_json()) == req
    res = c.ReviewResult(
        result_id=c.new_id("res"), result_version=1, job_id=req.job_id, document_id=did,
        provider="fake", model="fake-1",
        issues=[c.Issue(kind="other", description="d", evidence_ids=[req.submission_spans[0].span_id])],
        uncertainty="low", suggested_feedback="f", review_state="draft", versions=versions(),
        created_at=c.utcnow(),
    )
    assert c.ReviewResult.model_validate_json(res.model_dump_json()) == res
    with pytest.raises(ValidationError):
        c.ReviewResult.model_validate({**res.model_dump(), "review_state": "approved"})
    with pytest.raises(ValidationError):
        c.ReviewResult.model_validate({**res.model_dump(), "result_version": 0})


def test_results_are_immutable():
    v = versions()
    with pytest.raises(ValidationError):
        v.rubric_version = "r2"


def test_teacher_decision():
    d = c.TeacherDecision(result_id=c.new_id("res"), result_version=1, reviewer="t1",
                          decision="approved", edited_feedback="good", decided_at=c.utcnow())
    assert d.decision == "approved"
    with pytest.raises(ValidationError):
        c.TeacherDecision.model_validate({**d.model_dump(), "decision": "draft"})


def test_enums_match_spec():
    assert set(c.ProcessingState.__args__) == {"queued", "running", "completed", "failed", "canceled"}
    assert set(c.ReviewState.__args__) == {"draft", "needs_review", "approved", "rejected"}
    assert set(c.Role.__args__) == {"submission", "reference"}
    assert {e.value for e in ErrorCode} == {
        "input_rejected", "extraction_uncertain", "timeout", "rate_limited", "provider_failure",
        "invalid_json", "unknown_evidence_id", "stale_lease", "invalid_transition", "not_found", "budget_exceeded",
    }
    assert {e.value for e in ErrorCode if e.retryable} == {
        "timeout", "rate_limited", "provider_failure", "invalid_json"}
