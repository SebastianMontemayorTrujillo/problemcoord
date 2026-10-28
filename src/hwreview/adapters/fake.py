"""Deterministic offline adapter for tests and demos. Simple text rules, NOT mathematics.

It never sees or uses reference wording, so it cannot demand it.
"""

import re

from hwreview.contracts import EvidenceSpan, Issue, ReviewDraft, ReviewRequest

_EXAMPLE = re.compile(r"\bfor n = -?\d+", re.IGNORECASE)
_GENERALIZE = re.compile(r"\b(so|therefore|hence|thus)\b.*\b(every|all)\b", re.IGNORECASE)


class FakeAdapter:
    config_id = "fake:v1"
    provider, model = "fake", "fake-rules-v1"

    def review(self, request: ReviewRequest) -> ReviewDraft:
        spans = request.submission_spans
        if not spans or any(s.status == "uncertain" for s in spans):
            return self._draft([], "high", True,
                               "Part of this submission could not be read reliably. A teacher "
                               "needs to review the original document before any feedback is sent.")
        body = [s for s in spans if not _is_problem_statement(s)]

        examples = [s for s in body if _EXAMPLE.search(s.text)]
        conclusion = next((s for s in body if _GENERALIZE.search(s.text)), None)
        if examples and conclusion:
            issue = Issue(kind="logical_gap",
                          description="The conclusion generalizes from finitely many checked "
                                      "cases to every integer; examples do not prove the general claim.",
                          evidence_ids=[examples[0].span_id, conclusion.span_id])
            return self._draft([issue], "medium", False,
                               "Your computed examples are correct, but checking a few values does "
                               "not prove the statement for every integer. Give an argument that "
                               "works for an arbitrary integer n.")

        if len(body) <= 1 and not any("=" in s.text for s in body):
            cited = (body or spans)[-1]
            issue = Issue(kind="missing_work",
                          description="The submission states a conclusion without supporting reasoning.",
                          evidence_ids=[cited.span_id])
            return self._draft([issue], "medium", False,
                               "You state the result but do not show why it holds. Please write "
                               "out the reasoning that justifies it for every case.")

        return self._draft([], "medium", False,
                           "The argument appears to address every case. A teacher should confirm "
                           "each step before this feedback is shared.")

    def _draft(self, issues, uncertainty, needs_review, feedback) -> ReviewDraft:
        return ReviewDraft(provider=self.provider, model=self.model, issues=issues,
                           uncertainty=uncertainty, suggested_feedback=feedback,
                           needs_review=needs_review)


def _is_problem_statement(span: EvidenceSpan) -> bool:
    return span.page == 1 and span.paragraph == 0 and span.text.lower().startswith("problem")
