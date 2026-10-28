# Product — homework-review component

## Mission vs. component
The nonprofit provides free mathematics Olympiad training to high-school students. Teachers
spend much of their time writing feedback on written solutions. This component is **only** a
teacher-assist step: it drafts traceable feedback from PDFs; a teacher decides what students see.

## Input roles
| Role | Trust | Source |
|---|---|---|
| `submission` | untrusted data | synthetic student solution PDFs (demo) |
| `reference` | trusted only because a teacher assigned it | separate teacher-approved folder, optional |

A submission can never promote itself to a reference. Roles are assigned by the server from the
batch manifest, never inferred from document content.

## Teacher workflow
1. Teacher (or operator) creates a batch from an approved manifest: assignment, rubric version,
   optional reference documents, submission files.
2. Each file is validated. Invalid files are rejected with a typed reason; others get a job.
3. One worker extracts page/paragraph evidence and asks a model adapter for draft feedback.
4. Draft results cite evidence span IDs. Uncertain extraction is marked `needs_review`.
5. Teacher reads the draft next to the cited evidence, edits feedback, approves or rejects.
6. Export emits only the teacher-approved text of the exact approved result version.

## Minimum demo
Five fixed synthetic fixtures (see `fixtures/manifest.json`):
F1 correct alternative proof · F2 real logical gap · F3 missing work · F4 ambiguous scan
(no text layer) · F5 invalid PDF bytes. F1–F3 produce reviewable drafts, F4 needs review,
F5 is rejected before any model use. These are diagnostic cases, not an accuracy benchmark.

## Non-goals
Accounts/LMS/course management, automatic grades, proof verification, OCR or handwriting
recognition, retrieval systems, autonomous tutoring or messaging, anonymous public uploads,
agent frameworks inside the pipeline, local model weights/GPU, n8n/voice/sponsor integrations.
External agents (e.g. Hermes, n8n) may only call the HTTP/CLI contract.

## Open assumptions and blockers
- ASSUMED: demo PDFs are synthetic student submissions (confirmed by user 2026-10-28).
- BLOCKER (S4): `OPENROUTER_API_KEY`, current `:free` model IDs and rate limits, token cap.
- BLOCKER: a teacher must review fixture mathematical expectations
  (`math_expectations_reviewed: false` in the manifest).
- UNKNOWN: organizer rules on pre-event code, exact submission cutoff, team members, license,
  `.tech` domain ownership, DigitalOcean budget.
- Free hosted endpoints may log or train on prompts — acceptable only for synthetic data.
