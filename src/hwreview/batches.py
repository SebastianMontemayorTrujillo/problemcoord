"""Batch creation from an approved manifest: intake, dedup/reuse, durable enqueue."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hwreview import contracts as c
from hwreview.config import DEFAULT_LIMITS, EXTRACTION_VERSION, Limits
from hwreview.db import transaction
from hwreview.errors import ErrorCode, HWError
from hwreview.intake import BatchItem, ingest_batch, ingest_document, register_assignment
from hwreview.jobs import Queue
from hwreview.storage import BlobStore


@dataclass(frozen=True)
class BatchSpec:
    assignment_id: str
    rubric_version: str
    reference_set_version: str
    prompt_version: str


@dataclass(frozen=True)
class BatchItemView:
    name: str
    status: str  # accepted | rejected
    document_id: str | None = None
    job_id: str | None = None
    reused_result_id: str | None = None
    error: c.ErrorInfo | None = None


@dataclass(frozen=True)
class BatchView:
    batch_id: str
    items: list[BatchItemView]


def _manifest_file(base: Path, rel: str) -> Path:
    path = (base / rel).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HWError(ErrorCode.INPUT_REJECTED, "manifest file path is outside the manifest folder")
    return path


def load_manifest(path: Path) -> tuple[BatchSpec, list[BatchItem], list[BatchItem]]:
    """Read an approved manifest. Only synthetic-only manifests are accepted in this demo."""
    path = Path(path).resolve()
    m = json.loads(path.read_text())
    if m.get("synthetic_only") is not True:
        raise HWError(ErrorCode.INPUT_REJECTED, "only synthetic_only manifests are accepted")
    base = path.parent
    spec = BatchSpec(m["assignment_id"], m["rubric_version"], m["reference_set_version"],
                     m["prompt_version"])

    def item(entry, role):
        f = _manifest_file(base, entry["file"])
        return BatchItem(name=f.name, data=f.read_bytes(), role=role)

    return (spec, [item(r, "reference") for r in m.get("references", [])],
            [item(s, "submission") for s in m["submissions"]])


def create_batch(conn: sqlite3.Connection, store: BlobStore, spec: BatchSpec,
                 references: list[BatchItem], submissions: list[BatchItem],
                 provider_config_id: str, limits: Limits = DEFAULT_LIMITS,
                 extraction_version: str = EXTRACTION_VERSION) -> BatchView:
    register_assignment(conn, spec.assignment_id)
    ref_docs = [ingest_document(conn, store, r.data, "reference", spec.assignment_id, limits)[0]
                for r in references]
    batch_id = c.new_id("bat")
    with transaction(conn):
        conn.execute("INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (batch_id, spec.assignment_id, spec.rubric_version, spec.reference_set_version,
                      spec.prompt_version, provider_config_id, c.utcnow().isoformat()))
        conn.executemany("INSERT OR IGNORE INTO batch_references VALUES (?, ?)",
                         [(batch_id, d.document_id) for d in ref_docs])

    versions = c.VersionKey(rubric_version=spec.rubric_version,
                            reference_set_version=spec.reference_set_version,
                            prompt_version=spec.prompt_version,
                            extraction_version=extraction_version,
                            provider_config_id=provider_config_id)
    queue = Queue(conn, limits)
    views = []
    for outcome in ingest_batch(conn, store, spec.assignment_id, submissions, limits):
        if outcome.status != "accepted":
            views.append(BatchItemView(outcome.name, "rejected", error=outcome.error))
            continue
        reuse = queue.find_reusable(outcome.document_id, versions)
        if reuse and reuse[0] == "result":
            views.append(BatchItemView(outcome.name, "accepted", outcome.document_id,
                                       reused_result_id=reuse[1]))
            continue
        job_id = reuse[1] if reuse else queue.enqueue(batch_id, outcome.document_id, versions)
        views.append(BatchItemView(outcome.name, "accepted", outcome.document_id, job_id=job_id))

    with transaction(conn):
        conn.executemany(
            "INSERT INTO batch_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(batch_id, i, v.name, v.status, v.document_id, v.job_id, v.reused_result_id,
              v.error and v.error.code, v.error and v.error.message) for i, v in enumerate(views)])
    return BatchView(batch_id, views)
