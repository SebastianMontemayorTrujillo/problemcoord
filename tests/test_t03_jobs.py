"""T03 — R03: durable jobs. Commit before ack, legal transitions, restart, leases,
stale tokens, bounded attempts, cancellation discards late output."""

from dataclasses import replace

import pytest

from conftest import CountingAdapter, load_fixture_batch
from hwreview import db
from hwreview.adapters.fake import FakeAdapter
from hwreview.batches import create_batch
from hwreview.config import DEFAULT_LIMITS
from hwreview.errors import ErrorCode, HWError
from hwreview.jobs import Queue
from hwreview.worker import Worker

LIMITS = replace(DEFAULT_LIMITS, lease_s=60, max_attempts=2)


def setup(env, fixture_dir, clock, ids=("F1", "F2", "F3", "F4", "F5"), adapter=None):
    adapter = adapter or CountingAdapter(FakeAdapter())
    spec, refs, subs = load_fixture_batch(fixture_dir, ids)
    batch = create_batch(env.conn, env.store, spec, refs, subs, adapter.config_id, LIMITS)
    queue = Queue(env.conn, LIMITS, clock)
    worker = Worker(env.conn, env.store, adapter, limits=LIMITS, clock=clock)
    return batch, queue, worker, adapter


def job_ids(batch):
    return [i.job_id for i in batch.items if i.job_id]


def test_job_committed_before_ack(env, fixture_dir, clock):
    batch, *_ = setup(env, fixture_dir, clock, ids=("F1",))
    other = db.connect(env.db_path)
    try:
        row = other.execute("SELECT state FROM jobs WHERE job_id = ?", (job_ids(batch)[0],)).fetchone()
        assert row["state"] == "queued"
    finally:
        other.close()


def test_fixture_batch_outcomes_and_f5_never_reaches_adapter(env, fixture_dir, clock):
    batch, queue, worker, adapter = setup(env, fixture_dir, clock)
    statuses = {i.name: i.status for i in batch.items}
    assert statuses["F5.pdf"] == "rejected" and len(job_ids(batch)) == 4
    worker.drain()
    assert len(adapter.calls) == 3  # F4 has no readable text: extraction gate, no model call
    states = {}
    for item in batch.items:
        if item.job_id:
            job = queue.get(item.job_id)
            assert job.state == "completed"
            states[item.name] = queue.get_result(job.result_id, job.result_version).review_state
    assert states == {"F1.pdf": "draft", "F2.pdf": "draft", "F3.pdf": "draft", "F4.pdf": "needs_review"}


def test_legal_transitions(env, fixture_dir, clock):
    batch, queue, worker, _ = setup(env, fixture_dir, clock, ids=("F1",))
    jid = job_ids(batch)[0]
    with pytest.raises(HWError) as e:
        queue.complete(jid, "not-a-token", {})
    assert e.value.code in (ErrorCode.INVALID_TRANSITION, ErrorCode.STALE_LEASE)
    claim = queue.claim()
    assert claim.job_id == jid and queue.get(jid).state == "running"
    worker.drain()  # nothing else claimable while leased
    assert queue.get(jid).state == "running"
    assert queue.heartbeat(jid, claim.token) is False
    with pytest.raises(HWError) as e:
        queue.cancel("job_" + "0" * 32)
    assert e.value.code is ErrorCode.NOT_FOUND


def test_worker_restart_processes_committed_jobs(env, fixture_dir, clock):
    batch, *_ = setup(env, fixture_dir, clock, ids=("F1", "F2"))
    env.conn.close()  # simulated crash before any processing
    conn = db.connect(env.db_path)
    try:
        worker = Worker(conn, env.store, FakeAdapter(), limits=LIMITS, clock=clock)
        assert len(worker.drain()) == 2
        queue = Queue(conn, LIMITS, clock)
        assert all(queue.get(j).state == "completed" for j in job_ids(batch))
    finally:
        conn.close()
    env.conn = db.connect(env.db_path)


def test_expired_lease_reclaimed_only_after_expiry(env, fixture_dir, clock):
    batch, queue, *_ = setup(env, fixture_dir, clock, ids=("F1",))
    first = queue.claim()  # worker dies without completing
    clock.advance(LIMITS.lease_s - 1)
    assert queue.claim() is None
    clock.advance(2)
    second = queue.claim()
    assert second.job_id == first.job_id and second.token != first.token
    assert queue.get(first.job_id).attempts == 2


def test_stale_token_cannot_overwrite(env, fixture_dir, clock):
    batch, queue, *_ = setup(env, fixture_dir, clock, ids=("F1",))
    jid = job_ids(batch)[0]
    old = queue.claim()
    clock.advance(LIMITS.lease_s + 1)
    new = queue.claim()
    worker = Worker(env.conn, env.store, FakeAdapter(), limits=LIMITS, clock=clock)
    fields = worker.process_claim(new)  # new holder completes
    assert queue.get(jid).state == "completed"
    for call in (lambda: queue.complete(jid, old.token, fields),
                 lambda: queue.heartbeat(jid, old.token),
                 lambda: queue.fail(jid, old.token, HWError(ErrorCode.TIMEOUT, "late"))):
        with pytest.raises(HWError) as e:
            call()
        assert e.value.code is ErrorCode.STALE_LEASE
    assert env.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 1


