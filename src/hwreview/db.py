"""SQLite persistence (single host, WAL). SQLite is the authoritative job queue."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS assignments (
    assignment_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS documents (
    document_id   TEXT PRIMARY KEY,
    role          TEXT NOT NULL CHECK (role IN ('submission', 'reference')),
    assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id),
    sha256        TEXT NOT NULL,
    byte_count    INTEGER NOT NULL,
    page_count    INTEGER NOT NULL,
    storage_key   TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL,
    UNIQUE (assignment_id, role, sha256)
);
CREATE TABLE IF NOT EXISTS spans (
    span_id     TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    page        INTEGER NOT NULL,
    paragraph   INTEGER NOT NULL,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('ok', 'uncertain'))
);
CREATE INDEX IF NOT EXISTS spans_by_doc ON spans(document_id, page, paragraph);
CREATE TABLE IF NOT EXISTS batches (
    batch_id              TEXT PRIMARY KEY,
    assignment_id         TEXT NOT NULL REFERENCES assignments(assignment_id),
    rubric_version        TEXT NOT NULL,
    reference_set_version TEXT NOT NULL,
    prompt_version        TEXT NOT NULL,
    provider_config_id    TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_references (
    batch_id    TEXT NOT NULL REFERENCES batches(batch_id),
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    PRIMARY KEY (batch_id, document_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    batch_id         TEXT NOT NULL REFERENCES batches(batch_id),
    document_id      TEXT NOT NULL REFERENCES documents(document_id),
    version_key      TEXT NOT NULL,
    state            TEXT NOT NULL
                     CHECK (state IN ('queued', 'running', 'completed', 'failed', 'canceled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL,
    lease_token      TEXT,
    lease_expires_at REAL,
    error_code       TEXT,
    error_message    TEXT,
    result_id        TEXT,
    result_version   INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_state ON jobs(state, lease_expires_at);
CREATE INDEX IF NOT EXISTS jobs_by_identity ON jobs(document_id, version_key);
CREATE TABLE IF NOT EXISTS lineages (
    result_id   TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    version_key TEXT NOT NULL,
    UNIQUE (document_id, version_key)
);
CREATE TABLE IF NOT EXISTS results (
    result_id      TEXT NOT NULL REFERENCES lineages(result_id),
    result_version INTEGER NOT NULL,
    job_id         TEXT NOT NULL REFERENCES jobs(job_id),
    body           TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (result_id, result_version)
);
CREATE TABLE IF NOT EXISTS provider_usage (
    id                 INTEGER PRIMARY KEY,
    job_id             TEXT,
    provider_config_id TEXT NOT NULL,
    prompt_tokens      INTEGER,          -- NULL when the provider did not report it
    completion_tokens  INTEGER,
    charged_tokens     INTEGER NOT NULL, -- counted against the cap (reserved bound if unknown)
    outcome            TEXT NOT NULL,
    at                 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_items (
    batch_id         TEXT NOT NULL REFERENCES batches(batch_id),
    idx              INTEGER NOT NULL,
    name             TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')),
    document_id      TEXT,
    job_id           TEXT,
    reused_result_id TEXT,
    error_code       TEXT,
    error_message    TEXT,
    PRIMARY KEY (batch_id, idx)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)  # explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


class transaction:
    """BEGIN IMMEDIATE ... COMMIT/ROLLBACK on an autocommit connection."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
