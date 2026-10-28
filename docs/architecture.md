# Architecture

## Flow
```
CLI / (S5) HTTP+HTML ─▶ intake ─▶ SQLite (documents, jobs, results, decisions)
                                     │
                         single worker: claim (lease) ─▶ extraction adapter ─▶ evidence spans
                                     │                          │ uncertain → needs_review
                                     ▼
                           model adapter.review(request) ─▶ local validation ─▶ immutable result version
                                     │
                         teacher decision (per version) ─▶ export approved text only
```

## Components (`src/hwreview/`)
| Module | Responsibility |
|---|---|
| `contracts.py` | Pydantic schemas (versioned), enums. No provider/agent imports. |
| `errors.py` | Typed error vocabulary (`HWError` + codes). |
| `config.py` | Limits (PROPOSED) as one frozen dataclass. |
| `storage.py` | Blob store under `data/blobs/`, server-generated keys, traversal-safe. |
| `intake.py` | Byte/size/page validation, sha256, role/assignment checks, per-item outcomes. |
| `extraction.py` | `Extractor` protocol, `PypdfExtractor` (text layer → paragraphs). |
| `db.py` | SQLite schema, WAL, connection helper. |
| `jobs.py` | Durable queue: enqueue, claim, heartbeat, complete, fail, cancel. |
| `worker.py` | Stage pipeline with cancel checks between stages. |
| `adapters/` | `ModelAdapter` protocol, deterministic fake; (S4) OpenAI-compatible adapter for OpenRouter. |
| `validate.py` | Validates adapter output against the request (evidence IDs, schema). |
| `cli.py` | Thin CLI over the same contract. |

## Trust boundaries
1. **Files → intake:** untrusted bytes. Validated before storage; client paths never used as keys.
2. **Evidence → model:** document text is data inside a fixed prompt; no tools enabled; reference
   trust comes from the manifest role, not content.
3. **Model → result:** untrusted output; parsed and validated locally; unknown evidence IDs or
   malformed JSON never become a valid result.
4. **Result → student:** only teacher-approved text of an approved version is exportable.

## Persistence
Single host, single SQLite file (WAL) plus a blob directory, both under `data/` (a named Docker
volume in the deployment plan). SQLite is the authoritative queue. Suitable for this demo only;
no distributed-scale claims. A volume on the same host is not a backup.

## Queue semantics
Jobs are committed before IDs are returned. The worker claims atomically with a unique lease token
and expiry, commits, then does slow work outside any transaction. Heartbeats extend the lease.
Expired leases are reclaimable within the attempt budget; otherwise the job fails visibly.
Results are accepted only with the current token and no cancellation. Attempts are at-least-once;
a provider timeout does not prove the provider did nothing (possible duplicate cost).

## Extraction fallback
Local text-layer extraction via pypdf. Pages with too little text are `uncertain`, and the
document's result goes to `needs_review`. No OCR. Docling is an optional later adapter.

## Model adapters
`review(ReviewRequest) -> ReviewResult` or a typed error. The fake is deterministic and offline.
The hosted adapter (S4) speaks the OpenAI-compatible chat API, configured by base URL + model ID
(target: OpenRouter `:free` models), so switching hosts is configuration, not code.
