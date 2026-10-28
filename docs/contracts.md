# Contracts (schema_version `1.0`)

Implemented in `src/hwreview/contracts.py` (Pydantic v2, `extra="forbid"`, frozen).
All IDs are server-generated: `<prefix>_<32 hex>` (uuid4). Prefixes: `doc`, `job`, `bat`, `res`.
Timestamps are UTC ISO-8601. No schema contains a grade, score, or proof-verification field;
such fields are rejected as unknown.

## Document
| Field | Type | Rule |
|---|---|---|
| schema_version | `"1.0"` | |
| document_id | `doc_…` | server-generated |
| role | `submission` \| `reference` | from manifest, never from content |
| assignment_id | str | `[A-Za-z0-9_-]{1,64}` |
| sha256 | 64 lowercase hex | of exact bytes |
| byte_count | int ≥ 1 | |
| page_count | int ≥ 1 | |
| storage_key | 64 hex | server-generated; never a client path |

## EvidenceSpan
`span_id` = `{document_id}:p{page}:g{paragraph}` (deterministic) · `document_id` · `page` (≥1) ·
`paragraph` (≥0, index within page) · `text` · `status`: `ok` | `uncertain`.
A page whose text layer yields fewer than `min_chars_per_page` characters produces a single
`uncertain` span with empty text, so the gap stays visible.

## VersionKey
`rubric_version`, `reference_set_version`, `prompt_version`, `extraction_version`,
`provider_config_id` (e.g. `fake:v1`, `openrouter:<model-id>`). All non-empty strings.

## ReviewRequest
`schema_version`, `job_id`, `assignment_id`, `submission_document_id`, `submission_spans[]`,
`reference_spans[]` (only from teacher-assigned references of the same assignment), `versions: VersionKey`.

## ReviewDraft (adapter output, untrusted)
`provider`, `model`, `issues[]`, `uncertainty`: `low|medium|high`, `suggested_feedback` (str),
`needs_review` (bool). `Issue` = `kind` (`logical_gap|missing_work|unclear|other`),
`description`, `evidence_ids[]` (≥1).

## ReviewResult (immutable, system-built from a validated draft)
`schema_version`, `result_id` (`res_…`, one lineage per document + VersionKey),
`result_version` (int ≥1, increments on rerun), `job_id`, `document_id`, `provider`, `model`,
`issues[]`, `uncertainty`, `suggested_feedback`, `review_state` (`draft` | `needs_review`),
`versions`, `created_at`. `review_state` is `needs_review` when any submission span is
`uncertain`, the draft says `needs_review`, or `uncertainty == high`.

## TeacherDecision
`result_id`, `result_version`, `reviewer`, `decision` (`approved` | `rejected`),
`edited_feedback`, `decided_at`. The effective review state of a version is its latest decision,
else its `review_state`. Export (S5) requires: the version is the latest of its lineage **and**
its latest decision is `approved`; the export contains `edited_feedback` only.

## Processing state machine (jobs)
```
queued ─claim─▶ running ─complete─▶ completed
  │               │  ├─fail (attempts exhausted / non-retryable)─▶ failed
  │               │  ├─lease expired & attempts left─▶ (reclaimable as queued)
  │               │  └─cancel_requested observed between stages─▶ canceled
  └─cancel─▶ canceled
```
Terminal: completed, failed, canceled. `cancel_requested` is a flag on running jobs.
Columns: `attempts`, `max_attempts`, `lease_token`, `lease_expires_at`, `error_code`, `result_id`, `result_version`.
`complete/fail/heartbeat` require the current `lease_token`; otherwise `stale_lease` and the
output is discarded. A late result after cancellation is discarded.

## Review states
`draft`, `needs_review` (system), `approved`, `rejected` (teacher). Never auto-approved.

## Error vocabulary (`ErrorInfo{code, message, retryable}`)
| code | retryable | meaning |
|---|---|---|
| input_rejected | no | bad bytes, size/page limits, unknown role/assignment |
| extraction_uncertain | no | informational; leads to needs_review, not failure |
| timeout | yes | provider timeout (provider may still have run) |
| rate_limited | yes | provider 429 |
| provider_failure | yes | network/5xx |
| invalid_json | yes | unparseable / schema-invalid adapter output |
| unknown_evidence_id | no | draft cited an ID not in the request |
| stale_lease | no | token mismatch; output discarded |
| invalid_transition | no | illegal state change |
| not_found | no | unknown ID |
| budget_exceeded | no | token cap unset or would be exceeded; no request is sent |

## Extraction gate
If a submission has **no** readable span, the worker does not call any model. It records a
`needs_review` result with `provider: system`, `model: extraction-gate` and no issues.

## Provider usage and budget
Every hosted request writes a `provider_usage` row: reported `prompt_tokens`/`completion_tokens`
(NULL when not reported — never 0) and `charged_tokens` counted against `token_cap`. Unknown usage,
timeouts and 5xx are charged at the reserved bound (prompt estimate + `max_tokens`) because the
provider may have run; 429/4xx are charged 0. A request is sent only if `used + estimate ≤ cap`.
The hosted adapter refuses requests whose `prompt_version` differs from its own (`prompt:v1`).

## Deduplication
- **Content:** `(assignment_id, role, sha256)` is unique; identical bytes in the same scope reuse
  the document. Different role or assignment → a new document.
- **Result reuse:** a new batch item reuses an existing completed result only when the document and
  the full VersionKey match. Any version change → new job and new lineage.
- A matching hash alone is never sufficient.

## Limits (PROPOSED — awaiting approval)
files/batch 5 · bytes/file 5 MiB · pages/file 10 · pages/batch 25 · active model requests 1 ·
provider timeout 45 s · max attempts 2 · lease 60 s · heartbeat 15 s · min chars/page 20 ·
token cap: UNSET (blocks live calls).

## Interfaces
Library and CLI in S1–S3: `hwreview batch create <manifest>`, `hwreview worker run [--once]`,
`hwreview job show <id>`, `hwreview job cancel <id>`, `hwreview result show <id>`.
HTTP (S5, implemented in `api.py`, local-only, CSRF header `X-CSRF-Token` on writes):
`GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`, `GET /api/results/{id}/{version}`,
`POST /api/results/{id}/{version}/decision` (`{decision, edited_feedback}`),
`GET /api/results/{id}/{version}/export`. No upload endpoint: batches come only from the
allowlisted demo manifest (`POST /demo/batch`). Approving requires non-empty feedback.
Review screen: `/`, `/assignments/{id}`, `/review/{result_id}/{version}`, `/documents/{id}/pdf`.
