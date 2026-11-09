# ADR 0001: Modular monolith (FastAPI) plus background workers

- **Status:** Accepted
- **Date:** 2026-11-05
- **Phase:** 1 (Strategic analysis)

## Context
The platform needs these capabilities:
- ingestion
- parsing and extraction
- job intake
- matching
- search
- feedback
- admin/cost
- audit

The MVP will be built by a small team (1–2 developers is the working assumption). At MVP scale (≤100K candidates) the main load comes from per-job LLM calls and per-CV extraction. Neither needs services that scale independently.

## Decision
Build **one Python/FastAPI application** made of internal modules with explicit boundaries:
- `auth/tenancy`
- `candidates`
- `jobs`
- `matching`
- `search`
- `feedback`
- `admin`
- `audit`
- `model_gateway`
- `connectors`

The same codebase also runs **background workers** for ingestion, extraction, embedding and match runs. Modules talk to each other through Python interfaces, not over the network. Each module owns its own database tables.

## Alternatives considered
| Alternative | Why rejected (for now) |
|---|---|
| Microservices from day one | Adds network failure modes, distributed tracing, deployment overhead and data-consistency problems, with no matching scale benefit for an MVP |
| Serverless functions per pipeline step | Cold starts, hard local development, awkward long-running LLM batches |

## Consequences
- One deployable unit and one database, so local development stays simple (Docker Compose).
- Module boundaries must be enforced by convention and import rules (e.g. import-linter), or they will erode.
- Extracting a service later is straightforward if a module (e.g. embedding/rerank inference) needs separate scaling or GPUs.

## Revisit when
- A module needs independent scaling or hardware (GPU inference is the most likely candidate), or
- several teams need to deploy independently.
