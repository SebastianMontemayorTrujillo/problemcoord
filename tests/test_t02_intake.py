"""T02 — R02: safe intake. Invalid/oversized PDFs rejected; batch progress preserved."""

import re
from dataclasses import replace

import pytest

from conftest import make_pdf
from hwreview.config import DEFAULT_LIMITS
from hwreview.errors import ErrorCode, HWError
from hwreview.intake import BatchItem, ingest_batch, ingest_document, register_assignment

A = "olympiad-demo-1"


@pytest.fixture
def assigned(env):
    register_assignment(env.conn, A)
    return env


def items(fixture_bytes, ids):
    return [BatchItem(name=f"{i}.pdf", data=fixture_bytes(i), role="submission") for i in ids]


def test_f5_rejected_others_accepted(assigned, fixture_bytes):
    out = ingest_batch(assigned.conn, assigned.store, A, items(fixture_bytes, ["F1", "F5", "F2", "F3", "F4"]))
    assert [o.status for o in out] == ["accepted", "rejected", "accepted", "accepted", "accepted"]
    assert out[1].error.code == "input_rejected" and out[1].document_id is None
    assert all(o.document_id for o in out if o.status == "accepted")


def test_oversized_file_rejected(assigned, fixture_bytes):
    limits = replace(DEFAULT_LIMITS, max_bytes_per_file=100)
    with pytest.raises(HWError) as e:
        ingest_document(assigned.conn, assigned.store, fixture_bytes("F1"), "submission", A, limits)
    assert e.value.code is ErrorCode.INPUT_REJECTED


def test_too_many_pages_rejected(assigned):
    limits = replace(DEFAULT_LIMITS, max_pages_per_file=2)
    data = make_pdf([["page one text"], ["page two text"], ["page three text"]])
    with pytest.raises(HWError) as e:
        ingest_document(assigned.conn, assigned.store, data, "submission", A, limits)
    assert e.value.code is ErrorCode.INPUT_REJECTED


def test_truncated_pdf_rejected(assigned, fixture_bytes):
    data = fixture_bytes("F1")[:200]
    with pytest.raises(HWError) as e:
        ingest_document(assigned.conn, assigned.store, data, "submission", A, DEFAULT_LIMITS)
    assert e.value.code is ErrorCode.INPUT_REJECTED


def test_batch_page_budget_rejects_later_items_only(assigned):
    limits = replace(DEFAULT_LIMITS, max_pages_per_batch=3)
    two = make_pdf([["a" * 30], ["b" * 30]])
    two_b = make_pdf([["c" * 30], ["d" * 30]])
    one = make_pdf([["e" * 30]])
    out = ingest_batch(assigned.conn, assigned.store, A, [
        BatchItem("a.pdf", two, "submission"), BatchItem("b.pdf", two_b, "submission"),
        BatchItem("c.pdf", one, "submission")], limits)
    assert [o.status for o in out] == ["accepted", "rejected", "accepted"]


def test_too_many_files_rejects_whole_batch(assigned, fixture_bytes):
    limits = replace(DEFAULT_LIMITS, max_files_per_batch=2)
    with pytest.raises(HWError) as e:
        ingest_batch(assigned.conn, assigned.store, A, items(fixture_bytes, ["F1", "F2", "F3"]), limits)
    assert e.value.code is ErrorCode.INPUT_REJECTED


def test_unknown_role_or_assignment_rejected(assigned, fixture_bytes):
    with pytest.raises(HWError):
        ingest_document(assigned.conn, assigned.store, fixture_bytes("F1"), "teacher", A, DEFAULT_LIMITS)
    with pytest.raises(HWError):
        ingest_document(assigned.conn, assigned.store, fixture_bytes("F1"), "submission", "unknown-x", DEFAULT_LIMITS)
    with pytest.raises(HWError):
        register_assignment(assigned.conn, "../../etc")


def test_storage_keys_server_generated(assigned, fixture_bytes):
    doc, _ = ingest_document(assigned.conn, assigned.store, fixture_bytes("F1"),
                             "submission", A, DEFAULT_LIMITS)
    assert re.fullmatch(r"[0-9a-f]{64}", doc.storage_key)
    path = assigned.store.path_for(doc.storage_key)
    assert path.is_relative_to(assigned.store.root) and "F1" not in str(path)
    assert assigned.store.get(doc.storage_key) == fixture_bytes("F1")
    for bad in ["../hw.sqlite3", "/etc/passwd", "F1.pdf", ""]:
        with pytest.raises(HWError):
            assigned.store.get(bad)


def test_rejected_file_is_not_stored(assigned, fixture_bytes):
    ingest_batch(assigned.conn, assigned.store, A, items(fixture_bytes, ["F5"]))
    assert not any(p.is_file() for p in assigned.store.root.rglob("*"))
    assert assigned.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
