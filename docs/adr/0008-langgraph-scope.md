# ADR 0008: LangGraph for JD intake and match-run orchestration only

- **Status:** Accepted (scope-limited; confirm with a Phase 4 spike)
- **Date:** 2026-11-05
- **Supersedes:** decision D-009 ("LangGraph deferred")

## Context
Phase 1 deferred LangGraph. The matching pipeline is a fixed sequence of steps, and avoiding a new dependency and its learning curve looked cheaper. Your answers changed that trade-off:
- You are a solo developer who already knows **LangGraph/LangChain**, so the learning cost is near zero.
- Two workflows genuinely benefit from what LangGraph provides:
  1. **JD intake.** Requirement extraction → **pause for the recruiter to confirm or edit the requirement atoms** → resume. This is a human-in-the-loop interrupt that may last hours.
  2. **Match run.** Staged funnel with conditional branches (escalate or not, lazy deep evaluation), partial results streamed to the UI, and resumable after a failure.

## Decision
Use LangGraph **only** for those two graphs. Rules:
- **Checkpointing:** a Postgres checkpointer. No new store (consistent with ADR 0002).
- **Deterministic edges.** Routing between nodes is plain code that reads typed state. **No LLM-driven routing, no open-ended agent loops.** This keeps token cost bounded and predictable.
- **Model calls go through our Model Gateway, never through LangChain model wrappers.** Routing, budgets, PII redaction and cost metering stay in one place (ADR 0003, D-018).
- **Nodes are thin.** They wrap the same node-shaped pure functions planned earlier, so the logic stays unit-testable without LangGraph.
- **Ingestion stays on plain queue tasks** (Procrastinate, D-010). It is bulk and stateless per document, and gains nothing from a graph runtime.

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| Keep LangGraph deferred (D-009) | The main reason was learning cost, which no longer applies, and the human-in-the-loop resume would have to be hand-built |
| LangGraph everywhere, including ingestion and agents | Adds runtime overhead to bulk work, and agent loops make token cost unpredictable |
| Durable workflow engine (Temporal) | Heavier to operate for a solo developer; revisit only at large scale |

## Consequences
- Human-in-the-loop resume and partial-result streaming come built in.
- We depend on LangGraph's release cadence. Pin versions and keep nodes thin so the graphs could be replaced.
- The cost meter must tag every gateway call with the graph run and node IDs so cost per stage stays visible.

## Revisit when
- The Phase 4 spike shows the checkpointer or streaming does not fit our API design, or
- Phase 8 adds a conversational recruiter assistant. That would be a new, separately budgeted graph.
