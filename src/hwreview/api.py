"""HTTP API + teacher review screen (S5). Same contract as the CLI.

Teacher routes are local-only (loopback clients) in this demo; state-changing requests need the
per-process CSRF token. There are no uploads: batches come only from the allowlisted manifest.
"""

import hmac
import re
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict

from hwreview import contracts as c
from hwreview import db
from hwreview.adapters.fake import FakeAdapter
from hwreview.batches import create_batch, load_manifest
from hwreview.errors import ErrorCode, HWError
from hwreview.extraction import load_spans
from hwreview.intake import get_document
from hwreview.jobs import Queue
from hwreview.review import decide, effective_state, export, latest_decision, latest_version
from hwreview.storage import BlobStore
from hwreview.worker import Worker

HERE = Path(__file__).parent
LOOPBACK = frozenset({"127.0.0.1", "::1"})
_RESULT_ID = re.compile(r"^res_[0-9a-f]{32}$")
_JOB_ID = re.compile(r"^job_[0-9a-f]{32}$")
_DOCUMENT_ID = re.compile(r"^doc_[0-9a-f]{32}$")
_ASSIGNMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STATUS = {
    ErrorCode.NOT_FOUND: 404, ErrorCode.INVALID_TRANSITION: 409, ErrorCode.STALE_LEASE: 409,
    ErrorCode.INPUT_REJECTED: 400, ErrorCode.BUDGET_EXCEEDED: 409,
}


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str
    edited_feedback: str


def _check(pattern: re.Pattern, value: str) -> str:
    if not pattern.fullmatch(value):
        raise HWError(ErrorCode.NOT_FOUND, "unknown id")
    return value


