"""Single worker: claim → extract → request → adapter → validate → commit, with cancellation
checks (and lease heartbeats) between stages. Logs carry IDs and error codes only."""

import logging
import sqlite3
import threading
import time
from collections.abc import Callable

from hwreview import contracts as c
from hwreview.config import DEFAULT_LIMITS, Limits
from hwreview.errors import ErrorCode, HWError
from hwreview.extraction import Extractor, PypdfExtractor, save_spans
from hwreview.intake import get_document
from hwreview.jobs import Claim, Queue
from hwreview.storage import BlobStore
from hwreview.validate import build_result_fields

log = logging.getLogger("hwreview.worker")


class _Canceled(Exception):
    pass


# No readable text at all: never ask a model to review nothing (it could only invent content).
_EXTRACTION_GATE = c.ReviewDraft(
    provider="system", model="extraction-gate", issues=[], uncertainty="high", needs_review=True,
    suggested_feedback="No readable text was found in this submission. Review the original "
                       "document; no draft feedback was generated.")


def _readable(spans: list[c.EvidenceSpan]) -> bool:
    return any(s.status == "ok" for s in spans)


class Worker:
    def __init__(self, conn: sqlite3.Connection, store: BlobStore, adapter,
                 extractor: Extractor | None = None, limits: Limits = DEFAULT_LIMITS,
                 clock: Callable[[], float] = time.time):
        self.conn, self.store, self.adapter = conn, store, adapter
        self.extractor = extractor or PypdfExtractor(limits)
        self.queue = Queue(conn, limits, clock)

    def run_once(self) -> str | None:
        claim = self.queue.claim()
        if claim is None:
            return None
        self.process_claim(claim)
        return claim.job_id

    def drain(self) -> list[str]:
        done = []
        while (job_id := self.run_once()) is not None:
            done.append(job_id)
        return done

    def run_forever(self, poll_s: float = 1.0, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            if self.run_once() is None:
                stop.wait(poll_s)

    def process_claim(self, claim: Claim) -> dict | None:
        """Run the pipeline for one claim. Returns the committed result fields, else None."""
        job_id, token = claim.job_id, claim.token
        started = time.monotonic()
        try:
            job = self.queue.get(job_id)
            self._checkpoint(claim)
            doc = get_document(self.conn, job.document_id)
            spans = self.extractor.extract(doc.document_id, self.store.get(doc.storage_key))
            save_spans(self.conn, spans)
            references = self._reference_spans(job.batch_id)
            self._checkpoint(claim)
            request = c.ReviewRequest(job_id=job_id, assignment_id=doc.assignment_id,
                                      submission_document_id=doc.document_id,
                                      submission_spans=spans, reference_spans=references,
                                      versions=job.versions)
            raw = self.adapter.review(request) if _readable(spans) else _EXTRACTION_GATE
            self._checkpoint(claim)  # late output after cancellation is discarded here
            fields = build_result_fields(request, raw)
            result = self.queue.complete(job_id, token, fields)
            log.info("job=%s completed result=%s attempt=%d %.2fs", job_id,
                     result and result.result_id, claim.attempt, time.monotonic() - started)
            return fields if result else None
        except _Canceled:
            self._quietly(self.queue.mark_canceled, job_id, token)
            log.info("job=%s canceled", job_id)
        except HWError as e:
            if e.code is ErrorCode.STALE_LEASE:
                log.warning("job=%s stale lease; output discarded", job_id)
            else:
                state = self._quietly(self.queue.fail, job_id, token, e)
                log.warning("job=%s error=%s -> %s", job_id, e.code.value, state)
        except Exception as e:  # never let one item kill the worker
            err = HWError(ErrorCode.PROVIDER_FAILURE, f"unexpected {type(e).__name__}")
            state = self._quietly(self.queue.fail, job_id, token, err)
            log.error("job=%s unexpected %s -> %s", job_id, type(e).__name__, state)
        return None

    def _checkpoint(self, claim: Claim) -> None:
        if self.queue.heartbeat(claim.job_id, claim.token):
            raise _Canceled

    def _reference_spans(self, batch_id: str) -> list[c.EvidenceSpan]:
        spans = []
        rows = self.conn.execute(
            "SELECT d.document_id FROM batch_references br JOIN documents d USING (document_id)"
            " WHERE br.batch_id = ? AND d.role = 'reference' ORDER BY d.document_id", (batch_id,))
        for row in rows.fetchall():
            ref = get_document(self.conn, row["document_id"])
            ref_spans = self.extractor.extract(ref.document_id, self.store.get(ref.storage_key))
            save_spans(self.conn, ref_spans)
            spans.extend(s for s in ref_spans if s.status == "ok")
        return spans

    @staticmethod
    def _quietly(fn, *args):
        try:
            return fn(*args)
        except HWError as e:
            log.warning("could not record outcome: %s", e.code.value)
            return None
