# Tasks — slices

Loop per slice: requirement → failing test → implement → narrow test → regression → diff review.
Do not start a slice whose dependency is failing. Stop on contract conflicts.

| Slice | Depends | Allowed paths | Tests | Done when |
|---|---|---|---|---|
| S1 Contracts + fixtures | — | src/hwreview/{contracts,errors,config}.py, fixtures/**, tests/test_t01*, pyproject.toml | T01 | T01 green; generate.py is byte-deterministic across two runs; manifest lists F1–F5 |
| S2 Intake + extraction | S1 | src/hwreview/{storage,intake,extraction,db}.py, tests/test_t02*, tests/test_t05*, tests/conftest.py | T02 T05 | F5 rejected, F1–F4 accepted in one batch; spans carry page/paragraph; F4 uncertain |
| S3 Jobs + fake | S2 | src/hwreview/{jobs,worker,validate,cli}.py, src/hwreview/adapters/{base,fake}.py, tests/test_t03*, tests/test_t04* | T03 T04 | restart/lease/stale/cancel/attempt/dedup tests green; CLI smoke run gives F1–F3 draft, F4 needs_review |
| S4 Hosted adapter | S3 + key + cap | src/hwreview/adapters/openai_compat.py, tests/test_t06*, tests/test_t07* | T06 T07 | mocked contract tests green; live call recorded or explicitly BLOCKED |
| S5 Review + export | S3 | src/hwreview/{review,api}.py, src/hwreview/templates/**, tests/test_t08* | T08 | approval persists across restart; export only latest approved version |
| S6 E2E + security | S4 S5 | tests/test_t09*, tests/test_t10*, README.md | T09 T10 | five-fixture run recorded; security findings triaged |

Current approval: spec pack + S1–S3 (2026-10-28). S4–S6 require a new instruction.
