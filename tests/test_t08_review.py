"""T08 — R08: approval persists; only the exact approved latest version exports;
reruns are never auto-approved. Library and HTTP boundaries."""

import pytest
from fastapi.testclient import TestClient

from conftest import load_fixture_batch
from hwreview import db
from hwreview.adapters.fake import FakeAdapter
from hwreview.api import create_app
from hwreview.batches import create_batch
from hwreview.errors import ErrorCode, HWError
from hwreview.jobs import Queue
from hwreview.review import decide, effective_state, export, latest_decision
from hwreview.worker import Worker


def completed(env, fixture_dir, clock, fid="F2"):
    spec, refs, subs = load_fixture_batch(fixture_dir, (fid,))
    batch = create_batch(env.conn, env.store, spec, refs, subs, "fake:v1")
    Worker(env.conn, env.store, FakeAdapter(), clock=clock).drain()
    return Queue(env.conn).get(batch.items[0].job_id)


def test_export_requires_approval(env, fixture_dir, clock):
    job = completed(env, fixture_dir, clock)
    with pytest.raises(HWError) as e:
        export(env.conn, job.result_id, 1)
    assert e.value.code is ErrorCode.INVALID_TRANSITION
    assert effective_state(env.conn, job.result_id, 1) == "draft"


def test_approval_persists_and_exports_edited_text_only(env, fixture_dir, clock):
    job = completed(env, fixture_dir, clock)
    decide(env.conn, job.result_id, 1, "teacher-1", "approved", "Good start; prove it for all n.")
    env.conn.close()
    conn = db.connect(env.db_path)
    try:
        assert effective_state(conn, job.result_id, 1) == "approved"
        out = export(conn, job.result_id, 1)
        assert out["feedback"] == "Good start; prove it for all n."
        assert out["result_version"] == 1 and out["approved_by"] == "teacher-1"
        assert set(out) == {"result_id", "result_version", "document_id", "feedback",
                            "approved_by", "approved_at"}
    finally:
        conn.close()
    env.conn = db.connect(env.db_path)


def test_latest_decision_wins(env, fixture_dir, clock):
    job = completed(env, fixture_dir, clock)
    decide(env.conn, job.result_id, 1, "t", "approved", "ok")
    decide(env.conn, job.result_id, 1, "t", "rejected", "")
    assert latest_decision(env.conn, job.result_id, 1).decision == "rejected"
    with pytest.raises(HWError):
        export(env.conn, job.result_id, 1)


def test_rerun_invalidates_prior_approval(env, fixture_dir, clock):
    job = completed(env, fixture_dir, clock)
    decide(env.conn, job.result_id, 1, "t", "approved", "v1 text")
    queue = Queue(env.conn, clock=clock)
    queue.rerun(job.job_id)
    Worker(env.conn, env.store, FakeAdapter(), clock=clock).drain()
    assert effective_state(env.conn, job.result_id, 2) == "draft"  # not inherited
    for version in (1, 2):
        with pytest.raises(HWError) as e:
            export(env.conn, job.result_id, version)
        assert e.value.code is ErrorCode.INVALID_TRANSITION
    with pytest.raises(HWError):
        decide(env.conn, job.result_id, 1, "t", "approved", "stale")  # superseded version
    decide(env.conn, job.result_id, 2, "t", "approved", "v2 text")
    assert export(env.conn, job.result_id, 2)["feedback"] == "v2 text"


def test_unknown_result(env):
    with pytest.raises(HWError) as e:
        decide(env.conn, "res_" + "0" * 32, 1, "t", "approved", "x")
    assert e.value.code is ErrorCode.NOT_FOUND


# --- HTTP ------------------------------------------------------------------------------------

@pytest.fixture
def app_env(env, fixture_dir, clock):
    job = completed(env, fixture_dir, clock)
    app = create_app(env.root / "data", manifest_path=fixture_dir / "manifest.json",
                     local_hosts={"testclient"})
    return job, app


def csrf(app):
    return app.state.csrf_token


def test_http_decision_and_export_flow(app_env):
    job, app = app_env
    client = TestClient(app)
    url = f"/api/results/{job.result_id}/1"
    assert client.get(url).json()["review_state"] == "draft"
    assert client.get(url + "/export").status_code == 409
    r = client.post(url + "/decision", headers={"X-CSRF-Token": csrf(app)},
                    json={"decision": "approved", "edited_feedback": "Edited by teacher."})
    assert r.status_code == 200 and r.json()["decision"] == "approved"
    r = client.get(url + "/export")
    assert r.status_code == 200 and r.json()["feedback"] == "Edited by teacher."


def test_http_form_approval_from_review_screen(app_env):
    job, app = app_env
    client = TestClient(app)
    page = client.get(f"/review/{job.result_id}/1")
    assert page.status_code == 200 and "Approve" in page.text and "synthetic" in page.text.lower()
    r = client.post(f"/review/{job.result_id}/1/decision", follow_redirects=False,
                    data={"decision": "approved", "edited_feedback": "From the form", "csrf": csrf(app)})
    assert r.status_code == 303
    assert client.get(f"/api/results/{job.result_id}/1/export").json()["feedback"] == "From the form"


def test_http_requires_csrf(app_env):
    job, app = app_env
    client = TestClient(app)
    r = client.post(f"/api/results/{job.result_id}/1/decision",
                    json={"decision": "approved", "edited_feedback": "x"})
    assert r.status_code == 403
    r = client.post(f"/review/{job.result_id}/1/decision",
                    data={"decision": "approved", "edited_feedback": "x", "csrf": "wrong"})
    assert r.status_code == 403


def test_http_teacher_routes_local_only(env, fixture_dir, clock):
    job = completed(env, fixture_dir, clock)
    app = create_app(env.root / "data", manifest_path=fixture_dir / "manifest.json", local_hosts=set())
    client = TestClient(app)
    for path in ("/", f"/review/{job.result_id}/1", f"/api/results/{job.result_id}/1"):
        assert client.get(path).status_code == 403


def test_http_unknown_ids_are_404(app_env):
    _, app = app_env
    client = TestClient(app)
    assert client.get("/api/results/res_" + "0" * 32 + "/1").status_code == 404
    assert client.get("/api/results/not-an-id/1").status_code == 404
    assert client.get("/api/jobs/job_" + "0" * 32).status_code == 404


def test_cannot_approve_empty_feedback(env, fixture_dir, clock):
    job = completed(env, fixture_dir, clock)
    with pytest.raises(HWError) as e:
        decide(env.conn, job.result_id, 1, "t", "approved", "   ")
    assert e.value.code is ErrorCode.INPUT_REJECTED
    decide(env.conn, job.result_id, 1, "t", "rejected", "")  # rejecting needs no text


def test_unreadable_submission_has_no_prefilled_feedback(env, fixture_dir, clock):
    job = completed(env, fixture_dir, clock, fid="F4")
    app = create_app(env.root / "data", local_hosts={"testclient"})
    page = TestClient(app).get(f"/review/{job.result_id}/1").text
    assert "No readable text on this page" in page
    assert "No readable text was found in this submission" not in page.split('id="feedback"')[1].split("</textarea>")[0]