def test_attempt_budget_exhaustion_fails_visibly(env, fixture_dir, clock):
    batch, queue, *_ = setup(env, fixture_dir, clock, ids=("F1",))
    jid = job_ids(batch)[0]
    for _ in range(LIMITS.max_attempts):
        assert queue.claim() is not None
        clock.advance(LIMITS.lease_s + 1)
    assert queue.claim() is None
    job = queue.get(jid)
    assert job.state == "failed" and job.error_code == "timeout"


def test_retryable_errors_bounded(env, fixture_dir, clock):
    def boom(_):
        raise HWError(ErrorCode.TIMEOUT, "provider timeout")
    adapter = CountingAdapter(FakeAdapter(), hook=boom)
    batch, queue, worker, _ = setup(env, fixture_dir, clock, ids=("F1",), adapter=adapter)
    worker.drain()
    job = queue.get(job_ids(batch)[0])
    assert job.state == "failed" and job.error_code == "timeout"
    assert len(adapter.calls) == LIMITS.max_attempts


def test_non_retryable_error_fails_once_and_batch_continues(env, fixture_dir, clock):
    def bad_for_f2(req):
        if any("For n = 1" in s.text for s in req.submission_spans):
            return {"provider": "fake", "model": "m", "issues": [
                {"kind": "other", "description": "x", "evidence_ids": ["doc_x:p9:g9"]}],
                "uncertainty": "low", "suggested_feedback": "f", "needs_review": False}
        return None
    adapter = CountingAdapter(FakeAdapter(), hook=bad_for_f2)
    batch, queue, worker, _ = setup(env, fixture_dir, clock, ids=("F1", "F2", "F3"), adapter=adapter)
    worker.drain()
    by_name = {i.name: queue.get(i.job_id) for i in batch.items}
    assert by_name["F2.pdf"].state == "failed"
    assert by_name["F2.pdf"].error_code == "unknown_evidence_id" and by_name["F2.pdf"].attempts == 1
    assert by_name["F1.pdf"].state == by_name["F3.pdf"].state == "completed"


def test_invalid_json_is_typed(env, fixture_dir, clock):
    adapter = CountingAdapter(FakeAdapter(), hook=lambda _: "{not json")
    batch, queue, worker, _ = setup(env, fixture_dir, clock, ids=("F1",), adapter=adapter)
    worker.drain()
    job = queue.get(job_ids(batch)[0])
    assert job.state == "failed" and job.error_code == "invalid_json"


def test_cancel_queued_job_never_runs(env, fixture_dir, clock):
    batch, queue, worker, adapter = setup(env, fixture_dir, clock, ids=("F1",))
    jid = job_ids(batch)[0]
    assert queue.cancel(jid) == "canceled"
    worker.drain()
    assert adapter.calls == [] and queue.get(jid).state == "canceled"
    with pytest.raises(HWError) as e:
        queue.complete(jid, "x", {})
    assert e.value.code in (ErrorCode.INVALID_TRANSITION, ErrorCode.STALE_LEASE)


def test_cancel_during_run_discards_late_output(env, fixture_dir, clock):
    holder = {}

    def cancel_mid_flight(req):
        holder["state"] = holder["queue"].cancel(req.job_id)
        return None  # adapter still returns output (request could not be interrupted)
    adapter = CountingAdapter(FakeAdapter(), hook=cancel_mid_flight)
    batch, queue, worker, _ = setup(env, fixture_dir, clock, ids=("F1",), adapter=adapter)
    holder["queue"] = queue
    worker.drain()
    jid = job_ids(batch)[0]
    assert holder["state"] == "running"  # flagged, not yet terminal
    job = queue.get(jid)
    assert job.state == "canceled" and job.result_id is None
    assert env.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0


def test_cancel_terminal_job_is_invalid(env, fixture_dir, clock):
    batch, queue, worker, _ = setup(env, fixture_dir, clock, ids=("F1",))
    worker.drain()
    with pytest.raises(HWError) as e:
        queue.cancel(job_ids(batch)[0])
    assert e.value.code is ErrorCode.INVALID_TRANSITION


def test_queue_complete_after_cancel_request_discards(env, fixture_dir, clock):
    """The queue itself (not only the worker checkpoint) discards output after cancellation."""
    batch, queue, worker, _ = setup(env, fixture_dir, clock, ids=("F1",))
    claim = queue.claim()
    fields = {"job_id": claim.job_id}  # never inspected: discard happens first
    assert queue.cancel(claim.job_id) == "running"
    assert queue.complete(claim.job_id, claim.token, fields) is None
    assert queue.get(claim.job_id).state == "canceled"
    assert env.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
