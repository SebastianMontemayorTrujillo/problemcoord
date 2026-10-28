import importlib.util
import io
import json
from pathlib import Path

import pytest
from fpdf import FPDF

from hwreview import db
from hwreview.storage import BlobStore

ROOT = Path(__file__).resolve().parent.parent


def _load_generator():
    spec = importlib.util.spec_from_file_location("fixture_generate", ROOT / "fixtures" / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixtures")
    _load_generator().build(out)
    return out


@pytest.fixture(scope="session")
def manifest(fixture_dir):
    return json.loads((fixture_dir / "manifest.json").read_text())


@pytest.fixture
def fixture_bytes(fixture_dir):
    return lambda fid: (fixture_dir / "submissions" / f"{fid}.pdf").read_bytes()


@pytest.fixture
def env(tmp_path):
    """Fresh data dir: DB path, connection, blob store."""
    db_path = tmp_path / "data" / "hw.sqlite3"
    conn = db.connect(db_path)
    store = BlobStore(tmp_path / "data" / "blobs")
    yield type("Env", (), {"db_path": db_path, "conn": conn, "store": store, "root": tmp_path})
    conn.close()


def make_pdf(pages: list[list[str]]) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    for paras in pages:
        pdf.add_page()
        for p in paras:
            pdf.multi_cell(0, 7, p)
            pdf.ln(7)
    buf = io.BytesIO()
    buf.write(bytes(pdf.output()))
    return buf.getvalue()


class Clock:
    """Controllable clock for lease tests."""

    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock():
    return Clock()


class CountingAdapter:
    """Wraps an adapter, records calls; optional hook runs inside review()."""

    def __init__(self, inner, hook=None):
        self.inner, self.hook, self.calls = inner, hook, []
        self.config_id = inner.config_id

    def review(self, request):
        self.calls.append(request)
        if self.hook:
            out = self.hook(request)
            if out is not None:
                return out
        return self.inner.review(request)


def load_fixture_batch(fixture_dir, ids=("F1", "F2", "F3", "F4", "F5"), **overrides):
    from hwreview.batches import load_manifest
    spec, refs, subs = load_manifest(fixture_dir / "manifest.json")
    if overrides:
        from dataclasses import replace
        spec = replace(spec, **overrides)
    subs = [s for s in subs if s.name.split(".")[0] in ids]
    return spec, refs, subs
