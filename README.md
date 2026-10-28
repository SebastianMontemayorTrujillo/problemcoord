# hwreview — teacher-reviewed draft feedback for math homework PDFs

> **Synthetic data only.** This hackathon component must not process real students' work.
> It drafts feedback for a teacher to edit and approve. It does **not** grade, verify proofs,
> or message students.

Built for a civic-education nonprofit's free math-Olympiad program (MLH Hacktoberfest Hack Day,
CDMX, 28 Oct 2026). Specs: `docs/`. Assistant guidance: `CLAUDE.md`.

## Status
| Slice | State |
|---|---|
| S1 contracts + fixtures (T01) | implemented, tested |
| S2 intake + extraction (T02, T05) | implemented, tested |
| S3 durable jobs + fake adapter (T03, T04) | implemented, tested |
| S4 OpenRouter adapter (T06, T07) | implemented, mocked tests; live call not run (needs key + cap) |
| S5 teacher review + export (T08) | implemented, tested; Classroom-style review screen |
| S6 end-to-end + security (T09, T10) | implemented, tested (offline); deployment not started |

## Quick start (offline, fake adapter)
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python fixtures/generate.py          # deterministic synthetic PDFs + manifest
.venv/bin/pytest -q
.venv/bin/hwreview --data-dir /tmp/hwdemo batch create fixtures/manifest.json
.venv/bin/hwreview --data-dir /tmp/hwdemo worker run --once
.venv/bin/hwreview --data-dir /tmp/hwdemo batch show <batch_id>
.venv/bin/hwreview --data-dir /tmp/hwdemo result show <result_id> --with-evidence
```

## Teacher review screen
```bash
.venv/bin/hwreview --data-dir /tmp/hwdemo serve --with-worker   # http://127.0.0.1:8000
```
Click **Load demo submissions**, open a student, edit the feedback, **Approve feedback**, then
**Download approved feedback**. The screen is local-only (loopback clients).

## Live provider (optional, gated)
Set these yourself in the shell (never commit them): `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
(a current `:free` model id from openrouter.ai/models) and `HWREVIEW_TOKEN_CAP` (total tokens).
Then pass `--adapter openrouter` to `batch create` and `worker run`. Free endpoints may log
prompts: synthetic data only.

## Known limits
Text-layer PDFs only (no OCR); scans become `needs_review`. Single host, single worker, SQLite.
Limits in `src/hwreview/config.py` are proposed, not approved. Fixture math expectations are
unreviewed by a teacher. License: not yet chosen.
