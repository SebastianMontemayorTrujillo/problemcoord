"""T05 — R05: page/paragraph provenance survives extraction; poor extraction is needs_review."""

from conftest import make_pdf
from hwreview import contracts as c
from hwreview import db
from hwreview.config import DEFAULT_LIMITS
from hwreview.intake import ingest_document, register_assignment
from hwreview.extraction import PypdfExtractor, load_spans, needs_review, save_spans

DID = c.new_id("doc")


def test_f2_paragraph_provenance(fixture_bytes):
    spans = PypdfExtractor().extract(DID, fixture_bytes("F2"))
    assert [(s.page, s.paragraph) for s in spans] == [(1, 0), (1, 1), (1, 2)]
    assert spans[0].text.startswith("Problem 1.")
    assert spans[1].text.startswith("For n = 1")
    assert spans[2].text == "All of these are even, so n^2 + n is even for every integer n."
    assert all(s.status == "ok" for s in spans)
    assert spans[2].span_id == f"{DID}:p1:g2"
    assert not needs_review(spans)


def test_wrapped_lines_joined_within_paragraph(fixture_bytes):
    spans = PypdfExtractor().extract(DID, fixture_bytes("F1"))
    assert len(spans) == 4
    assert "\n" not in spans[1].text and spans[1].text.endswith("which is even.")


def test_extraction_is_deterministic(fixture_bytes):
    ex = PypdfExtractor()
    assert ex.extract(DID, fixture_bytes("F1")) == ex.extract(DID, fixture_bytes("F1"))


def test_no_text_layer_is_uncertain(fixture_bytes):
    spans = PypdfExtractor().extract(DID, fixture_bytes("F4"))
    assert len(spans) == 1
    assert spans[0].status == "uncertain" and spans[0].text == "" and spans[0].page == 1
    assert needs_review(spans)


def test_low_text_page_uncertain_among_good_pages():
    data = make_pdf([["A full paragraph of readable solution text here."], ["x"]])
    spans = PypdfExtractor(DEFAULT_LIMITS).extract(DID, data)
    assert [(s.page, s.status) for s in spans] == [(1, "ok"), (2, "uncertain")]
    assert needs_review(spans)


def test_spans_persist_across_reconnect(env, fixture_bytes):
    register_assignment(env.conn, "A1")
    doc, _ = ingest_document(env.conn, env.store, fixture_bytes("F2"), "submission", "A1")
    spans = PypdfExtractor().extract(doc.document_id, fixture_bytes("F2"))
    save_spans(env.conn, spans)
    env.conn.close()
    conn2 = db.connect(env.db_path)
    try:
        assert load_spans(conn2, doc.document_id) == spans
    finally:
        conn2.close()
