"""Local validation of untrusted adapter output against its request."""

import json

from pydantic import ValidationError

from hwreview import contracts as c
from hwreview.errors import ErrorCode, HWError
from hwreview.extraction import needs_review


def parse_draft(raw) -> c.ReviewDraft:
    if isinstance(raw, c.ReviewDraft):
        return raw
    try:
        if isinstance(raw, (str, bytes)):
            raw = json.loads(raw)
        return c.ReviewDraft.model_validate(raw)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        raise HWError(ErrorCode.INVALID_JSON, "adapter output is not a valid ReviewDraft") from None


def build_result_fields(request: c.ReviewRequest, raw) -> dict:
    """Fields for a ReviewResult (minus result_id/version/created_at, assigned on commit)."""
    draft = parse_draft(raw)
    known = request.evidence_ids()
    unknown = {e for issue in draft.issues for e in issue.evidence_ids} - known
    if unknown:
        raise HWError(ErrorCode.UNKNOWN_EVIDENCE_ID, f"{len(unknown)} cited evidence ID(s) not in request")
    review = (needs_review(request.submission_spans) or draft.needs_review
              or draft.uncertainty == "high")
    return dict(job_id=request.job_id, document_id=request.submission_document_id,
                provider=draft.provider, model=draft.model, issues=list(draft.issues),
                uncertainty=draft.uncertainty, suggested_feedback=draft.suggested_feedback,
                review_state="needs_review" if review else "draft", versions=request.versions)
