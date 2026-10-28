# Architecture decision records

## ADR-1 Greenfield Python stack
Context: no existing repository. Decision: Python 3.14, Pydantic v2, pypdf, SQLite (stdlib),
pytest; FastAPI only from S5. Alternatives: reuse an existing LMS (none available), Node/TS
(no advantage for PDF tooling here). Tradeoff: Python 3.14 is new; wheel availability is a risk.

## ADR-2 One worker, SQLite as the queue
Decision: single worker process; SQLite (WAL) with lease tokens is authoritative.
Alternatives: Redis/Celery (extra service), in-memory queue (not durable). Tradeoff: no
horizontal scale — explicitly out of demo scope.

## ADR-3 Hosted provider: OpenRouter free models via an OpenAI-compatible adapter
Decision (user, 2026-10-28): one adapter for the OpenAI-compatible chat API, configured by base
URL + model ID, first targeting OpenRouter `:free` models. Alternatives: Gemma via Google AI
Studio, Groq, Hugging Face (same adapter, config change); Claude API (paid). Tradeoffs: free tiers
are rate-limited and may log prompts (synthetic data only); smaller models produce invalid JSON
more often, which local validation turns into typed errors or needs_review. Model IDs and limits
must be confirmed from current docs before S4.

## ADR-4 No agent framework in the pipeline
Decision: the pipeline makes one tool-free model call per document. Agent harnesses (Hermes,
n8n, Claude Code) may call the HTTP/CLI contract as clients. Reason: tools and autonomy conflict
with "document content is data" and local validation.

## ADR-5 Local text extraction only
Decision: pypdf text layer; low-text pages are uncertain → needs_review. Alternatives: Docling,
OCR (heavier, out of scope). Tradeoff: scans are never auto-reviewed.

## ADR-6 Hosting (plan only)
Proposed: one DigitalOcean CPU Droplet, Docker Compose (web, worker, TLS reverse proxy), named
volume for SQLite + blobs. Not App Platform (non-persistent local storage). Nothing is deployed
without separate approval; domain, credits and budget are unknown.