def create_app(data_dir: Path, manifest_path: Path | None = None, adapter=None,
               with_worker: bool = False, local_hosts=LOOPBACK,
               reviewer: str = "local-teacher") -> FastAPI:
    data_dir = Path(data_dir)
    db_path = data_dir / "hw.sqlite3"
    adapter = adapter or FakeAdapter()
    db.connect(db_path).close()  # create schema up front

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        thread = None
        if with_worker:
            def run():
                conn = db.connect(db_path)
                try:
                    Worker(conn, BlobStore(data_dir / "blobs"), adapter).run_forever(0.5, stop)
                finally:
                    conn.close()
            thread = threading.Thread(target=run, name="hwreview-worker", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=5)

    app = FastAPI(title="hwreview", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["where"] = _where
    templates.env.globals.update(csrf=app.state.csrf_token, with_worker=with_worker,
                                 provider_label=_provider_label(adapter.config_id))

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.client.host if request.client else ""
        if host not in local_hosts and request.url.path != "/healthz":
            return JSONResponse({"error": {"code": "forbidden",
                                           "message": "teacher review is local-only"}}, 403)
        return await call_next(request)

    @app.exception_handler(HWError)
    async def hw_error(request: Request, e: HWError):
        status = _STATUS.get(e.code, 500)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": e.info().model_dump()}, status)
        return templates.TemplateResponse(request, "error.html", {"error": e}, status_code=status)

    def conn_dep():
        conn = db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def require_csrf(token: str | None) -> None:
        if not token or not hmac.compare_digest(token, app.state.csrf_token):
            raise _Forbidden

    class _Forbidden(Exception):
        pass

    @app.exception_handler(_Forbidden)
    async def forbidden(request: Request, _):
        return JSONResponse({"error": {"code": "forbidden", "message": "missing or invalid CSRF token"}}, 403)

    # --- HTML ----------------------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, conn: sqlite3.Connection = Depends(conn_dep)):
        assignments = []
        for row in conn.execute("SELECT assignment_id, MIN(created_at) AS first FROM batches"
                                " GROUP BY assignment_id ORDER BY first DESC").fetchall():
            entries = roster(conn, row["assignment_id"])
            assignments.append({"id": row["assignment_id"], "entries": entries,
                                "counts": _counts(entries), "first": row["first"]})
        return templates.TemplateResponse(request, "home.html", {
            "assignments": assignments, "can_run_demo": manifest_path is not None})

    @app.post("/demo/batch")
    def demo_batch(csrf: str = Form(""), conn: sqlite3.Connection = Depends(conn_dep)):
        require_csrf(csrf)
        if manifest_path is None:
            raise HWError(ErrorCode.NOT_FOUND, "no demo manifest configured")
        spec, refs, subs = load_manifest(manifest_path)
        create_batch(conn, BlobStore(data_dir / "blobs"), spec, refs, subs, adapter.config_id)
        return RedirectResponse(f"/assignments/{spec.assignment_id}", 303)

    @app.get("/assignments/{assignment_id}", response_class=HTMLResponse)
    def assignment(request: Request, assignment_id: str,
                   conn: sqlite3.Connection = Depends(conn_dep)):
        _check(_ASSIGNMENT, assignment_id)
        entries = roster(conn, assignment_id)
        if not entries:
            raise HWError(ErrorCode.NOT_FOUND, "no student work for this assignment")
        return templates.TemplateResponse(request, "assignment.html", {
            "assignment_id": assignment_id, "entries": entries, "counts": _counts(entries),
            "processing": any(e["status"] in ("queued", "processing") for e in entries)})

    @app.get("/review/{result_id}/{version}", response_class=HTMLResponse)
    def review_page(request: Request, result_id: str, version: int,
                    conn: sqlite3.Connection = Depends(conn_dep)):
        _check(_RESULT_ID, result_id)
        queue = Queue(conn)
        result = queue.get_result(result_id, version)
        job = queue.get(result.job_id)
        spans = load_spans(conn, result.document_id)
        cited: dict[str, list[int]] = {}
        for i, issue in enumerate(result.issues, start=1):
            for sid in issue.evidence_ids:
                cited.setdefault(sid, []).append(i)
        decision = latest_decision(conn, result_id, version)
        entries = roster(conn, _assignment_of(conn, result.document_id))
        me = next((i for i, e in enumerate(entries) if e["result_id"] == result_id), None)
        return templates.TemplateResponse(request, "review.html", {
            "result": result, "job": job, "spans": spans, "cited": cited,
            "decision": decision, "state": effective_state(conn, result_id, version),
            "latest": latest_version(conn, result_id), "entry": entries[me] if me is not None else None,
            "prev": _nav(entries, me, -1), "next": _nav(entries, me, 1),
            "assignment_id": _assignment_of(conn, result.document_id),
            "feedback": decision.edited_feedback if decision else
                        ("" if result.provider == "system" else result.suggested_feedback),
            "rerun_active": conn.execute(
                "SELECT 1 FROM jobs WHERE document_id = ? AND state IN ('queued','running')",
                (result.document_id,)).fetchone() is not None})

    @app.post("/review/{result_id}/{version}/decision")
    def review_decision(result_id: str, version: int, decision: str = Form(...),
                        edited_feedback: str = Form(""), csrf: str = Form(""),
                        conn: sqlite3.Connection = Depends(conn_dep)):
        require_csrf(csrf)
        _check(_RESULT_ID, result_id)
        _decide(conn, result_id, version, decision, edited_feedback)
        return RedirectResponse(f"/review/{result_id}/{version}?saved={decision}", 303)

    @app.post("/jobs/{job_id}/rerun")
    def rerun(job_id: str, csrf: str = Form(""), conn: sqlite3.Connection = Depends(conn_dep)):
        require_csrf(csrf)
        job = Queue(conn).get(_check(_JOB_ID, job_id))
        Queue(conn).rerun(job.job_id)
        return RedirectResponse(f"/assignments/{_assignment_of(conn, job.document_id)}", 303)

    @app.get("/documents/{document_id}/pdf")
    def document_pdf(document_id: str, conn: sqlite3.Connection = Depends(conn_dep)):
        doc = get_document(conn, _check(_DOCUMENT_ID, document_id))
        return Response(BlobStore(data_dir / "blobs").get(doc.storage_key), media_type="application/pdf",
                        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "sandbox"})

    @app.get("/review/{result_id}/{version}/export")
    def export_download(result_id: str, version: int, conn: sqlite3.Connection = Depends(conn_dep)):
        out = export(conn, _check(_RESULT_ID, result_id), version)
        return JSONResponse(out, headers={
            "Content-Disposition": f'attachment; filename="feedback-{result_id}-v{version}.json"'})

    # --- JSON API ------------------------------------------------------------------------
    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str, conn: sqlite3.Connection = Depends(conn_dep)):
        job = Queue(conn).get(_check(_JOB_ID, job_id))
        return {**job.__dict__, "versions": job.versions.model_dump()}

    @app.post("/api/jobs/{job_id}/cancel")
    def api_cancel(job_id: str, request: Request, conn: sqlite3.Connection = Depends(conn_dep)):
        require_csrf(request.headers.get("X-CSRF-Token"))
        return {"job_id": job_id, "state": Queue(conn).cancel(_check(_JOB_ID, job_id))}

    @app.get("/api/results/{result_id}/{version}")
    def api_result(result_id: str, version: int, conn: sqlite3.Connection = Depends(conn_dep)):
        result = Queue(conn).get_result(_check(_RESULT_ID, result_id), version)
        return {**result.model_dump(mode="json"),
                "review_state": effective_state(conn, result_id, version),
                "latest_version": latest_version(conn, result_id)}

    @app.post("/api/results/{result_id}/{version}/decision")
    def api_decision(result_id: str, version: int, body: DecisionIn, request: Request,
                     conn: sqlite3.Connection = Depends(conn_dep)):
        require_csrf(request.headers.get("X-CSRF-Token"))
        d = _decide(conn, _check(_RESULT_ID, result_id), version, body.decision, body.edited_feedback)
        return d.model_dump(mode="json")

    @app.get("/api/results/{result_id}/{version}/export")
    def api_export(result_id: str, version: int, conn: sqlite3.Connection = Depends(conn_dep)):
        return export(conn, _check(_RESULT_ID, result_id), version)

    def _decide(conn, result_id, version, decision, edited_feedback):
        if decision not in ("approved", "rejected"):
            raise HWError(ErrorCode.INPUT_REJECTED, "decision must be approved or rejected")
        return decide(conn, result_id, version, reviewer, decision, edited_feedback.strip())

    return app


