# ADR 0002: PostgreSQL + pgvector + BM25 as the single store for the MVP

- **Status:** Accepted (MVP)
- **Date:** 2026-11-05
- **Phase:** 1

## Context
Matching needs several things from storage:
- Relational data: candidates, jobs, evidence units, capability claims, taxonomy, audit, cost meter.
- Dense vector search over evidence units.
- Lexical (BM25) search for exact technology names such as "pgvector" or "LangGraph".
- Metadata filters.
- Strict tenant isolation.
- A verifiable hard delete, for DPDP/GDPR erasure.

## Decision
Use **PostgreSQL** as the system of record:
- **Dense search:** the **pgvector** extension, with HNSW indexes and `halfvec` storage. Use iterative index scans for filtered queries.
- **BM25:** the **ParadeDB `pg_search`** extension (Tantivy-based). Fall back to native Postgres full-text search if the managed provider doesn't offer it.
- **Hybrid fusion:** Reciprocal Rank Fusion (RRF) in SQL.
- **Tenant isolation:** Row-Level Security on every tenant-scoped table, vectors included.
- **Swap path:** access vectors through a `VectorIndex` interface so the backend can change later.

## Alternatives considered
| Alternative | Why rejected (for now) |
|---|---|
| Qdrant (dedicated vector DB) | Strong filtered-search and native hybrid support, but it adds a second stateful system. Deletes and tenant isolation must then stay consistent across two stores. Not needed below roughly 10–50M vectors |
| Elasticsearch / OpenSearch | Excellent hybrid search, but heavy to operate and duplicates relational data |
| Weaviate | No clear advantage over Qdrant for our needs |
| Graph DB + vector DB | See ADR 0007 |

## Consequences
- Erasure is a single transactional cascade.
- RLS gives one isolation mechanism for structured data and vectors alike.
- Hybrid search fusion must be written in SQL. It isn't built in the way it is in Qdrant.
- At 1M candidates (~15M evidence vectors) the index needs ~30 GB+ in RAM. Matryoshka truncation or quantization will be needed, or a move to pgvectorscale DiskANN or Qdrant.

## Revisit when
- Evidence vectors exceed roughly 10–50M, or
- filtered p95 retrieval latency breaks the SLO set in Phase 3, or
- the chosen managed Postgres lacks `pg_search` and native full-text search proves insufficient in the Phase 2 bake-off.
