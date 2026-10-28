"""T04 — R04: content dedup scoped to assignment+role; result reuse needs the full VersionKey."""

import pytest

from conftest import load_fixture_batch
from hwreview.adapters.fake import FakeAdapter
from hwreview.batches import create_batch
from hwreview.intake import BatchItem, ingest_document, register_assignment
from hwreview.jobs import Queue
from hwreview.worker import Worker


def run(env, fixture_dir, clock, provider="fake:v1", ids=("F1",), drain=True, **overrides):
    spec, refs, subs = load_fixture_batch(fixture_dir, ids, **overrides)
    batch = create_batch(env.conn, env.store, spec, refs, subs, provider)
    if drain:
        Worker(env.conn, env.store, FakeAdapter(), clock=clock).drain()
    return batch.items[0]


def test_identical_bytes_same_scope_reuse_document(env, fixture_bytes):
    register_assignment(env.conn, "A1")
    register_assignment(env.conn, "A2")
    d1, r1 = ingest_document(env.conn, env.store, fixture_bytes("F1"), "submission", "A1")
    d2, r2 = ingest_document(env.conn, env.store, fixture_bytes("F1"), "submission", "A1")
    assert d1.document_id == d2.document_id and (r1, r2) == (False, True)
    d3, _ = ingest_document(env.conn, env.store, fixture_bytes("F1"), "reference", "A1")
    d4, _ = ingest_document(env.conn, env.store, fixture_bytes("F1"), "submission", "A2")
    assert len({d1.document_id, d3.document_id, d4.document_id}) == 3


def test_same_versions_reuse_completed_result(env, fixture_dir, clock):
    first = run(env, fixture_dir, clock)
    second = run(env, fixture_dir, clock, drain=False)
    assert second.job_id is None
    job = Queue(env.conn).get(first.job_id)
    assert second.reused_result_id == job.result_id
    assert env.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_in_flight_job_is_shared(env, fixture_dir, clock):
    a = run(env, fixture_dir, clock, drain=False)
    b = run(env, fixture_dir, clock, drain=False)
    assert a.job_id == b.job_id


@pytest.mark.parametrize("change", [
    dict(prompt_version="prompt:v2"), dict(rubric_version="rubric-demo:v2"),
    dict(reference_set_version="refset-demo:v2"), dict(provider="openrouter:some-model"),
])
def test_changed_version_does_not_reuse(env, fixture_dir, clock, change):
    first = run(env, fixture_dir, clock)
    provider = change.pop("provider", "fake:v1")
    second = run(env, fixture_dir, clock, provider=provider, drain=False, **change)
    assert second.job_id and second.job_id != first.job_id and second.reused_result_id is None
    assert second.document_id == first.document_id


def test_changed_role_or_assignment_does_not_reuse(env, fixture_dir, clock):
    first = run(env, fixture_dir, clock)
    second = run(env, fixture_dir, clock, drain=False, assignment_id="olympiad-demo-2")
    assert second.document_id != first.document_id and second.reused_result_id is None


def test_rerun_creates_new_unapproved_version(env, fixture_dir, clock):
    first = run(env, fixture_dir, clock)
    queue = Queue(env.conn, clock=clock)
    v1 = queue.get(first.job_id)
    new_job = queue.rerun(first.job_id)
    Worker(env.conn, env.store, FakeAdapter(), clock=clock).drain()
    v2 = queue.get(new_job)
    assert v2.result_id == v1.result_id and (v1.result_version, v2.result_version) == (1, 2)
    r1 = queue.get_result(v1.result_id, 1)
    r2 = queue.get_result(v2.result_id, 2)
    assert r1.suggested_feedback == r2.suggested_feedback  # v1 row untouched
    assert r2.review_state in ("draft", "needs_review")


def test_hash_alone_is_not_identity(env, fixture_dir, clock):
    """Same bytes submitted under another name still map to the same document, not to a path."""
    spec, refs, subs = load_fixture_batch(fixture_dir, ("F1",))
    renamed = [BatchItem("totally-different.pdf", subs[0].data, "submission")]
    a = create_batch(env.conn, env.store, spec, refs, subs, "fake:v1").items[0]
    b = create_batch(env.conn, env.store, spec, refs, renamed, "fake:v1").items[0]
    assert a.document_id == b.document_id and a.job_id == b.job_id