# --- view helpers ------------------------------------------------------------------------

_LABELS = {
    "rejected_file": "File not accepted", "queued": "Waiting", "processing": "Drafting",
    "failed": "Draft failed", "canceled": "Canceled", "needs_review": "Needs your review",
    "draft": "Draft ready", "approved": "Approved", "rejected": "Feedback rejected",
}


def _provider_label(config_id: str) -> str:
    if config_id.startswith("fake:"):
        return "the offline test drafter"
    return f"{config_id.split(':', 1)[1]} via OpenRouter" if config_id.startswith("openrouter:") else config_id


def _where(span_id: str) -> str:
    """'doc_x:p1:g2' -> 'page 1, paragraph 3' (paragraphs shown 1-based)."""
    m = re.fullmatch(r"doc_[0-9a-f]{32}:p(\d+):g(\d+)", span_id)
    return f"page {m.group(1)}, paragraph {int(m.group(2)) + 1}" if m else span_id


def _assignment_of(conn: sqlite3.Connection, document_id: str) -> str:
    row = conn.execute("SELECT assignment_id FROM documents WHERE document_id = ?",
                       (document_id,)).fetchone()
    if row is None:
        raise HWError(ErrorCode.NOT_FOUND, "document not found")
    return row["assignment_id"]


def roster(conn: sqlite3.Connection, assignment_id: str) -> list[dict]:
    """One row per student document (latest batch wins); rejected files listed by name."""
    rows = conn.execute(
        "SELECT bi.*, b.created_at AS batch_at FROM batch_items bi JOIN batches b USING (batch_id)"
        " WHERE b.assignment_id = ? ORDER BY b.created_at, bi.idx", (assignment_id,)).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for r in rows:
        latest[r["document_id"] or f"rejected:{r['name']}"] = r
    queue = Queue(conn)
    entries = []
    for r in latest.values():
        name = r["display_name"] or r["name"]
        e = {"name": name, "initial": name[:1].upper(), "file": r["name"], "job_id": r["job_id"],
             "result_id": None, "version": None, "error": None, "issues": 0}
        if r["status"] == "rejected":
            e.update(status="rejected_file", error=r["error_message"])
        else:
            lineage = r["reused_result_id"]
            job = queue.get(r["job_id"]) if r["job_id"] else None
            if job and job.result_id:
                lineage = job.result_id
            active = conn.execute(
                "SELECT state FROM jobs WHERE document_id = ? AND state IN ('queued','running')"
                " ORDER BY rowid DESC LIMIT 1", (r["document_id"],)).fetchone()
            if lineage:
                v = latest_version(conn, lineage)
                res = queue.get_result(lineage, v)
                e.update(result_id=lineage, version=v, issues=len(res.issues),
                         status=effective_state(conn, lineage, v))
            elif job and job.state == "failed":
                e.update(status="failed", error=job.error_code)
            elif job and job.state == "canceled":
                e.update(status="canceled")
            else:
                e.update(status="queued" if job and job.state == "queued" else "processing")
            if active and lineage:
                e["rerunning"] = True
        e["label"] = _LABELS[e["status"]]
        entries.append(e)
    return entries


def _counts(entries: list[dict]) -> dict:
    out = {"total": len(entries), "to_review": 0, "approved": 0, "other": 0}
    for e in entries:
        if e["status"] in ("draft", "needs_review"):
            out["to_review"] += 1
        elif e["status"] == "approved":
            out["approved"] += 1
        else:
            out["other"] += 1
    return out


def _nav(entries: list[dict], me: int | None, step: int) -> dict | None:
    if me is None:
        return None
    i = me + step
    while 0 <= i < len(entries):
        if entries[i]["result_id"]:
            return entries[i]
        i += step
    return None
