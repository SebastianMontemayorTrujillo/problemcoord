# Acceptance — requirements ↔ tests

| Req | Requirement | Test | File | Slice | Manual evidence |
|---|---|---|---|---|---|
| R01 | Contracts are versioned and reject invalid/grade fields | T01 | tests/test_t01_contracts.py | S1 | — |
| R02 | Safe intake: invalid/oversized PDFs rejected, batch continues | T02 | tests/test_t02_intake.py | S2 | F5 rejection in CLI output |
| R03 | Durable jobs: commit-before-ack, legal transitions, restart, leases, stale tokens, attempts, cancel | T03 | tests/test_t03_jobs.py | S3 | kill/restart worker demo |
| R04 | Identity/dedup scoped to assignment+role; result reuse needs full VersionKey | T04 | tests/test_t04_dedup.py | S3 | — |
| R05 | Page/paragraph provenance; uncertain extraction → needs_review | T05 | tests/test_t05_provenance.py | S2 | F4 shown as needs_review |
| R06 | Unknown evidence IDs / malformed JSON never yield a valid result | T06 | tests/test_t06_validation.py | S4 | — |
| R07 | Provider timeouts/rate limits typed; retry & spend budget respected | T07 | tests/test_t07_provider.py | S4 | one live synthetic call (if approved) |
| R08 | Approval persists; only exact approved latest version exports; rerun not auto-approved | T08 | tests/test_t08_review.py | S5 | teacher edit+approve+export demo |
| R09 | AuthZ, traversal, no URL fetch, document instructions ignored, redacted logs | T09 | tests/test_t09_security.py | S6 | security review notes |
| R10 | Five-fixture end-to-end workflow; fake/real contract parity | T10 | tests/test_t10_e2e.py | S6 | recorded demo run |

## Fixture expectations (diagnostic, not a benchmark)
| Fixture | Expected outcome | Cannot establish |
|---|---|---|
| F1 alternative proof | reviewable draft; no demand for reference wording | that a real model accepts alternative proofs generally |
| F2 logical gap | draft cites the span containing the gap | detection of subtler gaps |
| F3 missing work | asks for missing reasoning; invents none | — |
| F4 no text layer | `needs_review`, no confident feedback | handwriting recognition |
| F5 invalid bytes | `input_rejected` before any adapter call | — |

Mathematical expectations are UNREVIEWED until a teacher signs off (manifest flag).
Report each T as pass / fail / blocked / not run with command, timestamp and evidence location.

## Evidence (2026-10-28T09:26Z, local, Python 3.14.4, Linux)
Command: `.venv/bin/pytest -q` → **112 passed in 1.26s**, exit 0. All tests are offline; the hosted adapter is
exercised only through a mocked transport.

| Test | Status | Notes |
|---|---|---|
| T01–T05 | pass | contracts, intake, durable jobs, dedup, provenance |
| T06 | pass | invalid JSON, unknown evidence IDs, no self-approval |
| T07 | pass (mocked) | typed 429/5xx/timeout, token cap, unknown usage ≠ 0, fake/hosted parity |
| T07 live | **not run** | needs `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `HWREVIEW_TOKEN_CAP` + approval |
| T08 | pass | approval persists, exact latest version export, rerun not auto-approved |
| T09 | pass | local-only, CSRF, traversal, no uploads/URL fetch, escaping, redacted logs |
| T10 | pass | five fixtures over HTTP with restart: F1–F3 drafts, F4 needs review (no model call), F5 rejected |

Manual: CLI smoke run and HTML review screen exercised on a throwaway data dir (approve → export
→ draft again → v1 export refused). Mutation checks: disabling each guard fails at least one test.
Fixture mathematical expectations remain **unreviewed by a teacher**.
