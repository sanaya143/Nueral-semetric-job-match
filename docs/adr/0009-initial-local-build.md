# ADR 0009: Initial build runs locally on SQLite and local files, without Docker

- **Status:** Accepted (initial build only; revisit at production readiness)
- **Date:** 2026-11-09
- **Relates to:** ADR 0002 (Postgres + pgvector + BM25), ADR 0008 (LangGraph scope), D-010 (Procrastinate queue)

## Context
You asked for a first working build, before production readiness, **without Docker and on local storage**. You are a solo developer on Windows with no GPU and a $50/month development budget (D-019). No design-partner data is available yet, so the build also needs a synthetic dataset to exercise and evaluate the pipeline.

The production design (ADR 0002) depends on PostgreSQL with pgvector, `pg_search` and row-level security. Running that natively on Windows without Docker is possible but slow to set up, and it would delay the part that carries the real risk: whether evidence-based matching beats keyword matching.

## Decision
The initial build uses only what runs in-process on the developer's machine:

| Concern | Production design | Initial build |
|---|---|---|
| Relational store | PostgreSQL + RLS | **SQLite** (WAL mode, foreign keys, one file in `data/`) |
| Lexical search | BM25 via `pg_search` | **SQLite FTS5** (bm25) |
| Vector search | pgvector HNSW | **numpy** brute-force cosine over unit vectors, cached per tenant and model. Fine at thousands of evidence units |
| Raw files | Encrypted object storage | **`data/uploads/<tenant>/`** on local disk |
| Background work | Procrastinate queue (D-010) | **FastAPI `BackgroundTasks`** in-process |
| Tenancy | RLS policies | Every query filtered through a `scoped()` helper; tenant comes from the `X-Tenant-ID` header |
| Auth | OIDC | None; API binds to 127.0.0.1 |
| Embeddings (offline) | API or self-hosted model | **Local concept-hash embedding** (512-d, taxonomy-aware). OpenRouter `baai/bge-m3` is optional |
| LLM | OpenRouter via the Model Gateway | Same gateway; **offline mode is the default** (deterministic extraction and verdicts, $0) |
| JD intake human-in-the-loop | LangGraph interrupt with a Postgres checkpointer (ADR 0008) | **Draft → confirmed state in the database**, version-bumped on edit. The match run is a LangGraph `StateGraph` with deterministic edges and no checkpointer |

Interfaces were kept narrow so each swap is local:
- `vector_index.search(...)`, the FTS query in `retrieval/hybrid.py` and `db.scoped()` are the only places that know the store.
- The Model Gateway is the only path to any model, in both modes.

## Alternatives considered
| Alternative | Why rejected for now |
|---|---|
| Native PostgreSQL + pgvector on Windows | Extension builds and service setup add days. No benefit at this scale before the matching claim is validated |
| Docker Compose (the original plan) | Explicitly ruled out for this build |
| Chroma / LanceDB for vectors | Another dependency and store to keep consistent with SQLite; numpy is enough for thousands of vectors |
| LangGraph interrupt for JD intake now | Needs a durable checkpointer. DB state gives the same human-in-the-loop behaviour (nothing ranks until you confirm), and it is simpler to inspect |

## Consequences
- The app runs with three commands and costs $0 in offline mode. The whole pipeline, including the dashboard, can be demoed and evaluated today.
- **Not production-safe:** no auth, no RLS, no encryption at rest, single process. That is acceptable only on a local machine with synthetic or consented data.
- Brute-force vector search is O(N) per query. It is fine to roughly 50K evidence units; beyond that, move to pgvector (ADR 0002).
- In-process background tasks are lost if the server stops mid-ingestion. Those candidates stay in `processing` and must be re-uploaded. A queue fixes this in production.

## Exit criteria (when to replace this)
Move to the ADR 0002 stack when **any** of these is true:
1. A design partner's real data is about to be loaded.
2. More than one user needs access.
3. The corpus passes about 50K evidence units, or p95 match-run latency passes about 5 s.
