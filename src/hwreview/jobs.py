"""Durable SQLite job queue with lease tokens (docs/contracts.md, processing state machine).

Attempts are at-least-once: a timed-out provider call may still have run (and cost money).
"""

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from hwreview import contracts as c
from hwreview.config import DEFAULT_LIMITS, Limits
from hwreview.db import transaction
from hwreview.errors import ErrorCode, HWError


def version_key_json(versions: c.VersionKey) -> str:
    return json.dumps(versions.model_dump(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Claim:
    job_id: str
    token: str
    attempt: int


@dataclass(frozen=True)
class Job:
    job_id: str
    batch_id: str
    document_id: str
    versions: c.VersionKey
    state: str
    cancel_requested: bool
    attempts: int
    max_attempts: int
    error_code: str | None
    error_message: str | None
    result_id: str | None
    result_version: int | None


def _job(row) -> Job:
    return Job(job_id=row["job_id"], batch_id=row["batch_id"], document_id=row["document_id"],
               versions=c.VersionKey.model_validate_json(row["version_key"]), state=row["state"],
               cancel_requested=bool(row["cancel_requested"]), attempts=row["attempts"],
               max_attempts=row["max_attempts"], error_code=row["error_code"],
               error_message=row["error_message"], result_id=row["result_id"],
               result_version=row["result_version"])


class Queue:
    def __init__(self, conn: sqlite3.Connection, limits: Limits = DEFAULT_LIMITS,
                 clock: Callable[[], float] = time.time):
        self.conn, self.limits, self.clock = conn, limits, clock

    # --- producer side -------------------------------------------------------------------
    def enqueue(self, batch_id: str, document_id: str, versions: c.VersionKey) -> str:
        """Insert and COMMIT a queued job before returning its ID."""
        job_id, now = c.new_id("job"), c.utcnow().isoformat()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO jobs (job_id, batch_id, document_id, version_key, state, max_attempts,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
                (job_id, batch_id, document_id, version_key_json(versions),
                 self.limits.max_attempts, now, now))
        return job_id

    def find_reusable(self, document_id: str, versions: c.VersionKey) -> tuple[str, str] | None:
        """('result', result_id) for a completed lineage, ('job', job_id) for in-flight work."""
        vk = version_key_json(versions)
        row = self.conn.execute(
            "SELECT result_id FROM lineages WHERE document_id = ? AND version_key = ?",
            (document_id, vk)).fetchone()
        if row:
            return "result", row["result_id"]
        row = self.conn.execute(
            "SELECT job_id FROM jobs WHERE document_id = ? AND version_key = ?"
            " AND state IN ('queued', 'running') AND cancel_requested = 0 ORDER BY rowid LIMIT 1",
            (document_id, vk)).fetchone()
        return ("job", row["job_id"]) if row else None

    def rerun(self, job_id: str) -> str:
        """New job for the same document and versions. Its result is a new, unapproved version."""
        job = self.get(job_id)
        if job.state not in c.TERMINAL_STATES:
            raise HWError(ErrorCode.INVALID_TRANSITION, "job is still active")
        return self.enqueue(job.batch_id, job.document_id, job.versions)

    def cancel(self, job_id: str) -> str:
        with transaction(self.conn):
            job = self._row(job_id)
            if job["state"] == "queued":
                self._set(job_id, state="canceled", lease_token=None)
                return "canceled"
            if job["state"] == "running":
                self._set(job_id, cancel_requested=1)
                return "running"
        raise HWError(ErrorCode.INVALID_TRANSITION, f"cannot cancel a {job['state']} job")

    # --- worker side ---------------------------------------------------------------------
    def claim(self) -> Claim | None:
        """Atomically lease the next job. The transaction ends before any slow work."""
        now, token = self.clock(), secrets.token_hex(16)
        with transaction(self.conn):
            expired = "state = 'running' AND lease_expires_at <= ?"
            self.conn.execute(
                f"UPDATE jobs SET state = 'canceled', lease_token = NULL, updated_at = ?"
                f" WHERE {expired} AND cancel_requested = 1", (c.utcnow().isoformat(), now))
            self.conn.execute(
                f"UPDATE jobs SET state = 'failed', lease_token = NULL, error_code = 'timeout',"
                f" error_message = 'lease expired; attempt budget exhausted', updated_at = ?"
                f" WHERE {expired} AND attempts >= max_attempts", (c.utcnow().isoformat(), now))
            row = self.conn.execute(
                "UPDATE jobs SET state = 'running', attempts = attempts + 1, lease_token = ?,"
                " lease_expires_at = ?, updated_at = ? WHERE job_id = ("
                f"  SELECT job_id FROM jobs WHERE state = 'queued' OR ({expired})"
                "  ORDER BY rowid LIMIT 1) RETURNING job_id, attempts",
                (token, now + self.limits.lease_s, c.utcnow().isoformat(), now)).fetchone()
        return Claim(row["job_id"], token, row["attempts"]) if row else None

    def heartbeat(self, job_id: str, token: str) -> bool:
        """Extend the lease. Returns True when cancellation was requested."""
        with transaction(self.conn):
            row = self._owned(job_id, token)
            self._set(job_id, lease_expires_at=self.clock() + self.limits.lease_s)
            return bool(row["cancel_requested"])

    def mark_canceled(self, job_id: str, token: str) -> None:
        with transaction(self.conn):
            self._owned(job_id, token)
            self._set(job_id, state="canceled")

    def fail(self, job_id: str, token: str, err: HWError) -> str:
        with transaction(self.conn):
            row = self._owned(job_id, token)
            state = "queued" if err.retryable and row["attempts"] < row["max_attempts"] else "failed"
            fields = dict(state=state, error_code=err.code.value, error_message=err.message[:500])
            if state == "queued":
                fields.update(lease_token=None, lease_expires_at=None)
            self._set(job_id, **fields)
            return state

    def complete(self, job_id: str, token: str, fields: dict) -> c.ReviewResult | None:
        """Persist an immutable result version. Returns None if cancellation discarded it."""
        with transaction(self.conn):
            row = self._owned(job_id, token)
            if row["cancel_requested"]:
                self._set(job_id, state="canceled")
                return None
            vk = row["version_key"]
            lineage = self.conn.execute(
                "SELECT result_id FROM lineages WHERE document_id = ? AND version_key = ?",
                (row["document_id"], vk)).fetchone()
            if lineage:
                result_id = lineage["result_id"]
            else:
                result_id = c.new_id("res")
                self.conn.execute("INSERT INTO lineages VALUES (?, ?, ?)",
                                  (result_id, row["document_id"], vk))
            version = self.conn.execute(
                "SELECT COALESCE(MAX(result_version), 0) + 1 FROM results WHERE result_id = ?",
                (result_id,)).fetchone()[0]
            result = c.ReviewResult(result_id=result_id, result_version=version,
                                    created_at=c.utcnow(), **fields)
            if result.job_id != job_id or result.document_id != row["document_id"]:
                raise HWError(ErrorCode.INVALID_TRANSITION, "result does not belong to this job")
            self.conn.execute("INSERT INTO results VALUES (?, ?, ?, ?, ?)",
                              (result_id, version, job_id, result.model_dump_json(),
                               result.created_at.isoformat()))
            self._set(job_id, state="completed", result_id=result_id, result_version=version,
                      error_code=None, error_message=None)
            return result

    # --- reads ---------------------------------------------------------------------------
    def get(self, job_id: str) -> Job:
        return _job(self._row(job_id))

    def get_result(self, result_id: str, version: int | None = None) -> c.ReviewResult:
        if version is None:
            row = self.conn.execute(
                "SELECT body FROM results WHERE result_id = ? ORDER BY result_version DESC LIMIT 1",
                (result_id,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT body FROM results WHERE result_id = ? AND result_version = ?",
                (result_id, version)).fetchone()
        if row is None:
            raise HWError(ErrorCode.NOT_FOUND, "result not found")
        return c.ReviewResult.model_validate_json(row["body"])

    # --- internals -----------------------------------------------------------------------
    def _row(self, job_id: str):
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise HWError(ErrorCode.NOT_FOUND, "job not found")
        return row

    def _owned(self, job_id: str, token: str):
        row = self._row(job_id)
        if not token or row["lease_token"] != token:
            raise HWError(ErrorCode.STALE_LEASE, "lease token does not match")
        if row["state"] != "running":
            raise HWError(ErrorCode.INVALID_TRANSITION, f"job is {row['state']}")
        return row

    def _set(self, job_id: str, **fields) -> None:
        fields["updated_at"] = c.utcnow().isoformat()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {cols} WHERE job_id = ?", (*fields.values(), job_id))
