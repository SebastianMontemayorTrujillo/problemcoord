"""Teacher decisions and student-facing export (docs/contracts.md, TeacherDecision).

Decisions are append-only and tied to one result version. Export requires the version to be the
latest of its lineage AND its latest decision to be `approved`; it contains the edited text only.
"""

import sqlite3

from hwreview import contracts as c
from hwreview.db import transaction
from hwreview.errors import ErrorCode, HWError


def _result_row(conn: sqlite3.Connection, result_id: str, version: int):
    row = conn.execute("SELECT body FROM results WHERE result_id = ? AND result_version = ?",
                       (result_id, version)).fetchone()
    if row is None:
        raise HWError(ErrorCode.NOT_FOUND, "result not found")
    return row


def latest_version(conn: sqlite3.Connection, result_id: str) -> int | None:
    return conn.execute("SELECT MAX(result_version) FROM results WHERE result_id = ?",
                        (result_id,)).fetchone()[0]


def latest_decision(conn: sqlite3.Connection, result_id: str, version: int) -> c.TeacherDecision | None:
    row = conn.execute(
        "SELECT * FROM decisions WHERE result_id = ? AND result_version = ? ORDER BY id DESC LIMIT 1",
        (result_id, version)).fetchone()
    if row is None:
        return None
    return c.TeacherDecision(result_id=row["result_id"], result_version=row["result_version"],
                             reviewer=row["reviewer"], decision=row["decision"],
                             edited_feedback=row["edited_feedback"], decided_at=row["decided_at"])


def effective_state(conn: sqlite3.Connection, result_id: str, version: int) -> str:
    result = c.ReviewResult.model_validate_json(_result_row(conn, result_id, version)["body"])
    decision = latest_decision(conn, result_id, version)
    return decision.decision if decision else result.review_state


def decide(conn: sqlite3.Connection, result_id: str, version: int, reviewer: str,
           decision: str, edited_feedback: str) -> c.TeacherDecision:
    if decision == "approved" and not edited_feedback.strip():
        raise HWError(ErrorCode.INPUT_REJECTED, "write feedback before approving")
    d = c.TeacherDecision(result_id=result_id, result_version=version, reviewer=reviewer,
                          decision=decision, edited_feedback=edited_feedback, decided_at=c.utcnow())
    with transaction(conn):
        _result_row(conn, result_id, version)
        if version != latest_version(conn, result_id):
            raise HWError(ErrorCode.INVALID_TRANSITION, "a newer version exists; review that one")
        conn.execute(
            "INSERT INTO decisions (result_id, result_version, reviewer, decision, edited_feedback,"
            " decided_at) VALUES (?, ?, ?, ?, ?, ?)",
            (d.result_id, d.result_version, d.reviewer, d.decision, d.edited_feedback,
             d.decided_at.isoformat()))
    return d


def export(conn: sqlite3.Connection, result_id: str, version: int) -> dict:
    result = c.ReviewResult.model_validate_json(_result_row(conn, result_id, version)["body"])
    if version != latest_version(conn, result_id):
        raise HWError(ErrorCode.INVALID_TRANSITION, "this version was superseded by a rerun")
    decision = latest_decision(conn, result_id, version)
    if decision is None or decision.decision != "approved":
        raise HWError(ErrorCode.INVALID_TRANSITION, "only teacher-approved feedback can be exported")
    return {"result_id": result_id, "result_version": version, "document_id": result.document_id,
            "feedback": decision.edited_feedback, "approved_by": decision.reviewer,
            "approved_at": decision.decided_at.isoformat()}
