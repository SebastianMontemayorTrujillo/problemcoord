"""T10 — R10: five-fixture workflow end to end over HTTP, with restart in the middle.
Fake adapter, offline. Diagnostic accounting, not an accuracy measure."""

from fastapi.testclient import TestClient

from conftest import CountingAdapter
from hwreview import db
from hwreview.adapters.fake import FakeAdapter
from hwreview.api import create_app, roster
from hwreview.storage import BlobStore
from hwreview.worker import Worker


def test_five_fixture_workflow(env, fixture_dir, clock):
    data = env.root / "data"
    app = create_app(data, manifest_path=fixture_dir / "manifest.json", local_hosts={"testclient"})
    client = TestClient(app)
    r = client.post("/demo/batch", data={"csrf": app.state.csrf_token}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/assignments/olympiad-demo-1"

    # Worker runs as a separate "process" (own connection), then "restarts".
    adapter = CountingAdapter(FakeAdapter())
    conn = db.connect(data / "hw.sqlite3")
    Worker(conn, BlobStore(data / "blobs"), adapter, clock=clock).drain()
    conn.close()
    assert len(adapter.calls) == 3  # F1-F3; F4 gated (no text); F5 rejected before any model use

    entries = {e["file"]: e for e in roster(env.conn, "olympiad-demo-1")}
    assert {f: e["status"] for f, e in entries.items()} == {
        "F1.pdf": "draft", "F2.pdf": "draft", "F3.pdf": "draft",
        "F4.pdf": "needs_review", "F5.pdf": "rejected_file"}
    assert entries["F1.pdf"]["issues"] == 0                       # alternative proof not penalized
    assert entries["F2.pdf"]["issues"] == 1                       # gap cited
    f3 = client.get(f"/api/results/{entries['F3.pdf']['result_id']}/1").json()
    assert f3["issues"][0]["kind"] == "missing_work"              # asks for work, invents none

    # Teacher approves F1-F3 with edits; F4 stays for manual review.
    for f in ("F1.pdf", "F2.pdf", "F3.pdf"):
        e = entries[f]
        r = client.post(f"/api/results/{e['result_id']}/1/decision",
                        headers={"X-CSRF-Token": app.state.csrf_token},
                        json={"decision": "approved", "edited_feedback": f"Teacher note for {f}"})
        assert r.status_code == 200

    # Restart the web app: new process state, same volume.
    app2 = create_app(data, local_hosts={"testclient"})
    client2 = TestClient(app2)
    for f in ("F1.pdf", "F2.pdf", "F3.pdf"):
        out = client2.get(f"/api/results/{entries[f]['result_id']}/1/export").json()
        assert out["feedback"] == f"Teacher note for {f}"
    assert client2.get(f"/api/results/{entries['F4.pdf']['result_id']}/1/export").status_code == 409
    page = client2.get("/assignments/olympiad-demo-1").text
    assert page.count("chip--approved") == 3 and "chip--needs_review" in page
