# ADR 0005: MCP only for exposing the platform to AI assistants (Phase 8)

- **Status:** Accepted
- **Date:** 2026-11-05
- **Phase:** 1

## Context
The brief asks where MCP (Model Context Protocol) genuinely helps. MCP exposes tools and context **to LLM clients**. The current spec revision (2026-07-28) builds authorization on OAuth 2.1, with MCP servers acting as resource servers.

Candidate ingestion, parsing and matching are bulk, deterministic data movements with no LLM in the decision loop.

## Decision
- **No MCP in these paths:**
  - ingestion and connectors
  - parsing and extraction
  - the matching engine
  - calls to model providers

  These use REST/webhooks, internal interfaces and the job queue.
- **Phase 8, outbound MCP server:**
  - **Purpose:** a remote MCP server that lets recruiters use the engine from the AI assistants they already use.
  - **Tools:** `search_candidates`, `get_match_report`, `list_jobs`, `explain_ranking`, `submit_feedback`. Write tools (e.g. `create_job`) require confirmation.
  - **Auth and tenancy:** OAuth 2.1; the tenant comes from the token, never from tool parameters. Per-tool scopes and rate limits.
  - **Data protection:** no bulk-export tool; PII-minimized outputs; CV text delimited as untrusted content.
  - **Audit:** every call is logged.
- **Phase 8+, MCP client:** the in-app recruiter assistant may consume MCP servers that customers provide (calendar, email, HRIS).

## Alternatives considered
| Alternative | Why rejected |
|---|---|
| MCP servers as the ingestion layer for each recruitment source | Adds an LLM-oriented protocol to bulk ETL. Slower, harder to audit, and no benefit over REST/webhooks |
| MCP between internal modules | Unnecessary network hops and serialization inside a monolith |
| No MCP at all | Gives up a cheap distribution channel into AI assistants |

## Consequences
- The MVP has no MCP dependency.
- The internal API must be designed with clean, permission-scoped read operations, so the Phase 8 MCP server is a thin adapter.

## Revisit when
- Major ATS vendors ship official MCP servers that offer data access better than their REST APIs, or
- customers ask for agent-driven workflows earlier than planned.
