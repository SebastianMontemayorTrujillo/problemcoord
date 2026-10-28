"""Intake: validate bytes before storage, assign server IDs, dedup within assignment+role."""

import hashlib
import io
import re
import sqlite3
from dataclasses import dataclass

from pypdf import PdfReader

from hwreview import contracts as c
from hwreview.config import DEFAULT_LIMITS, Limits
from hwreview.db import transaction
from hwreview.errors import ErrorCode, HWError
from hwreview.storage import BlobStore

_ASSIGNMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ROLES = ("submission", "reference")


@dataclass(frozen=True)
class BatchItem:
    name: str  # display only; never used as a path
    data: bytes
    role: str


@dataclass(frozen=True)
class ItemOutcome:
    name: str
    status: str  # accepted | rejected
    document_id: str | None = None
    reused_document: bool = False
    error: c.ErrorInfo | None = None


def _reject(msg: str) -> HWError:
    return HWError(ErrorCode.INPUT_REJECTED, msg)


def register_assignment(conn: sqlite3.Connection, assignment_id: str) -> None:
    if not isinstance(assignment_id, str) or not _ASSIGNMENT.fullmatch(assignment_id):
        raise _reject("invalid assignment id")
    conn.execute("INSERT OR IGNORE INTO assignments(assignment_id) VALUES (?)", (assignment_id,))


def validate_pdf(data: bytes, limits: Limits = DEFAULT_LIMITS) -> int:
    """Return the page count, or raise input_rejected. Runs before any storage or model call."""
    if not data:
        raise _reject("empty file")
    if len(data) > limits.max_bytes_per_file:
        raise _reject("file exceeds byte limit")
    if not data.startswith(b"%PDF-"):
        raise _reject("not a PDF (bad magic bytes)")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise _reject("encrypted PDFs are not accepted")
        pages = len(reader.pages)
    except HWError:
        raise
    except Exception:
        raise _reject("PDF could not be parsed") from None
    if pages < 1:
        raise _reject("PDF has no pages")
    if pages > limits.max_pages_per_file:
        raise _reject("PDF exceeds page limit")
    return pages


def _row_to_document(row) -> c.Document:
    return c.Document(document_id=row["document_id"], role=row["role"],
                      assignment_id=row["assignment_id"], sha256=row["sha256"],
                      byte_count=row["byte_count"], page_count=row["page_count"],
                      storage_key=row["storage_key"])


def get_document(conn: sqlite3.Connection, document_id: str) -> c.Document:
    row = conn.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
    if row is None:
        raise HWError(ErrorCode.NOT_FOUND, "document not found")
    return _row_to_document(row)


def ingest_document(conn: sqlite3.Connection, store: BlobStore, data: bytes, role: str,
                    assignment_id: str, limits: Limits = DEFAULT_LIMITS) -> tuple[c.Document, bool]:
    """Validate and persist one document. Returns (document, reused_existing)."""
    if role not in ROLES:
        raise _reject("unknown role")
    if not conn.execute("SELECT 1 FROM assignments WHERE assignment_id = ?", (assignment_id,)).fetchone():
        raise _reject("unknown assignment")
    pages = validate_pdf(data, limits)
    sha = hashlib.sha256(data).hexdigest()

    existing = conn.execute(
        "SELECT * FROM documents WHERE assignment_id = ? AND role = ? AND sha256 = ?",
        (assignment_id, role, sha)).fetchone()
    if existing:
        return _row_to_document(existing), True

    key = store.put(data)
    doc = c.Document(document_id=c.new_id("doc"), role=role, assignment_id=assignment_id,
                     sha256=sha, byte_count=len(data), page_count=pages, storage_key=key)
    with transaction(conn):
        conn.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc.document_id, doc.role, doc.assignment_id, doc.sha256, doc.byte_count,
             doc.page_count, doc.storage_key, c.utcnow().isoformat()))
    return doc, False


def ingest_batch(conn: sqlite3.Connection, store: BlobStore, assignment_id: str,
                 items: list[BatchItem], limits: Limits = DEFAULT_LIMITS) -> list[ItemOutcome]:
    """Per-item outcomes; one bad item never stops the others."""
    if len(items) > limits.max_files_per_batch:
        raise _reject("batch exceeds file limit")
    outcomes, pages_used = [], 0
    for item in items:
        try:
            pages = validate_pdf(item.data, limits)
            if pages_used + pages > limits.max_pages_per_batch:
                raise _reject("batch page budget exceeded")
            doc, reused = ingest_document(conn, store, item.data, item.role, assignment_id, limits)
            pages_used += pages
            outcomes.append(ItemOutcome(item.name, "accepted", doc.document_id, reused))
        except HWError as e:
            outcomes.append(ItemOutcome(item.name, "rejected", error=e.info()))
    return outcomes
