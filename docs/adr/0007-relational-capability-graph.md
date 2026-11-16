# ADR 0007: Relational capability graph in Postgres instead of a graph database

- **Status:** Accepted (MVP)
- **Date:** 2026-11-05
- **Phase:** 1

## Context
The system has to connect work experience to the competencies it demonstrates. For example: a candidate built a document Q&A assistant with pgvector, which shows LLM application development, RAG engineering and vector search. It also has to relate skills to one another (broader, narrower, related, implies).

The brief asks whether a knowledge graph is justified.

## Decision
Model the graph **relationally in PostgreSQL**:
- `candidate → evidence_unit → capability_claim → skill`
- `capability_claim` carries the evidence level, confidence and extraction version
- `skill_relation(skill_a, skill_b, type)` with type ∈ {broader, narrower, related, implies}
- `job_requirement → skill` mappings for matching

Two-to-three-hop traversals use recursive CTEs.

The taxonomy is seeded from **ESCO** (free, multilingual) and **O*NET**, plus a curated tech/AI layer. Unknown terms are resolved by embedding nearest neighbour; anything still unresolved goes to a review queue.

## Alternatives considered
| Alternative | Why rejected (for now) |
|---|---|
| Neo4j or another graph DB | A second store to keep consistent with Postgres for RLS, deletion and backups, for traversals that SQL handles at MVP depth |
| Single embedding per CV, no graph | Loses the evidence → capability link that explanations depend on |
| LLM-only capability inference at query time | Expensive, slow, and not reproducible |

## Consequences
- Erasure, tenant isolation and transactions stay inside one database.
- Deep multi-hop analytics (career-path mining, workforce intelligence) will be awkward in SQL.
- Taxonomy curation becomes an ongoing product responsibility.

## Revisit when
- Career-path or workforce-intelligence features become core (Phase 8+), or
- traversal queries routinely need more than three hops.
