"""Generate the five synthetic fixtures + teacher reference deterministically.

SYNTHETIC DATA ONLY. No real students. Mathematical expectations are UNREVIEWED until a teacher
signs off (manifest flag `math_expectations_reviewed`).

Usage: python fixtures/generate.py [output_dir]   (default: this script's directory)
"""

import datetime
import hashlib
import json
import sys
from pathlib import Path

from fpdf import FPDF

FIXED_DATE = datetime.datetime(2026, 10, 28, tzinfo=datetime.UTC)
PROBLEM = "Problem 1. Prove that n^2 + n is even for every integer n."

REFERENCE = [
    PROBLEM,
    "Reference solution. Write n^2 + n = n(n + 1). Among the two consecutive integers n and "
    "n + 1 exactly one is even, so their product n(n + 1) is even.",
]

TEXT_FIXTURES = {
    "F1": [
        PROBLEM,
        "Case 1: n is even, so n = 2k for some integer k. Then n^2 + n = 4k^2 + 2k = "
        "2(2k^2 + k), which is even.",
        "Case 2: n is odd, so n = 2k + 1 for some integer k. Then n^2 + n = 4k^2 + 4k + 1 + "
        "2k + 1 = 2(2k^2 + 3k + 1), which is even.",
        "Every integer is even or odd, so n^2 + n is even for every integer n.",
    ],
    "F2": [
        PROBLEM,
        "For n = 1 we get 1 + 1 = 2. For n = 2 we get 4 + 2 = 6. For n = 3 we get 9 + 3 = 12.",
        "All of these are even, so n^2 + n is even for every integer n.",
    ],
    "F3": [
        PROBLEM,
        "Answer: n^2 + n is always even.",
    ],
}

MANIFEST_FIXTURES = [
    {"fixture_id": "F1", "file": "submissions/F1.pdf",
     "intent": "correct alternative proof (parity cases) differing from the reference",
     "expected": "reviewable draft; feedback must not demand the reference's wording",
     "cannot_establish": "that any model accepts alternative proofs in general"},
    {"fixture_id": "F2", "file": "submissions/F2.pdf",
     "intent": "real logical gap: generalizes from n = 1, 2, 3",
     "expected": "draft cites the paragraph that generalizes from examples (page 1, paragraph 2)",
     "cannot_establish": "detection of subtler gaps"},
    {"fixture_id": "F3", "file": "submissions/F3.pdf",
     "intent": "missing work: states the answer only",
     "expected": "asks for the missing reasoning without inventing any",
     "cannot_establish": "behaviour on partially complete work"},
    {"fixture_id": "F4", "file": "submissions/F4.pdf",
     "intent": "ambiguous scan: vector strokes only, no text layer",
     "expected": "needs_review; no confident feedback",
     "cannot_establish": "handwriting recognition (out of scope)"},
    {"fixture_id": "F5", "file": "submissions/F5.pdf",
     "intent": "invalid PDF bytes",
     "expected": "input_rejected before any model call; not counted as a model run",
     "cannot_establish": "robustness against crafted malicious PDFs"},
]


def _pdf() -> FPDF:
    pdf = FPDF()
    pdf.set_creation_date(FIXED_DATE)
    pdf.set_producer("hwreview fixtures")
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    return pdf


def text_pdf(paragraphs: list[str]) -> bytes:
    pdf = _pdf()
    for para in paragraphs:
        pdf.multi_cell(0, 7, para)
        pdf.ln(7)
    return bytes(pdf.output())


def scribble_pdf() -> bytes:
    """Strokes resembling an illegible handwritten scan; deliberately no text layer."""
    pdf = _pdf()
    pdf.set_line_width(0.6)
    for row in range(8):
        y = 30 + row * 14
        x = 20.0
        for i in range(18):
            dx, dy = 8 + (i * 7 + row * 3) % 5, ((i * 5 + row) % 7) - 3
            pdf.line(x, y, x + dx, y + dy)
            x += dx
    return bytes(pdf.output())


def build(out: Path) -> dict:
    (out / "submissions").mkdir(parents=True, exist_ok=True)
    (out / "reference").mkdir(parents=True, exist_ok=True)
    files = {"reference/R1.pdf": text_pdf(REFERENCE)}
    for fid, paras in TEXT_FIXTURES.items():
        files[f"submissions/{fid}.pdf"] = text_pdf(paras)
    files["submissions/F4.pdf"] = scribble_pdf()
    files["submissions/F5.pdf"] = b"This is not a PDF file. It only has a .pdf name.\n"

    hashes = {}
    for rel, data in files.items():
        (out / rel).write_bytes(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()

    manifest = {
        "manifest_version": "1.0",
        "synthetic_only": True,
        "math_expectations_reviewed": False,
        "assignment_id": "olympiad-demo-1",
        "rubric_version": "rubric-demo:v1",
        "reference_set_version": "refset-demo:v1",
        "prompt_version": "prompt:v1",
        "references": [{"file": "reference/R1.pdf", "sha256": hashes["reference/R1.pdf"]}],
        "submissions": [{**f, "sha256": hashes[f["file"]]} for f in MANIFEST_FIXTURES],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    m = build(target)
    for item in m["references"] + m["submissions"]:
        print(item["sha256"], item["file"])
