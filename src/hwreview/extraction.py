"""Local text-layer extraction. No OCR: pages without enough text become `uncertain`."""

import io
import re
import sqlite3
from typing import Protocol

from pypdf import PdfReader

from hwreview import contracts as c
from hwreview.config import DEFAULT_LIMITS, EXTRACTION_VERSION, Limits

_PARA_BREAK = re.compile(r"\n\s*\n")
_WS = re.compile(r"\s+")


class Extractor(Protocol):
    version: str

    def extract(self, document_id: str, data: bytes) -> list[c.EvidenceSpan]: ...


class PypdfExtractor:
    version = EXTRACTION_VERSION

    def __init__(self, limits: Limits = DEFAULT_LIMITS):
        self.limits = limits

    def extract(self, document_id: str, data: bytes) -> list[c.EvidenceSpan]:
        reader = PdfReader(io.BytesIO(data), strict=False)
        spans: list[c.EvidenceSpan] = []
        for page_no, page in enumerate(reader.pages, start=1):
            try:
                raw = page.extract_text(extraction_mode="layout") or ""
            except Exception:
                raw = ""
            paras = [_WS.sub(" ", p).strip() for p in _PARA_BREAK.split(raw)]
            paras = [p for p in paras if p]
            if sum(len(p) for p in paras) < self.limits.min_chars_per_page:
                spans.append(_span(document_id, page_no, 0, "", "uncertain"))
                continue
            spans.extend(_span(document_id, page_no, i, p, "ok") for i, p in enumerate(paras))
        return spans


def _span(document_id: str, page: int, paragraph: int, text: str, status: str) -> c.EvidenceSpan:
    return c.EvidenceSpan(span_id=c.span_id(document_id, page, paragraph), document_id=document_id,
                          page=page, paragraph=paragraph, text=text, status=status)


def needs_review(spans: list[c.EvidenceSpan]) -> bool:
    return not spans or any(s.status == "uncertain" for s in spans)


def save_spans(conn: sqlite3.Connection, spans: list[c.EvidenceSpan]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO spans VALUES (?, ?, ?, ?, ?, ?)",
        [(s.span_id, s.document_id, s.page, s.paragraph, s.text, s.status) for s in spans])


def load_spans(conn: sqlite3.Connection, document_id: str) -> list[c.EvidenceSpan]:
    rows = conn.execute(
        "SELECT * FROM spans WHERE document_id = ? ORDER BY page, paragraph", (document_id,))
    return [c.EvidenceSpan(span_id=r["span_id"], document_id=r["document_id"], page=r["page"],
                           paragraph=r["paragraph"], text=r["text"], status=r["status"]) for r in rows]
